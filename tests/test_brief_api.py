from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from energy_scraper.api.brief import data_status, intraday_contracts, parse_window
from energy_scraper.core.database import Database


def test_window_is_limited_to_ninety_days():
    assert tuple(map(str,parse_window("2026-06-04","2026-09-01"))) == ("2026-06-04","2026-09-01")
    with pytest.raises(HTTPException) as error:
        parse_window("2026-06-03","2026-09-01")
    assert error.value.status_code == 400


def test_status_reports_fresh_and_failures(tmp_path):
    db=Database(tmp_path/"brief.sqlite"); db.initialize(); now=datetime.now(UTC).isoformat()
    with db.connect() as conn:
        for source in ("eex","nordpool"):
            conn.execute("INSERT INTO scrape_runs(run_id,source_id,started_at,finished_at,status) VALUES(?,?,?,?,?)",(f"ok-{source}",source,now,now,"SUCCESS"))
        conn.execute("INSERT INTO scrape_runs(run_id,source_id,started_at,finished_at,status) VALUES(?,?,?,?,?)",("bad","eex",now,now,"FAILED"))
        result=data_status(conn)
    assert result["status"] == "fresh"
    assert result["counts"]["recent_failures"] == 1
    assert all(source["fresh"] for source in result["sources"])


def test_status_reports_stale(tmp_path):
    db=Database(tmp_path/"brief.sqlite"); db.initialize(); old=(datetime.now(UTC)-timedelta(hours=27)).isoformat()
    with db.connect() as conn:
        for source in ("eex","nordpool"):
            conn.execute("INSERT INTO scrape_runs(run_id,source_id,started_at,finished_at,status) VALUES(?,?,?,?,?)",(source,source,old,old,"SUCCESS"))
        assert data_status(conn)["status"] == "stale"


def test_intraday_keeps_latest_snapshot_per_day(tmp_path):
    db=Database(tmp_path/"brief.sqlite"); db.initialize()
    for suffix in ("a","b"):
        run=db.start_run("nordpool",suffix)
        db.finish_run(run,"SUCCESS")
        with db.connect() as conn:
            conn.execute("""INSERT INTO intraday_contract_stats(
              delivery_area,delivery_date,contract_id,contract_name,contract_type,resolution_minutes,
              vwap,source_update_time,price_unit,volume_unit,source_id,run_id,collected_at)
              VALUES('FR','2026-09-01','qh1','QH 1','QH',15,?,?,'EUR/MWh','MW','nordpool',?,?)""",
              (1 if suffix=="a" else 2,f"2026-09-01T0{1 if suffix=='a' else 2}:00:00Z",run,f"2026-09-01T0{1 if suffix=='a' else 2}:01:00Z"))
    with db.connect(read_only=True) as conn:
        result=intraday_contracts(conn,"2026-09-01","2026-09-01","FR","15")
    assert result["meta"]["row_count"] == 1
    assert result["data"][0]["vwap"] == 2


def test_intraday_rejects_unknown_resolution(tmp_path):
    db=Database(tmp_path/"brief.sqlite"); db.initialize()
    with db.connect(read_only=True) as conn, pytest.raises(HTTPException):
        intraday_contracts(conn,"2026-09-01","2026-09-01","FR","10")
