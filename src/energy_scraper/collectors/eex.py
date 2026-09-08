from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from energy_scraper.core.archive import archive_payload
from energy_scraper.core.database import Database, utcnow
from energy_scraper.core.error_classifier import classify_error
from .base import ApiCollector


@dataclass(frozen=True)
class EexProduct:
    dataset_id: str
    short_code: str
    commodity: str
    area: str
    product: str
    profile: str
    maturity_type: str = "Year"


PRODUCTS = {
    "F7BY": EexProduct("eex_fr_base_year", "F7BY", "POWER", "FR", "Base", "BASE"),
    "F7PY": EexProduct("eex_fr_peak_year", "F7PY", "POWER", "FR", "Peak", "PEAK"),
    "G3BY": EexProduct("eex_ttf_year", "G3BY", "NATGAS", "TTF", "Physical", "BASE"),
    "F7BM": EexProduct("eex_fr_base_month", "F7BM", "POWER", "FR", "Base", "BASE", "Month"),
    "F7BQ": EexProduct("eex_fr_base_quarter", "F7BQ", "POWER", "FR", "Base", "BASE", "Quarter"),
    "F7PM": EexProduct("eex_fr_peak_month", "F7PM", "POWER", "FR", "Peak", "PEAK", "Month"),
    "F7PQ": EexProduct("eex_fr_peak_quarter", "F7PQ", "POWER", "FR", "Peak", "PEAK", "Quarter"),
}

FIELD_MAP = {
    "totVolTrdd": "traded_volume_mwh", "grossOpenInt": "gross_open_interest_contracts",
    "grossOpenIntSz": "gross_open_interest_mwh", "netOpenInt": "net_open_interest_contracts",
    "netOpenIntSz": "net_open_interest_mwh", "settlPx": "settlement",
}
EXPECTED_HEADER = {"shortCode", "maturityDate", "tradeDate", "settlPx"}


def maturity_code(year: int) -> str:
    return f"{year}01"


def delivery_bounds(year: int) -> tuple[str, str]:
    return f"{year}-01-01", f"{year}-12-31"


