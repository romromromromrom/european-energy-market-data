from pathlib import Path

from fastapi.testclient import TestClient

from energy_scraper.api.app import create_app
from energy_scraper.core.config import Settings
from energy_scraper.core.database import Database


def settings(tmp_path:Path)->Settings:
    root=Path(__file__).parents[1]
    return Settings(root=root,db_path=tmp_path/"test.sqlite",raw_dir=tmp_path/"raw",parquet_dir=tmp_path/"parquet",sources_path=root/"config"/"sources.yaml",backfill_plan_path=tmp_path/"plan.yaml")


def test_database_and_health(tmp_path):
    cfg=settings(tmp_path); Database(cfg.db_path).initialize()
    with TestClient(create_app(cfg)) as client:
        response=client.get("/health")
        assert response.status_code==200 and response.json()["db_readable"] is True


def test_api_null_and_pagination_and_injection(tmp_path):
    cfg=settings(tmp_path); db=Database(cfg.db_path); db.initialize()
    run=db.start_run("nordpool","x")
    with db.connect() as conn:
        conn.execute("""INSERT INTO intraday_contract_stats(delivery_area,delivery_date,contract_id,contract_name,contract_type,resolution_minutes,
          open,high,low,close,vwap,vwap_1h,vwap_3h,source_update_time,price_unit,volume_unit,source_id,run_id,collected_at)
          VALUES('FR','2026-08-25','qh1','QH 1','QH',15,1,3,1,2,2,NULL,NULL,'2026-08-25T12:00:00Z','EUR/MWh','MW','nordpool',?,'2026-08-25T12:01:00Z')""",(run,))
    with TestClient(create_app(cfg)) as client:
        body=client.get("/v1/intraday/contracts",params={"area":"FR","limit":1}).json()
        assert body["data"][0]["vwap_1h"] is None
        assert client.get("/v1/series",params={"dataset":"futures; DROP TABLE sources"}).status_code==400
        assert client.get("/v1/series",params={"dataset":"intraday","area":"FR' OR 1=1 --"}).json()["data"]==[]
        assert client.get("/v1/series",params={"dataset":"intraday","maturity":"CAL-2027"}).status_code==400


def test_unavailable_database(tmp_path):
    cfg=settings(tmp_path)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/health").status_code==503


def test_brief_status_route(tmp_path):
    cfg=settings(tmp_path); Database(cfg.db_path).initialize()
    with TestClient(create_app(cfg)) as client:
        response=client.get("/v1/brief/status")
    assert response.status_code == 200
    assert response.json()["status"] == "stale"


def test_required_api_token(monkeypatch,tmp_path):
    cfg=settings(tmp_path); Database(cfg.db_path).initialize()
    monkeypatch.setenv("ENERGY_API_REQUIRE_TOKEN","true")
    monkeypatch.delenv("ENERGY_API_TOKEN",raising=False)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/v1/brief/status").status_code == 503
    monkeypatch.setenv("ENERGY_API_TOKEN","secret")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/v1/brief/status").status_code == 401
        assert client.get("/v1/brief/status",headers={"Authorization":"Bearer secret"}).status_code == 200
