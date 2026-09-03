from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from typing import Any, Iterator

from energy_scraper.core.archive import archive_payload
from energy_scraper.core.database import Database, utcnow
from energy_scraper.core.error_classifier import classify_error
from .base import ApiCollector


def dates_between(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def contract_resolution(name: str | None) -> tuple[str | None, int | None]:
    value = (name or "").upper()
    for kind, minutes in (("QH", 15), ("HH", 30), ("PH", 60)):
        if re.search(rf"(?:^|[^A-Z]){kind}(?:[^A-Z]|$)", value) or value.startswith(kind):
            return kind, minutes
    return None, None


def parse_payload(payload: dict[str, Any], requested_area: str) -> list[dict[str, Any]]:
    contracts = payload.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("Nord Pool schema error: contracts missing")
    area = payload.get("deliveryArea") or requested_area
    if area != requested_area:
        raise ValueError(f"Unexpected delivery area: {area}")
    output = []
    for row in contracts:
        if not isinstance(row, dict) or row.get("contractId") is None:
            raise ValueError("Nord Pool schema error: invalid contract")
        kind, minutes = contract_resolution(row.get("contractName"))
        output.append({
            "delivery_area": area, "delivery_date": str(payload.get("deliveryDateCET", ""))[:10],
            "delivery_start": row.get("deliveryStart"), "delivery_end": row.get("deliveryEnd"),
            "contract_id": str(row["contractId"]), "contract_name": row.get("contractName"),
            "contract_type": kind, "resolution_minutes": minutes, "is_local_contract": row.get("isLocalContract"),
            "contract_open_time": row.get("contractOpenTime"), "contract_close_time": row.get("contractCloseTime"),
            "open": row.get("openPrice"), "high": row.get("highPrice"), "low": row.get("lowPrice"), "close": row.get("closePrice"),
            "vwap": row.get("averagePrice"), "vwap_1h": row.get("averagePriceLast1H"), "vwap_3h": row.get("averagePriceLast3H"),
            "volume": row.get("volume"), "buy_volume": row.get("buyVolume"), "sell_volume": row.get("sellVolume"),
            "first_trade_time": row.get("openTradeTime"), "last_trade_time": row.get("closeTradeTime"),
            "source_update_time": payload.get("updateTime"), "price_unit": payload.get("priceUnit"), "volume_unit": payload.get("volumeUnit"),
        })
    if output and not payload.get("updateTime"):
        raise ValueError("Nord Pool schema error: updateTime missing")
    return output


class NordPoolCollector(ApiCollector):
    def __init__(self, settings, db: Database):
        super().__init__(settings); self.db = db
        self.url = settings.sources()["nordpool"]["base_url"]

    def collect(self, start: date, end: date, areas: list[str], force: bool = False, mode: str = "final") -> dict[str, int]:
        totals = {"partitions": 0, "received": 0, "inserted": 0, "empty": 0, "errors": 0}
        for day in dates_between(start, end):
            for area in areas:
                partition = f"{area}:{day}"
                if not force and mode == "final" and self._complete(partition):
                    continue
                totals["partitions"] += 1
                try:
                    received, inserted = self._collect_one(day, area, partition)
                    totals["received"] += received; totals["inserted"] += inserted
                    totals["empty"] += int(received == 0)
                except Exception as exc:
                    totals["errors"] += 1; self._failed(partition, day, area, exc)
        return totals

    def _complete(self, partition: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT status FROM collection_partitions WHERE dataset_id='nordpool_intraday' AND partition_key=?", (partition,)).fetchone()
            return bool(row and row[0] in {"SUCCESS", "SUCCESS_EMPTY"})

    def _collect_one(self, day: date, area: str, partition: str) -> tuple[int, int]:
        params = {"date": day.isoformat(), "deliveryArea": area}
        run_id = self.db.start_run("nordpool", self.url)
        try:
            payload, request_url = self.get_json(self.url, params, self._headers())
            _, digest = archive_payload(self.settings.raw_dir, "nordpool", payload, {"request_url": request_url, "parameters": params})
            rows = parse_payload(payload, area); collected = utcnow(); status = "SUCCESS" if rows else "SUCCESS_EMPTY"
            with self.db.transaction() as conn:
                values = [(r["delivery_area"],r["delivery_date"] or day.isoformat(),r["delivery_start"],r["delivery_end"],r["contract_id"],r["contract_name"],r["contract_type"],r["resolution_minutes"],r["is_local_contract"],r["contract_open_time"],r["contract_close_time"],r["open"],r["high"],r["low"],r["close"],r["vwap"],r["vwap_1h"],r["vwap_3h"],r["volume"],r["buy_volume"],r["sell_volume"],r["first_trade_time"],r["last_trade_time"],r["source_update_time"],r["price_unit"],r["volume_unit"],"nordpool",run_id,collected,digest) for r in rows]
                inserted = self.db.insert_many(conn, """INSERT OR IGNORE INTO intraday_contract_stats
                  (delivery_area,delivery_date,delivery_start,delivery_end,contract_id,contract_name,contract_type,resolution_minutes,
                   is_local_contract,contract_open_time,contract_close_time,open,high,low,close,vwap,vwap_1h,vwap_3h,volume,buy_volume,
                   sell_volume,first_trade_time,last_trade_time,source_update_time,price_unit,volume_unit,source_id,run_id,collected_at,raw_payload_hash)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)",("nordpool_intraday",partition,status,collected,len(rows),run_id))
                conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,chunk_start,chunk_end,
                  attempt_count,status,first_attempt_at,last_attempt_at,records_received,next_action) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?)
                  ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET attempt_count=backfill_jobs.attempt_count+1,
                  status=excluded.status,last_attempt_at=excluded.last_attempt_at,records_received=excluded.records_received,error_class=NULL,
                  http_status=NULL,error_message=NULL,next_action='none'""",
                  (str(uuid.uuid4()),"nordpool_intraday","nordpool",partition,str(day),str(day),str(day),str(day),status,collected,collected,len(rows),"none"))
            self.db.finish_run(run_id,status,len(rows),inserted,payload_hash=digest)
            return len(rows), inserted
        except Exception as exc:
            self.db.finish_run(run_id,"FAILED",error=str(exc)); raise

    def _failed(self, partition: str, day: date, area: str, exc: Exception) -> None:
        kind, http_status = classify_error(exc); status = "RETRY_EXHAUSTED" if kind == "RETRYABLE_REMOTE_ERROR" else kind; now=utcnow()
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,chunk_start,chunk_end,
              attempt_count,status,first_attempt_at,last_attempt_at,error_class,http_status,error_message,next_action) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)
              ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET attempt_count=backfill_jobs.attempt_count+1,status=excluded.status,
              last_attempt_at=excluded.last_attempt_at,error_class=excluded.error_class,http_status=excluded.http_status,error_message=excluded.error_message""",
              (str(uuid.uuid4()),"nordpool_intraday","nordpool",partition,str(day),str(day),str(day),str(day),status,now,now,kind,http_status,str(exc)[:2000],"repair-gaps"))

    def _headers(self) -> dict[str,str]:
        return {"accept":"application/json, text/plain, */*","origin":"https://data.nordpoolgroup.com","referer":"https://data.nordpoolgroup.com/","user-agent":self.settings.user_agent}
