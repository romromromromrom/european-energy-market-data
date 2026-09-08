from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

import typer
import yaml

from .api.app import create_app
from .browser.network_capture import run_capture
from .collectors.eex import EexCollector
from .collectors.nordpool import NordPoolCollector
from .core.config import Settings
from .core.database import Database
from .core.backfill import BackfillRunner
from .core.brief_export import export_brief_csv
from .core.gaps import export_gap_template, import_gap_csv
from .core.wizard import default_plan, run_wizard, serialize_plan, validate_plan

app=typer.Typer(help="European energy market data collector",no_args_is_help=True)
collect_app=typer.Typer(help="Collect a source"); api_app=typer.Typer(help="Read-only local API"); gaps_app=typer.Typer(help="Gap utilities")
app.add_typer(collect_app,name="collect"); app.add_typer(api_app,name="api"); app.add_typer(gaps_app,name="gaps")


def context():
    settings=Settings.load(); db=Database(settings.db_path); db.initialize(); return settings,db


def parse_date(value:str|None,name:str)->date|None:
    if value is None: return None
    try: return date.fromisoformat(value)
    except ValueError as exc: raise typer.BadParameter(f"{name} must be YYYY-MM-DD") from exc


@app.command("init-db")
def init_db():
    settings,db=context(); typer.echo(f"SQLite initialized: {settings.db_path}")


@app.command("init-backfill")
def init_backfill(non_interactive:bool=typer.Option(False,"--non-interactive"),config:Path|None=typer.Option(None,"--config")):
    settings,_=context(); target=config or settings.backfill_plan_path
    if non_interactive:
        if target.exists():
            plan = validate_plan(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            plan = serialize_plan(default_plan())
            target.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
    else:
        plan = run_wizard(target)
    typer.echo(f"Plan valid: {len(plan)} datasets — {target}")


@app.command("run-backfill")
def run_backfill(force:bool=typer.Option(False,"--force",help="Re-fetch partitions already completed")):
    settings,db=context()
    typer.echo(json.dumps(BackfillRunner(settings,db).run("cli",force),indent=2))


@collect_app.command("eex")
def collect_eex(start:str|None=typer.Option(None),end:str|None=typer.Option(None),years:str|None=typer.Option(None),codes:str="F7BY,F7PY,G3BY",force:bool=False,ticker:bool=False):
    settings,db=context(); end_date=parse_date(end,"end") or date.today(); start_date=parse_date(start,"start") or end_date-timedelta(days=7)
    collector=EexCollector(settings,db); parsed_years=[int(x) for x in years.split(",")] if years else None
    result=collector.collect(start_date,end_date,parsed_years,codes.split(","),force)
    if ticker:
        for code in codes.split(","):
            for year in parsed_years or range(date.today().year+1,date.today().year+4):
                try: collector.collect_ticker(code,year)
                except Exception as exc: typer.echo(f"ticker {code}/{year}: {exc}",err=True)
    typer.echo(json.dumps(result,indent=2))


@collect_app.command("nordpool")
def collect_nordpool(date_:str|None=typer.Option(None,"--date"),start:str|None=None,end:str|None=None,areas:str="FR,BE,DE-LU",force:bool=False,mode:str="final"):
    settings,db=context(); selected=parse_date(date_,"date"); end_date=selected or parse_date(end,"end") or date.today(); start_date=selected or parse_date(start,"start") or end_date
    if start_date>end_date: raise typer.BadParameter("start must be <= end")
    result=NordPoolCollector(settings,db).collect(start_date,end_date,[x.strip() for x in areas.split(",") if x.strip()],force,mode)
    typer.echo(json.dumps(result,indent=2))


@collect_app.command("all")
def collect_all(date_:str|None=typer.Option(None,"--date")):
    settings,db=context(); day=parse_date(date_,"date") or date.today()
    typer.echo(json.dumps({"eex":EexCollector(settings,db).collect(day-timedelta(days=7),day),"nordpool":NordPoolCollector(settings,db).collect(day,day,["FR","BE","DE-LU"])},indent=2))


@collect_app.command("daily")
def collect_daily(date_:str|None=typer.Option(None,"--date",help="Reference day; defaults to today")):
    """Refresh EEX and the previous Nord Pool delivery day for the daily brief."""
    settings,db=context(); day=parse_date(date_,"date") or date.today(); results={}; errors={}
    try:
        results["eex"]=EexCollector(settings,db).collect(day-timedelta(days=7),day)
    except Exception as exc:
        errors["eex"]=str(exc)
    try:
        delivery_day=day-timedelta(days=1)
        results["nordpool"]=NordPoolCollector(settings,db).collect(delivery_day,delivery_day,["FR","BE","DE-LU"])
    except Exception as exc:
        errors["nordpool"]=str(exc)
    payload={"reference_date":str(day),"results":results,"errors":errors,"status":"SUCCESS" if not errors else "PARTIAL"}
    typer.echo(json.dumps(payload,indent=2))
    if errors: raise typer.Exit(1)


@app.command("validate")
def validate():
    settings,db=context()
    with db.connect() as conn:
        integrity=conn.execute("PRAGMA integrity_check").fetchone()[0]
        duplicates=conn.execute("SELECT COUNT(*) FROM (SELECT instrument_id,trading_date,price_type,source_id,COUNT(*) n FROM market_prices GROUP BY 1,2,3,4 HAVING n>1)").fetchone()[0]
        failed=conn.execute("SELECT COUNT(*) FROM scrape_runs WHERE status='FAILED'").fetchone()[0]
    typer.echo(json.dumps({"integrity":integrity,"logical_duplicates":duplicates,"failed_runs":failed},indent=2))


@app.command("status")
def status():
    _,db=context()
    with db.connect() as conn:
        result={"runs":conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0],"futures":conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0],"intraday":conn.execute("SELECT COUNT(*) FROM intraday_contract_stats").fetchone()[0],"open_gaps":conn.execute("SELECT COUNT(*) FROM data_gaps WHERE gap_status='OPEN'").fetchone()[0]}
    typer.echo(json.dumps(result,indent=2))


