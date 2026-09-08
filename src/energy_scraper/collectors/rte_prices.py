from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from energy_scraper.core.archive import archive_payload
from energy_scraper.core.calendars import local_intervals
from energy_scraper.core.database import Database, utcnow
from energy_scraper.core.error_classifier import classify_error
from .base import ApiCollector

DATASET = "rte_balancing_prices_fr"
RESERVE_FIELDS = {
    "weighted_average_price": None,
    "marginal_price": None,
    "weighted_average_price_as": "AS",
    "weighted_average_price_margin": "margin",
    "weighted_average_price_mfrr": "mFRR",
    "weighted_average_price_rr": "RR",
    "weighted_average_price_afrr_activated_for_fr": "aFRR_FR",
    "weighted_average_price_meas": "MEAS",
}
SCALAR_FIELDS = {"clearing_price": ("clearing", None), "pre": ("imbalance", None)}

def _price(value: Any, field: str) -> float | None:
    if value is None: return None
    if isinstance(value, bool): raise ValueError(f"RTE invalid price in {field}")
    try: return float(value)
    except (TypeError, ValueError) as exc: raise ValueError(f"RTE invalid price in {field}: {value!r}") from exc

def parse_rte_prices(payload: Any, requested_day: date, allow_partial: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(payload,dict) or not isinstance(payload.get("values"),list) or not payload["values"]:
        raise ValueError("RTE price response is empty")
    grid=local_intervals(requested_day,15); source_rows=payload["values"]
    if (not allow_partial and len(source_rows)!=len(grid)) or len(source_rows)>len(grid):
        raise ValueError(f"RTE price interval count mismatch: expected {len(grid)}, got {len(source_rows)}")
    by_time={datetime.fromisoformat(str(row.get("date"))).astimezone(grid[0][0].tzinfo):row for row in source_rows if isinstance(row,dict)}
    rows=[]
    for utc_start,local_start in grid:
        source=by_time.get(utc_start)
        if source is None:
            if allow_partial: continue
            raise ValueError(f"RTE missing price interval {local_start.isoformat()}")
        unknown=set(source)-({"date"}|set(RESERVE_FIELDS)|set(SCALAR_FIELDS))
        if unknown: raise ValueError(f"RTE unknown price fields: {sorted(unknown)}")
        end=(utc_start+timedelta(minutes=15)).astimezone(local_start.tzinfo)
        base={"delivery_date":requested_day.isoformat(),"period_start":local_start.isoformat(),"period_end":end.isoformat()}
        for field,reserve in RESERVE_FIELDS.items():
            value=source.get(field)
            if value is not None and not isinstance(value,dict): raise ValueError(f"RTE {field} must be directional")
            for direction in ("rise","drop"):
                rows.append({**base,"direction":direction,"reserve_type":reserve,"price_type":field,
                             "price_eur_mwh":_price(value.get(direction) if value else None,field),"source_field":field})
        rows.append({**base,"direction":None,"reserve_type":None,"price_type":"clearing",
                     "price_eur_mwh":_price(source.get("clearing_price"),"clearing_price"),"source_field":"clearing_price"})
        pre=source.get("pre")
        if pre is not None and not isinstance(pre,dict): raise ValueError("RTE pre must be directional")
        for direction in ("positive","negative"):
            rows.append({**base,"direction":direction,"reserve_type":None,"price_type":"imbalance",
                         "price_eur_mwh":_price(pre.get(direction) if pre else None,"pre"),"source_field":"pre"})
    return rows,payload.get("updatedDate")

class RtePriceCollector(ApiCollector):
    def __init__(self,settings:Any,db:Database):
        super().__init__(settings); self.db=db; self.url=settings.sources()["rte_prices"]["base_url"]
    def collect(self,start:date,end:date,force:bool=False,allow_partial:bool=False)->dict[str,int]:
        totals={"partitions":0,"received":0,"inserted":0,"errors":0}; day=start
        while day<=end:
            part=day.isoformat()
            with self.db.connect() as conn: complete=conn.execute("SELECT 1 FROM collection_partitions WHERE dataset_id=? AND partition_key=? AND status='SUCCESS'",(DATASET,part)).fetchone()
            if force or not complete:
                totals["partitions"]+=1
                try:
                    received,inserted=self._day(day,part,allow_partial); totals["received"]+=received; totals["inserted"]+=inserted
                except Exception as exc: totals["errors"]+=1; self._failed(part,day,exc)
            day+=timedelta(days=1)
        return totals
    def _day(self,day:date,part:str,allow_partial:bool)->tuple[int,int]:
        run=self.db.start_run("rte",self.url)
        try:
            payload,url=self.get_json(self.url,{"startDate":day.strftime("%d/%m/%Y")},{"accept":"application/json"})
            _,digest=archive_payload(self.settings.raw_dir,"rte_prices",payload,{"request_url":url,"delivery_date":part})
            rows,updated=parse_rte_prices(payload,day,allow_partial); collected=utcnow()
            with self.db.transaction() as conn:
                inserted=self.db.insert_many(conn,"""INSERT OR IGNORE INTO rte_balancing_prices
                (delivery_date,period_start,period_end,timezone,direction,reserve_type,price_type,price_eur_mwh,unit,source_field,source_update_time,collected_at,source_id,run_id,raw_payload_hash,quality_status)
                VALUES(?,?,?,'Europe/Paris',?,?,?,?,'EUR/MWh',?,?,?,'rte',?,?,'valid')""",
                [(r["delivery_date"],r["period_start"],r["period_end"],r["direction"],r["reserve_type"],r["price_type"],r["price_eur_mwh"],r["source_field"],updated,collected,run,digest) for r in rows])
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)",(DATASET,part,"SUCCESS",collected,len(rows),run))
            self.db.finish_run(run,"SUCCESS",len(rows),inserted,payload_hash=digest); return len(rows),inserted
        except Exception as exc: self.db.finish_run(run,"FAILED",error=str(exc)); raise
    def _failed(self,part:str,day:date,exc:Exception)->None:
        kind,status=classify_error(exc); now=utcnow()
        with self.db.connect() as conn:
            run=conn.execute("SELECT run_id FROM scrape_runs WHERE source_id='rte' ORDER BY started_at DESC LIMIT 1").fetchone()
            if run: conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)",(DATASET,part,"FAILED",now,0,run[0]))
            conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,chunk_start,chunk_end,attempt_count,status,first_attempt_at,last_attempt_at,error_class,http_status,error_message,next_action)
            VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?) ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET attempt_count=attempt_count+1,status=excluded.status,last_attempt_at=excluded.last_attempt_at,error_class=excluded.error_class,http_status=excluded.http_status,error_message=excluded.error_message""",
            (str(uuid.uuid4()),DATASET,"rte",part,str(day),str(day),str(day),str(day),kind,now,now,kind,status,str(exc)[:2000],"repair-gaps"))
