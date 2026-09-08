from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

from energy_scraper.collectors.eex import EexCollector, PRODUCTS
from energy_scraper.collectors.nordpool import NordPoolCollector
from energy_scraper.collectors.epex_spot import EpexSpotCollector
from energy_scraper.collectors.rte_balancing import RteBalancingCollector
from energy_scraper.collectors.rte_prices import RtePriceCollector
from energy_scraper.core.database import Database, utcnow
from energy_scraper.core.wizard import validate_plan


EEX_DATASETS = {product.dataset_id: code for code, product in PRODUCTS.items()}
NORDPOOL_DATASETS = {
    "nordpool_intraday_fr": "FR",
    "nordpool_intraday_be": "BE",
    "nordpool_intraday_de_lu": "DE-LU",
}


class BackfillRunner:
    """Execute the actionable plan and discover evidence-backed historical cutoffs."""

    def __init__(self, settings: Any, db: Database):
        self.settings = settings
        self.db = db

    def run(self, trigger_source: str = "cli", force: bool = False) -> dict[str, Any]:
        plan = validate_plan(self.settings.backfill_plan_path)
        actionable = [item for item in plan.values() if item["collector"] in {"eex", "nordpool", "epex", "rte_balancing", "rte_prices"} and item["mode"] != "skip"]
        execution_id = str(uuid.uuid4())
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO backfill_executions(execution_id,started_at,status,trigger_source,datasets_total) VALUES(?,?,?,?,?)",
                (execution_id, utcnow(), "RUNNING", trigger_source, len(actionable)),
            )
        totals = {"records_received": 0, "records_inserted": 0, "datasets_completed": 0}
        results: dict[str, Any] = {}
        try:
            eex_items = [item for item in actionable if item["collector"] == "eex"]
            if eex_items:
                eex_result = self._run_eex(eex_items, force)
                results.update(eex_result["datasets"])
                totals["records_received"] += eex_result["received"]
                totals["records_inserted"] += eex_result["inserted"]
                totals["datasets_completed"] += len(eex_items)
            for item in (x for x in actionable if x["collector"] == "nordpool"):
                result = self._run_nordpool(item, force)
                results[item["dataset_id"]] = result
                totals["records_received"] += result["received"]
                totals["records_inserted"] += result["inserted"]
                totals["datasets_completed"] += 1
            for item in (x for x in actionable if x["collector"] in {"epex","rte_balancing","rte_prices"}):
                today=date.today(); start=self._date(str(item["requested_start"]),today); end=self._date(str(item["requested_end"]),today)
                collector=EpexSpotCollector(self.settings,self.db) if item["collector"]=="epex" else (RteBalancingCollector(self.settings,self.db) if item["collector"]=="rte_balancing" else RtePriceCollector(self.settings,self.db))
                result=collector.collect(start,end,force); results[item["dataset_id"]]={"status":"DONE",**result}
                totals["records_received"] += result["received"]; totals["records_inserted"] += result["inserted"]; totals["datasets_completed"] += 1
            status = "SUCCESS"
            error = None
        except Exception as exc:
            status, error = "FAILED", str(exc)[:2000]
            raise
        finally:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE backfill_executions SET finished_at=?,status=?,datasets_completed=?,records_received=?,records_inserted=?,error_message=? WHERE execution_id=?",
                    (utcnow(), status, totals["datasets_completed"], totals["records_received"], totals["records_inserted"], error, execution_id),
                )
        return {"execution_id": execution_id, "status": status, **totals, "datasets": results}

    @staticmethod
    def _date(value: str, fallback: date) -> date:
        return fallback if value == "today" else date.fromisoformat(value)

    def _run_eex(self, items: list[dict[str, Any]], force: bool) -> dict[str, Any]:
        today = date.today()
        requested_start = min(self._date(str(x["requested_start"]), today) for x in items)
        mode_max = any(x["mode"] == "max" for x in items)
        # In max mode scan whole trading years backwards. A complete empty year confirms the boundary.
        scan_start = date(2000, 1, 1) if mode_max else requested_start
        codes = [EEX_DATASETS[x["dataset_id"]] for x in items]
        collector = EexCollector(self.settings, self.db)
        result = {"partitions": 0, "received": 0, "inserted": 0, "errors": 0}
        if mode_max:
            year = today.year + 4
            while year >= 2000:
                probe = collector.collect(scan_start, today, [year], codes, force)
                result = {key: result.get(key, 0) + probe.get(key, 0) for key in result}
                if self._eex_maturity_count(codes, year) == 0 and probe["errors"] == 0:
                    break
                year -= 1
        else:
            years = list(range(max(requested_start.year, today.year + 1), today.year + 4))
            result = collector.collect(requested_start, today, years, codes, force)
        datasets: dict[str, Any] = {}
        for item in items:
            dataset_id = item["dataset_id"]
            earliest = self._earliest_eex(dataset_id)
            maturity = self._earliest_eex_maturity(EEX_DATASETS[dataset_id])
            unavailable = (earliest - timedelta(days=1)).isoformat() if earliest else None
            self._save_limit(dataset_id, EEX_DATASETS[dataset_id], earliest.isoformat() if earliest else None, unavailable,
                             "response_window_and_maturity_probe", {"collector_result": result,
                              "earliest_available_partition": f"CAL-{maturity}" if maturity else None,
                              "first_unavailable_partition": f"CAL-{maturity - 1}" if maturity else None})
            datasets[dataset_id] = {"status": "DONE", "earliest_available_date": earliest.isoformat() if earliest else None,
                                    "earliest_available_partition": f"CAL-{maturity}" if maturity else None}
        return {"datasets": datasets, "received": result["received"], "inserted": result["inserted"]}

    def _eex_maturity_count(self, codes: list[str], year: int) -> int:
        placeholders = ",".join("?" for _ in codes)
        with self.db.connect(read_only=True) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM market_prices p JOIN instruments i USING(instrument_id) WHERE i.product_code IN ({placeholders}) AND i.maturity=? AND p.settlement IS NOT NULL",
                (*codes, f"CAL-{year}"),
            ).fetchone()
        return int(row[0])

    def _earliest_eex_maturity(self, code: str) -> int | None:
        with self.db.connect(read_only=True) as conn:
            row = conn.execute("SELECT MIN(CAST(substr(i.maturity,5) AS INT)) FROM instruments i JOIN market_prices p USING(instrument_id) WHERE i.product_code=? AND p.settlement IS NOT NULL", (code,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _earliest_eex(self, dataset_id: str) -> date | None:
        code = EEX_DATASETS[dataset_id]
        with self.db.connect(read_only=True) as conn:
            row = conn.execute("SELECT MIN(p.trading_date) FROM market_prices p JOIN instruments i USING(instrument_id) WHERE i.product_code=?", (code,)).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def _run_nordpool(self, item: dict[str, Any], force: bool) -> dict[str, Any]:
        today = date.today(); area = NORDPOOL_DATASETS[item["dataset_id"]]
        requested_start = self._date(str(item["requested_start"]), today)
        collector = NordPoolCollector(self.settings, self.db)
        result = collector.collect(requested_start, today, [area], force, "final")
        earliest = self._earliest_nordpool(area)
        # For max mode, move backwards one day at a time; one failed/empty day directly
        # adjacent to known data is the endpoint boundary for this daily API.
        first_unavailable = None
        if item["mode"] == "max" and earliest:
            probe_day = earliest - timedelta(days=1)
            probe = collector.collect(probe_day, probe_day, [area], force, "final")
            for key in result:
                result[key] += probe.get(key, 0)
            if probe["received"] == 0:
                first_unavailable = probe_day.isoformat()
        elif earliest and requested_start < earliest:
            first_unavailable = (earliest - timedelta(days=1)).isoformat()
        dataset_id = item["dataset_id"]
        last_error = self._latest_job_error(area)
        status = "ACCESS_RESTRICTED" if earliest is None and last_error == "ACCESS_RESTRICTED" else "DONE"
        self._save_limit(dataset_id, area, earliest.isoformat() if earliest else None, first_unavailable,
                         "adjacent_daily_probe", {"collector_result": result, "last_error_class": last_error})
        return {"status": status, "earliest_available_date": earliest.isoformat() if earliest else None,
                "first_unavailable_date": first_unavailable, "received": result["received"], "inserted": result["inserted"]}

    def _earliest_nordpool(self, area: str) -> date | None:
        with self.db.connect(read_only=True) as conn:
            row = conn.execute("SELECT MIN(delivery_date) FROM intraday_contract_stats WHERE delivery_area=?", (area,)).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def _latest_job_error(self, area: str) -> str | None:
        with self.db.connect(read_only=True) as conn:
            row = conn.execute("SELECT error_class FROM backfill_jobs WHERE source_id='nordpool' AND partition_key LIKE ? ORDER BY last_attempt_at DESC LIMIT 1", (f"{area}:%",)).fetchone()
        return row[0] if row else None

    def _save_limit(self, dataset_id: str, series_key: str, earliest: str | None, unavailable: str | None,
                    method: str, evidence: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO historical_limits VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(dataset_id,series_key) DO UPDATE SET earliest_available_date=excluded.earliest_available_date,
              first_unavailable_date=excluded.first_unavailable_date,detection_method=excluded.detection_method,
              detected_at=excluded.detected_at,evidence=excluded.evidence""",
              (dataset_id, series_key, earliest, unavailable, method, utcnow(), json.dumps(evidence, separators=(",", ":"))))