def add_months(day: date, count: int) -> date:
    absolute = day.year * 12 + day.month - 1 + count
    return date(absolute // 12, absolute % 12 + 1, 1)


def default_maturities(reference: date, maturity_type: str) -> list[str]:
    if maturity_type == "Month":
        return [add_months(reference.replace(day=1), offset).strftime("%Y%m") for offset in range(1, 4)]
    if maturity_type == "Quarter":
        quarter_start = date(reference.year, ((reference.month - 1) // 3) * 3 + 1, 1)
        return [add_months(quarter_start, 3 * offset).strftime("%Y%m") for offset in range(1, 5)]
    return [maturity_code(year) for year in range(reference.year + 1, reference.year + 4)]


def normalize_maturity(raw: str, maturity_type: str) -> str:
    compact = raw.replace("-", "")[:6]
    year, month = int(compact[:4]), int(compact[4:6])
    if maturity_type == "Month":
        return f"MON-{year:04d}-{month:02d}"
    if maturity_type == "Quarter":
        if month not in {1, 4, 7, 10}:
            raise ValueError(f"invalid EEX quarter maturity month: {month}")
        return f"Q{(month - 1) // 3 + 1}-{year:04d}"
    return f"CAL-{year:04d}"


def maturity_bounds(maturity: str, maturity_type: str) -> tuple[str, str]:
    compact = maturity.replace("-", "")[:6]; year, month = int(compact[:4]), int(compact[4:6])
    months = 1 if maturity_type == "Month" else 3 if maturity_type == "Quarter" else 12
    start = date(year, month, 1); after = add_months(start, months)
    return start.isoformat(), (after - timedelta(days=1)).isoformat()


def parse_table_payload(payload: dict[str, Any], product: EexProduct, requested_start: date,
                        requested_end: date, requested_year: int | None = None,
                        requested_maturity: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    header = payload.get("header")
    data = payload.get("data")
    if not isinstance(header, list) or not isinstance(data, list):
        raise ValueError("EEX schema error: header/data missing")
    missing = EXPECTED_HEADER - set(header)
    if missing:
        raise ValueError(f"EEX schema error: missing {sorted(missing)}")
    output: list[dict[str, Any]] = []
    for values in data:
        if len(values) != len(header):
            raise ValueError("EEX schema error: row/header length mismatch")
        row = dict(zip(header, values, strict=True))
        if row["shortCode"] != product.short_code:
            raise ValueError(f"Unexpected shortCode {row['shortCode']}")
        # Unavailable maturities silently fall back to the rolling contract.
        expected = requested_maturity or (maturity_code(requested_year) if requested_year is not None else None)
        if expected is not None and str(row["maturityDate"]).replace("-", "")[:6] != expected.replace("-", "")[:6]:
            continue
        trade_date = date.fromisoformat(str(row["tradeDate"])[:10])
        if not requested_start <= trade_date <= requested_end:
            raise ValueError(f"tradeDate outside requested range: {trade_date}")
        normalized = {"short_code": row["shortCode"], "maturity_date": str(row["maturityDate"])[:10], "trade_date": trade_date.isoformat(),
                      "currency": payload.get("currency"), "unit": payload.get("uOM")}
        normalized.update({target: row.get(source) for source, target in FIELD_MAP.items()})
        output.append(normalized)
    dates = [row["trade_date"] for row in output]
    if len(dates) != len(set(dates)):
        raise ValueError("EEX dates are duplicated")
    if dates not in (sorted(dates), sorted(dates, reverse=True)):
        raise ValueError("EEX dates are not monotonic")
    output.sort(key=lambda row: row["trade_date"])
    return output, list(header)


class EexCollector(ApiCollector):
    def __init__(self, settings, db: Database):
        super().__init__(settings)
        self.db = db
        self.base_url = settings.sources()["eex"]["base_url"].rstrip("/")

    def collect(self, start: date, end: date, years: list[int] | None = None,
                codes: list[str] | None = None, force: bool = False,
                maturities: list[str] | None = None, reference: date | None = None) -> dict[str, int]:
        years = years or list(range(max(start.year + 1, date.today().year + 1), date.today().year + 4))
        codes = codes or list(PRODUCTS)
        totals = {"partitions": 0, "received": 0, "inserted": 0, "errors": 0}
        for code in codes:
            if code not in PRODUCTS:
                raise ValueError(f"Unsupported EEX code: {code}")
            product = PRODUCTS[code]
            requested = (maturities or default_maturities(reference or end, product.maturity_type)) if product.maturity_type != "Year" else [maturity_code(year) for year in years]
            for maturity in requested:
                part = f"{code}:{maturity}:{start}:{end}"
                if not force and self._complete(PRODUCTS[code].dataset_id, part):
                    continue
                totals["partitions"] += 1
                try:
                    received, inserted = self._collect_partition(product, maturity, start, end, part)
                    totals["received"] += received
                    totals["inserted"] += inserted
                except Exception as exc:
                    totals["errors"] += 1
                    self._failed_job(PRODUCTS[code].dataset_id, part, start, end, exc)
        return totals

    def _complete(self, dataset: str, partition: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT status FROM collection_partitions WHERE dataset_id=? AND partition_key=?", (dataset, partition)).fetchone()
            return bool(row and row[0] in {"SUCCESS", "SUCCESS_EMPTY"})

    def _collect_partition(self, product: EexProduct, maturity: str, start: date, end: date, partition: str) -> tuple[int, int]:
        url = f"{self.base_url}/table-data"
        params = {"shortCode": product.short_code, "commodity": product.commodity, "pricing": "F", "area": product.area,
                  "product": product.product, "maturity": maturity, "startDate": start.isoformat(), "endDate": end.isoformat(),
                  "maturityType": product.maturity_type, "isRolling": "true"}
        run_id = self.db.start_run("eex", url)
        try:
            payload, request_url = self.get_json(url, params, self._headers())
            archive_path, digest = archive_payload(self.settings.raw_dir, "eex", payload, {"request_url": request_url, "parameters": params})
            rows, header = parse_table_payload(payload, product, start, end, requested_maturity=maturity)
            label = normalize_maturity(maturity, product.maturity_type)
            instrument_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"EEX:{product.short_code}:{label}"))
            collected = utcnow()
            delivery_start, delivery_end = maturity_bounds(maturity, product.maturity_type)
            with self.db.transaction() as conn:
                conn.execute("""INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (instrument_id, "EEX", product.commodity, product.area, product.product, product.short_code,
                     product.profile, label, delivery_start, delivery_end, payload.get("currency"), payload.get("uOM")))
                values = [(instrument_id, collected, r["trade_date"], "settlement", r["settlement"], r["settlement"],
                           r["traded_volume_mwh"], r["gross_open_interest_contracts"], r["traded_volume_mwh"],
                           r["gross_open_interest_contracts"], r["gross_open_interest_mwh"], r["net_open_interest_contracts"],
                           r["net_open_interest_mwh"], "eex", run_id) for r in rows]
                inserted = self.db.insert_many(conn, """INSERT OR IGNORE INTO market_prices
                    (instrument_id,observation_timestamp,trading_date,price_type,price,settlement,volume,open_interest,
                     traded_volume_mwh,gross_open_interest_contracts,gross_open_interest_mwh,net_open_interest_contracts,
                     net_open_interest_mwh,source_id,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                status = "SUCCESS" if rows else "SUCCESS_EMPTY"
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)", (product.dataset_id, partition, status, collected, len(rows), run_id))
                conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,
                  chunk_start,chunk_end,attempt_count,status,first_attempt_at,last_attempt_at,records_received,next_action)
                  VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?) ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET
                  attempt_count=backfill_jobs.attempt_count+1,status=excluded.status,last_attempt_at=excluded.last_attempt_at,
                  records_received=excluded.records_received,error_class=NULL,http_status=NULL,error_message=NULL,next_action=excluded.next_action""",
                  (str(uuid.uuid4()), product.dataset_id, "eex", partition, str(start), str(end), str(start), str(end), status, collected, collected, len(rows), "none"))
            self.db.finish_run(run_id, status, len(rows), inserted, payload_hash=digest)
            return len(rows), inserted
        except Exception as exc:
            self.db.finish_run(run_id, "FAILED", error=str(exc))
            raise

    def collect_ticker(self, code: str, year: int) -> int:
        product = PRODUCTS[code]
        url = f"{self.base_url}/price-ticker"
        params = {"shortCode": code, "maturity": maturity_code(year)}
        run_id = self.db.start_run("eex", url)
        try:
            payload, request_url = self.get_json(url, params, self._headers())
            _, digest = archive_payload(self.settings.raw_dir, "eex", payload, {"request_url": request_url, "parameters": params, "kind": "ticker"})
            instrument_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"EEX:{code}:{year}"))
            delivery_start, delivery_end = delivery_bounds(year)
            with self.db.transaction() as conn:
                conn.execute("INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (instrument_id,"EEX",product.commodity,product.area,product.product,code,product.profile,f"CAL-{year}",delivery_start,delivery_end,payload.get("currency"),None))
                conn.execute("""INSERT OR IGNORE INTO ticker_snapshots(instrument_id,source_update_time,collected_at,settlement,currency,
                 diff_settlement,no_previous_value_found,raw_payload_hash,source_id,run_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                 (instrument_id,payload.get("lastUpdatedAt"),utcnow(),payload.get("settlPx"),payload.get("currency"),payload.get("diffSettlPx"),payload.get("noPreviousValueFound"),digest,"eex",run_id))
                inserted = conn.total_changes
            self.db.finish_run(run_id,"SUCCESS",1,inserted,payload_hash=digest)
            return inserted
        except Exception as exc:
            self.db.finish_run(run_id,"FAILED",error=str(exc)); raise

    def _failed_job(self, dataset: str, partition: str, start: date, end: date, exc: Exception) -> None:
        kind, status = classify_error(exc); now = utcnow()
        final = "RETRY_EXHAUSTED" if kind == "RETRYABLE_REMOTE_ERROR" else kind
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,
              chunk_start,chunk_end,attempt_count,status,first_attempt_at,last_attempt_at,error_class,http_status,error_message,next_action)
              VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?) ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET
              attempt_count=backfill_jobs.attempt_count+1,status=excluded.status,last_attempt_at=excluded.last_attempt_at,
              error_class=excluded.error_class,http_status=excluded.http_status,error_message=excluded.error_message,next_action=excluded.next_action""",
              (str(uuid.uuid4()),dataset,"eex",partition,str(start),str(end),str(start),str(end),final,now,now,kind,status,str(exc)[:2000],"repair-gaps"))

    def _headers(self) -> dict[str, str]:
        return {"accept":"application/json, text/javascript, */*; q=0.01","origin":"https://www.eex.com","referer":"https://www.eex.com/","user-agent":self.settings.user_agent}