@app.command("export")
def export(dataset:str,start:str,end:str,output:Path=Path("export.csv")):
    _,db=context(); specs={"futures":("market_prices","trading_date"),"intraday":("intraday_contract_stats","delivery_date")}
    if dataset not in specs: raise typer.BadParameter("dataset must be futures or intraday")
    table,column=specs[dataset]; start_date=parse_date(start,"start"); end_date=parse_date(end,"end")
    with db.connect(read_only=True) as conn:
        rows=conn.execute(f"SELECT * FROM {table} WHERE {column} BETWEEN ? AND ?",(str(start_date),str(end_date))).fetchall()
        with output.open("w",newline="",encoding="utf-8") as fh:
            writer=csv.writer(fh); writer.writerow(rows[0].keys() if rows else []); writer.writerows(rows)
    typer.echo(f"{len(rows)} rows -> {output}")


@app.command("export-brief")
def export_brief(
    output:Path=typer.Option(Path("reports/morning_brief.csv"),"--output","-o"),
    end:str|None=typer.Option(None,"--end",help="Last data date; defaults to today"),
    days:int=typer.Option(7,"--days",min=1,max=90,help="Rolling data window"),
):
    """Export the bounded morning-brief dataset as one atomic CSV file."""
    _,db=context(); end_date=parse_date(end,"end") or date.today()
    with db.connect(read_only=True) as conn:
        count=export_brief_csv(conn,output,end_date,days)
    typer.echo(f"{count} rows -> {output}")


@app.command("discover-network")
def discover_network(source:str,browser_profile:Path|None=None):
    settings,_=context(); urls={"eex":"https://www.eex.com/en/market-data/market-data-hub","nordpool":"https://data.nordpoolgroup.com/"}
    if source not in urls: raise typer.BadParameter(f"supported: {','.join(urls)}")
    output=settings.root/"artifacts"/"network_discovery"/source; report=run_capture(urls[source],output,browser_profile)
    typer.echo(f"{len(report)} endpoints -> {output/'network_report.json'}")


@app.command("repair-gaps")
def repair_gaps(dataset:str|None=None,source:str|None=None,start:str|None=None,end:str|None=None,only_open:bool=True):
    settings,db=context(); clauses=["gap_status='OPEN'"] if only_open else []; params=[]
    start_date=parse_date(start,"start"); end_date=parse_date(end,"end")
    for col,val,op in (("dataset_id",dataset,"="),("source_attempted",source,"="),("expected_timestamp_utc",str(start_date) if start_date else None,">="),("expected_timestamp_utc",str(end_date) if end_date else None,"<=")):
        if val: clauses.append(f"{col}{op}?"); params.append(val)
    with db.connect() as conn: rows=conn.execute("SELECT dataset_id,series_key,delivery_date_local FROM expected_observations JOIN data_gaps USING(dataset_id,series_key,expected_timestamp_utc)"+(" WHERE "+" AND ".join(clauses) if clauses else ""),params).fetchall()
    typer.echo(json.dumps({"targeted_gaps":len(rows),"status":"Use source-specific collectors; unsupported datasets remain auditable"}))


@app.command("import-gap-fill")
def import_gap_fill(path:Path,dry_run:bool=typer.Option(True,"--dry-run/--commit")):
    _,db=context(); typer.echo(json.dumps(import_gap_csv(db,path,dry_run),indent=2))


@gaps_app.command("export-template")
def gap_template(output:Path=Path("reports/gap_fill_template.csv")):
    typer.echo(str(export_gap_template(output)))


@api_app.command("serve")
def api_serve(host:str="127.0.0.1",port:int=8765):
    if host not in {"127.0.0.1","localhost","::1"}: raise typer.BadParameter("Public binding is disabled; configure an authenticated HTTPS tunnel explicitly")
    import uvicorn
    uvicorn.run(create_app(Settings.load()),host=host,port=port)


@api_app.command("status")
def api_status():
    settings,_=context(); typer.echo(json.dumps({"bind_host":"127.0.0.1","port":8765,"db_exists":settings.db_path.exists(),"public_tunnel":False,"token_configured":bool(os.getenv("ENERGY_API_TOKEN"))},indent=2))
