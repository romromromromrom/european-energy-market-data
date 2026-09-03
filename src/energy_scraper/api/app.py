from __future__ import annotations

import base64
import hmac
import json
import os
import sqlite3
import time
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import csv
import io

from energy_scraper import __version__
from energy_scraper.core.config import Settings
from energy_scraper.core.database import Database
from energy_scraper.core.backfill import BackfillRunner
from energy_scraper.core.wizard import validate_plan

DATASETS = {
 "futures": {"table":"market_prices p JOIN instruments i ON p.instrument_id=i.instrument_id JOIN sources s ON p.source_id=s.source_id",
   "columns":["p.observation_id","i.product_code","i.maturity","i.market_area","p.trading_date","p.settlement","p.volume","p.open_interest","s.source_name AS source","p.observation_timestamp AS collected_at","p.data_type","p.quality_status"], "date":"p.trading_date", "area":"i.market_area", "instrument":"i.product_code"},
 "intraday": {"table":"intraday_contract_stats i JOIN sources s ON i.source_id=s.source_id",
   "columns":["i.snapshot_id","i.delivery_area","i.delivery_date","i.delivery_start","i.delivery_end","i.contract_id","i.contract_name","i.contract_type","i.resolution_minutes","i.open","i.high","i.low","i.close","i.vwap","i.vwap_1h","i.vwap_3h","i.volume","i.buy_volume","i.sell_volume","i.source_update_time","i.collected_at","i.price_unit","i.volume_unit","s.source_name AS source","i.data_type","i.quality_status"], "date":"i.delivery_date", "area":"i.delivery_area", "instrument":"i.contract_id"},
}
ALIASES={"market_prices":"futures","intraday_contract_stats":"intraday"}


def encode_cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode()


def decode_cursor(value: str | None) -> int:
    if not value: return 0
    try: return max(0,int(base64.urlsafe_b64decode(value.encode()).decode()))
    except Exception as exc: raise HTTPException(400,"Invalid cursor") from exc


