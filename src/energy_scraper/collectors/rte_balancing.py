from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from energy_scraper.core.archive import archive_payload
from energy_scraper.core.calendars import local_intervals
from energy_scraper.core.database import Database, utcnow
from energy_scraper.core.error_classifier import classify_error
from .base import ApiCollector


DATASET = "rte_balancing_volumes_fr"
# The endpoint is specifically the RTE "volumes" table. These are its observed
# field names; no price fields are re-labelled as volumes.
SOURCE_FIELDS = {"fcr", "afrr", "mfrr", "rr", "rr_standard", "activation_non_pc", "deltap", "igcc",
                 "countertrading_xb_redispatching", "tso_mutual_emergency", "xb_balancing",
                 "volume_mfrr_SA", "volume_mfrr_DA"}


def parse_rte_payload(payload: Any, requested_day: date) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list) or not payload["values"]:
        raise ValueError("RTE business response is empty or missing values")
    values = payload["values"]
    grid = local_intervals(requested_day, 15)
    if len(values) != len(grid):
        raise ValueError(f"RTE interval count mismatch: expected {len(grid)}, got {len(values)}")
    rows: list[dict[str, Any]] = []
    for source, (expected_utc, expected_local) in zip(values, grid, strict=True):
        if not isinstance(source, dict) or set(source) - ({"date"} | SOURCE_FIELDS):
            raise ValueError("RTE response contains an unknown field")
        timestamp = datetime.fromisoformat(str(source.get("date")))
        if timestamp.tzinfo is None or timestamp.astimezone(expected_utc.tzinfo) != expected_utc:
            raise ValueError(f"RTE timestamp mismatch: {source.get('date')}")
        end = (expected_utc + timedelta(minutes=15)).astimezone(expected_local.tzinfo)
        for field in sorted(SOURCE_FIELDS & set(source)):
            raw = source[field]
            directional = isinstance(raw, dict)
            entries = raw.items() if directional else [(None, raw)]
            if directional and set(raw) - {"rise", "drop"}:
                raise ValueError(f"RTE unknown direction in {field}")
            for direction, value in entries:
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    raise ValueError(f"RTE non-numeric value in {field}")
                rows.append({"delivery_date": requested_day.isoformat(), "period_start": expected_local.isoformat(),
                             "period_end": end.isoformat(), "direction": direction, "reserve_type": field,
                             "value": value, "unit": "MWh", "source_field": field})
    return rows, payload.get("updatedDate")


class RteBalancingCollector(ApiCollector):
    def __init__(self, settings: Any, db: Database):
        super().__init__(settings); self.db = db
        self.url = settings.sources()["rte_balancing"]["base_url"]

    def collect(self, start: date, end: date, force: bool = False) -> dict[str, int]:
        totals = {"partitions": 0, "received": 0, "inserted": 0, "errors": 0}; day = start
        while day <= end:
            partition = day.isoformat()
            with self.db.connect() as conn:
                complete = conn.execute("SELECT 1 FROM collection_partitions WHERE dataset_id=? AND partition_key=? AND status='SUCCESS'", (DATASET,partition)).fetchone()
            if force or not complete:
                totals["partitions"] += 1
                try:
                    received, inserted = self._collect_day(day,partition)
                    totals["received"] += received; totals["inserted"] += inserted
                except Exception as exc:
                    totals["errors"] += 1; self._failed(partition,day,exc)
            day += timedelta(days=1)
        return totals

    def _collect_day(self, day: date, partition: str) -> tuple[int, int]:
        run_id = self.db.start_run("rte",self.url)
        try:
            payload, request_url = self.get_json(self.url,{"startDate":day.strftime("%d/%m/%Y")},{"accept":"application/json"})
            _, digest = archive_payload(self.settings.raw_dir,"rte",payload,{"request_url":request_url,"delivery_date":partition})
            rows, updated = parse_rte_payload(payload,day); collected = utcnow()
            with self.db.transaction() as conn:
                inserted = self.db.insert_many(conn,"""INSERT OR IGNORE INTO rte_balancing_volumes
                  (delivery_date,period_start,period_end,timezone,direction,reserve_type,metric,value,unit,source_field,source_update_time,collected_at,source_id,run_id,raw_payload_hash,quality_status)
                  VALUES(?,?,?,'Europe/Paris',?,?,'volume',?,?,?,?,?,'rte',?,?,'valid')""",
                  [(r["delivery_date"],r["period_start"],r["period_end"],r["direction"],r["reserve_type"],r["value"],r["unit"],r["source_field"],updated,collected,run_id,digest) for r in rows])
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)",(DATASET,partition,"SUCCESS",collected,len(rows),run_id))
            self.db.finish_run(run_id,"SUCCESS",len(rows),inserted,payload_hash=digest); return len(rows),inserted
        except Exception as exc:
            self.db.finish_run(run_id,"FAILED",error=str(exc)); raise

    def _failed(self, partition: str, day: date, exc: Exception) -> None:
        kind,status=classify_error(exc); now=utcnow()
        with self.db.connect() as conn:
            run=conn.execute("SELECT run_id FROM scrape_runs WHERE source_id='rte' ORDER BY started_at DESC LIMIT 1").fetchone()
            if run:
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)",(DATASET,partition,"FAILED",now,0,run[0]))
            conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,chunk_start,chunk_end,attempt_count,status,first_attempt_at,last_attempt_at,error_class,http_status,error_message,next_action)
              VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?) ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET attempt_count=attempt_count+1,status=excluded.status,last_attempt_at=excluded.last_attempt_at,error_class=excluded.error_class,http_status=excluded.http_status,error_message=excluded.error_message""",
              (str(uuid.uuid4()),DATASET,"rte",partition,str(day),str(day),str(day),str(day),kind,now,now,kind,status,str(exc)[:2000],"repair-gaps"))
