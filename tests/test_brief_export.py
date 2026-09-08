import csv
from datetime import UTC, date, datetime

import pytest

from energy_scraper.core.brief_export import BRIEF_FIELDS, build_brief_rows, export_brief_csv
from energy_scraper.core.database import Database


def test_brief_export_contains_status_and_market_rows(tmp_path):
    db = Database(tmp_path / "brief.sqlite")
    db.initialize()
    now = datetime.now(UTC).isoformat()
    with db.connect() as conn:
        for source in ("eex", "nordpool"):
            conn.execute(
                "INSERT INTO scrape_runs(run_id,source_id,started_at,finished_at,status,records_received,records_inserted) VALUES(?,?,?,?,?,?,?)",
                (f"run-{source}", source, now, now, "SUCCESS", 1, 1),
            )
        conn.execute(
            "INSERT INTO instruments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("future-1", "EEX", "power", "FR", "Year Base", "F7BY", "base", "2027", None, None, "EUR", "EUR/MWh"),
        )
        conn.execute(
            """INSERT INTO market_prices(
                instrument_id,observation_timestamp,trading_date,price_type,settlement,
                source_id,run_id,data_type,quality_status)
                VALUES(?,?,?,?,?,?,?,?,?)""",
            ("future-1", now, "2026-09-08", "settlement", 72.5, "eex", "run-eex", "raw", "valid"),
        )
        conn.execute(
            """INSERT INTO intraday_contract_stats(
                delivery_area,delivery_date,contract_id,contract_name,contract_type,resolution_minutes,
                vwap,source_update_time,price_unit,volume_unit,source_id,run_id,collected_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("FR", "2026-09-08", "qh-1", "QH 1", "QH", 15, 68.2, now, "EUR/MWh", "MWh", "nordpool", "run-nordpool", now),
        )
        conn.execute(
            """INSERT INTO data_gaps(
                gap_id,dataset_id,series_key,expected_timestamp_utc,expected_timestamp_local,
                detected_at,gap_status,source_attempted,attempt_count)
                VALUES(?,?,?,?,?,?,?,?,?)""",
            ("gap-1", "intraday", "FR:QH", "2026-09-08T00:00:00Z", "2026-09-08T02:00:00+02:00", now, "OPEN", "nordpool", 1),
        )

    output = tmp_path / "morning_brief.csv"
    with db.connect(read_only=True) as conn:
        count = export_brief_csv(conn, output, date(2026, 9, 8))

    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert count == len(rows)
    assert set(BRIEF_FIELDS) == set(rows[0])
    assert {row["record_type"] for row in rows} == {"status", "source_status", "futures", "intraday", "gap"}
    futures = next(row for row in rows if row["record_type"] == "futures")
    assert futures["settlement"] == "72.5"
    assert futures["price_unit"] == "EUR/MWh"
    assert next(row for row in rows if row["record_type"] == "intraday")["vwap"] == "68.2"


def test_empty_database_still_exports_explicit_stale_status(tmp_path):
    db = Database(tmp_path / "brief.sqlite")
    db.initialize()
    output = tmp_path / "morning_brief.csv"
    with db.connect(read_only=True) as conn:
        export_brief_csv(conn, output, date(2026, 9, 8))
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["record_type"] == "status"
    assert rows[0]["overall_status"] == "stale"
    assert len(rows) == 3


@pytest.mark.parametrize("days", [0, 91])
def test_brief_window_must_be_bounded(tmp_path, days):
    db = Database(tmp_path / "brief.sqlite")
    db.initialize()
    with db.connect(read_only=True) as conn, pytest.raises(ValueError):
        build_brief_rows(conn, date(2026, 9, 8), days)


def test_failed_export_preserves_previous_file(tmp_path):
    db = Database(tmp_path / "brief.sqlite")
    db.initialize()
    output = tmp_path / "morning_brief.csv"
    output.write_text("previous-good-export\n", encoding="utf-8")
    with db.connect(read_only=True) as conn, pytest.raises(ValueError):
        export_brief_csv(conn, output, date(2026, 9, 8), days=91)
    assert output.read_text(encoding="utf-8") == "previous-good-export\n"