def create_app(settings: Settings | None=None) -> FastAPI:
    settings=settings or Settings.load(); db=Database(settings.db_path); app=FastAPI(title="European Energy Market Data API",version=__version__)
    backfill_lock = threading.Lock()
    ui_dir=Path(__file__).resolve().parents[1]/"ui"
    app.mount("/ui",StaticFiles(directory=ui_dir),name="ui")
    api_cfg=(settings.sources().get("api") or {}); max_limit=int(api_cfg.get("max_limit",5000)); auth_cfg=api_cfg.get("auth") or {}

    def authorize(authorization: str | None=Header(default=None)) -> None:
        if not auth_cfg.get("enabled",True): return
        expected=os.getenv(auth_cfg.get("token_env","ENERGY_API_TOKEN"))
        if not expected: return  # local-only deployment can operate without a token
        supplied=authorization.removeprefix("Bearer ") if authorization else ""
        if not hmac.compare_digest(supplied,expected): raise HTTPException(401,"Invalid bearer token")

    def ro() -> sqlite3.Connection:
        try: return db.connect(read_only=True)
        except sqlite3.Error as exc: raise HTTPException(503,"Database unavailable") from exc

    @app.get("/",include_in_schema=False)
    def dashboard():
        return FileResponse(ui_dir/"index.html")

    @app.middleware("http")
    async def audit(request:Request,call_next):
        started=time.monotonic(); request_id=str(uuid.uuid4())
        response=await call_next(request)
        record={"request_id":request_id,"timestamp":datetime.now(UTC).isoformat(),"endpoint":request.url.path,
          "query_parameters_sanitized":dict(request.query_params),"status":response.status_code,"duration_ms":round((time.monotonic()-started)*1000,2),"client_class":"local" if request.client and request.client.host in {"127.0.0.1","::1","testclient"} else "remote"}
        log=settings.root/"logs"/"api.jsonl"; log.parent.mkdir(parents=True,exist_ok=True)
        with log.open("a",encoding="utf-8") as fh: fh.write(json.dumps(record,separators=(",",":"))+"\n")
        response.headers["X-Request-ID"]=request_id; return response

    @app.get("/health")
    def health(_:None=Depends(authorize)):
        try:
            with ro() as conn:
                row=conn.execute("SELECT MAX(finished_at) FROM scrape_runs").fetchone()
            return {"status":"ok","db_readable":True,"last_collection_at":row[0],"api_version":__version__}
        except HTTPException:
            return JSONResponse(status_code=503,content={"status":"error","db_readable":False,"last_collection_at":None,"api_version":__version__})

    @app.get("/v1/datasets")
    def datasets(_:None=Depends(authorize)): return {"data":list(DATASETS)}

    @app.get("/v1/backfill-plan")
    def backfill_plan(_:None=Depends(authorize)):
        plan_path = settings.backfill_plan_path
        if plan_path.exists():
            plan = validate_plan(plan_path)
        else:
            plan = {}
        with ro() as conn:
            limits = {row["dataset_id"]: dict(row) for row in conn.execute("SELECT * FROM historical_limits")}
            execution = conn.execute("SELECT * FROM backfill_executions ORDER BY started_at DESC LIMIT 1").fetchone()
        rows = []
        for dataset_id, item in plan.items():
            merged = dict(item)
            if dataset_id in limits:
                merged.update({key: limits[dataset_id][key] for key in ("earliest_available_date", "first_unavailable_date", "detection_method", "detected_at")})
                try:
                    evidence = json.loads(limits[dataset_id].get("evidence") or "{}")
                except (TypeError, json.JSONDecodeError):
                    evidence = {}
                merged["earliest_available_partition"] = evidence.get("earliest_available_partition")
                merged["status"] = "ACCESS_RESTRICTED" if evidence.get("last_error_class") == "ACCESS_RESTRICTED" and not merged.get("earliest_available_date") else "DONE"
            rows.append(merged)
        return {"meta":{"row_count":len(rows),"path":str(plan_path),"last_execution":dict(execution) if execution else None},"data":rows}

    @app.post("/v1/backfill/run",status_code=202)
    def start_backfill(request:Request,_:None=Depends(authorize)):
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403,"Backfill can only be started locally")
        if not backfill_lock.acquire(blocking=False):
            raise HTTPException(409,"A backfill is already running")
        def worker() -> None:
            try:
                BackfillRunner(settings,db).run("dashboard")
            finally:
                backfill_lock.release()
        threading.Thread(target=worker,name="energy-backfill",daemon=True).start()
        return {"status":"STARTED"}

    @app.get("/v1/catalog")
    def catalog(_:None=Depends(authorize)):
        with ro() as conn:
            sources=[dict(r) for r in conn.execute("SELECT source_id,source_name,source_url,access_method,license_notes FROM sources")]
            fut=dict(conn.execute("SELECT COUNT(*),MIN(trading_date),MAX(trading_date) FROM market_prices").fetchone())
            intra=dict(conn.execute("SELECT COUNT(*),MIN(delivery_date),MAX(delivery_date) FROM intraday_contract_stats").fetchone())
            gaps=conn.execute("SELECT COUNT(*) FROM data_gaps WHERE gap_status='OPEN'").fetchone()[0]
        return {"meta":{"row_count":2},"data":{"datasets":{"futures":fut,"intraday":intra},"sources":sources,"open_gaps":gaps}}

    @app.get("/v1/coverage")
    def coverage(_:None=Depends(authorize)):
        with ro() as conn:
            rows=[dict(r) for r in conn.execute("""SELECT e.dataset_id,e.series_key,COUNT(*) expected,
              SUM(CASE WHEN g.gap_id IS NULL THEN 1 ELSE 0 END) received,SUM(CASE WHEN g.gap_status='OPEN' THEN 1 ELSE 0 END) missing
              FROM expected_observations e LEFT JOIN data_gaps g ON e.dataset_id=g.dataset_id AND e.series_key=g.series_key
              AND e.expected_timestamp_utc=g.expected_timestamp_utc GROUP BY e.dataset_id,e.series_key""")]
        return {"meta":{"row_count":len(rows)},"data":rows}

    @app.get("/v1/latest")
    def latest(dataset:str=Query(pattern="^(futures|intraday)$"),area:str|None=None,_:None=Depends(authorize)):
        spec=DATASETS[dataset]; where=f" WHERE {spec['area']}=?" if area else ""; params=(area,) if area else ()
        sql=f"SELECT {','.join(spec['columns'])} FROM {spec['table']}{where} ORDER BY {spec['date']} DESC LIMIT 1"
        with ro() as conn:
            row=conn.execute(sql,params).fetchone()
        return envelope(dataset,[dict(row)] if row else [],None,None,None)

    @app.get("/v1/gaps")
    def gaps(dataset:str|None=None,area:str|None=None,start:str|None=None,end:str|None=None,status:str|None=None,
             limit:int=Query(1000,ge=1),cursor:str|None=None,_:None=Depends(authorize)):
        limit=min(limit,max_limit); where=[]; params=[]
        for column,value,op in (("dataset_id",dataset,"="),("series_key",area,"LIKE"),("expected_timestamp_utc",start,">="),("expected_timestamp_utc",end,"<="),("gap_status",status,"=")):
            if value is not None: where.append(f"{column} {op} ?"); params.append(f"%{value}%" if op=="LIKE" else value)
        offset=decode_cursor(cursor); clause=" WHERE "+" AND ".join(where) if where else ""
        with ro() as conn: rows=[dict(r) for r in conn.execute(f"SELECT * FROM data_gaps{clause} ORDER BY expected_timestamp_utc LIMIT ? OFFSET ?",(*params,limit+1,offset))]
        more=len(rows)>limit; rows=rows[:limit]
        return envelope("gaps",rows,encode_cursor(offset+limit) if more else None,start,end)

    @app.get("/v1/series")
    def series(dataset:str,area:str|None=None,instrument:str|None=None,maturity:str|None=None,start:str|None=None,end:str|None=None,resolution:int|None=None,
               fields:str|None=None,limit:int=Query(1000,ge=1),cursor:str|None=None,_:None=Depends(authorize)):
        key=ALIASES.get(dataset,dataset)
        if key not in DATASETS: raise HTTPException(400,"Unknown dataset")
        spec=DATASETS[key]; allowed=spec["columns"]
        selected=allowed
        if fields:
            names={x.strip() for x in fields.split(",")}; selected=[x for x in allowed if (x.split(" AS ")[-1].split(".")[-1] in names)]
            if not selected: raise HTTPException(400,"No valid fields requested")
        where=[]; params=[]
        for column,value,op in ((spec["area"],area,"="),(spec["instrument"],instrument,"="),(spec["date"],start,">="),(spec["date"],end,"<=")):
            if value is not None: where.append(f"{column}{op}?"); params.append(value)
        if maturity is not None:
            if key != "futures": raise HTTPException(400,"maturity applies only to futures")
            where.append("i.maturity=?"); params.append(maturity)
        if resolution is not None:
            if key!="intraday": raise HTTPException(400,"resolution applies only to intraday")
            where.append("i.resolution_minutes=?"); params.append(resolution)
        clause=" WHERE "+" AND ".join(where) if where else ""; limit=min(limit,max_limit); offset=decode_cursor(cursor)
        sql=f"SELECT {','.join(selected)} FROM {spec['table']}{clause} ORDER BY {spec['date']} LIMIT ? OFFSET ?"
        with ro() as conn: rows=[dict(r) for r in conn.execute(sql,(*params,limit+1,offset))]
        more=len(rows)>limit; rows=rows[:limit]
        return envelope(key,rows,encode_cursor(offset+limit) if more else None,start,end)

    @app.get("/v1/futures")
    def futures(area:str|None=None,instrument:str|None=None,maturity:str|None=None,start:str|None=None,end:str|None=None,
                limit:int=Query(1000,ge=1),cursor:str|None=None,_:None=Depends(authorize)):
        return series("futures",area=area,instrument=instrument,maturity=maturity,start=start,end=end,limit=limit,cursor=cursor)

    @app.get("/v1/futures/maturities")
    def futures_maturities(_:None=Depends(authorize)):
        with ro() as conn:
            rows=[row[0] for row in conn.execute("""SELECT DISTINCT i.maturity FROM instruments i
              JOIN market_prices p USING(instrument_id) WHERE i.exchange='EEX' AND p.settlement IS NOT NULL
              ORDER BY i.maturity""")]
        return {"meta":{"row_count":len(rows)},"data":rows}

    @app.get("/v1/intraday/contracts")
    def contracts(area:str|None=None,start:str|None=None,end:str|None=None,resolution:int|None=None,limit:int=Query(1000,ge=1),cursor:str|None=None,_:None=Depends(authorize)):
        return series("intraday",area=area,start=start,end=end,resolution=resolution,limit=limit,cursor=cursor)

    @app.get("/v1/intraday/snapshots")
    def snapshots(area:str|None=None,start:str|None=None,end:str|None=None,limit:int=Query(1000,ge=1),cursor:str|None=None,_:None=Depends(authorize)):
        return series("intraday",area=area,start=start,end=end,limit=limit,cursor=cursor)

    @app.get("/v1/export")
    def bounded_export(dataset:str=Query(pattern="^(futures|intraday)$"),start:str=Query(),end:str=Query(),
                       area:str|None=None,format:str=Query("csv",pattern="^csv$"),_:None=Depends(authorize)):
        spec=DATASETS[dataset]; where=[f"{spec['date']}>=?",f"{spec['date']}<=?"]; params:list[Any]=[start,end]
        if area: where.append(f"{spec['area']}=?"); params.append(area)
        sql=f"SELECT {','.join(spec['columns'])} FROM {spec['table']} WHERE {' AND '.join(where)} ORDER BY {spec['date']} LIMIT ?"
        params.append(max_limit)
        with ro() as conn: rows=[dict(r) for r in conn.execute(sql,params)]
        stream=io.StringIO(); writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else ["empty"]); writer.writeheader(); writer.writerows(rows)
        return StreamingResponse(iter([stream.getvalue()]),media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="{dataset}_{start}_{end}.csv"'})

    for route,table in (("/v1/power-system","power_system_state"),("/v1/gas/storage","gas_storage"),("/v1/gas/flows","gas_flows"),("/v1/interconnectors","interconnector_flows")):
        def endpoint(limit:int=Query(1000,ge=1),_:None=Depends(authorize),_table=table):
            with ro() as conn: rows=[dict(r) for r in conn.execute(f"SELECT * FROM {_table} LIMIT ?",(min(limit,max_limit),))]
            return envelope(_table,rows,None,None,None)
        app.add_api_route(route,endpoint,methods=["GET"])
    return app


def envelope(dataset:str,rows:list[dict[str,Any]],cursor:str|None,start:str|None,end:str|None)->dict:
    return {"meta":{"dataset":dataset,"source":"preserved per row","unit":"preserved per row","timezone":"UTC + local delivery fields","start":start,"end":end,"row_count":len(rows),"next_cursor":cursor},"data":rows}


app=create_app()
