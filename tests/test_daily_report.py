from datetime import UTC, date, datetime, timedelta

from energy_scraper.core.daily_report import write_daily_reports
from energy_scraper.core.database import Database


def test_daily_report_is_readable_and_lists_missing_data(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    db.initialize()
    started = datetime(2026, 9, 10, 5, 15, tzinfo=UTC)
    finished = started + timedelta(minutes=1)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO scrape_runs(run_id,source_id,started_at,status) VALUES(?,?,?,'FAILED')",
            ("run-1", "epex", started.isoformat()),
        )
        conn.execute(
            "INSERT INTO collection_partitions VALUES(?,?,?,?,?,?)",
            ("epex_day_ahead_fr", "2026-09-10", "FAILED", finished.isoformat(), 0, "run-1"),
        )
        conn.execute(
            """INSERT INTO backfill_jobs(
                job_id,dataset_id,source_id,partition_key,status,last_attempt_at,
                records_received,error_message)
                VALUES(?,?,?,?,?,?,?,?)""",
            ("job-1", "epex_day_ahead_fr", "epex", "2026-09-10", "ACCESS_RESTRICTED",
             finished.isoformat(), 0, "403 Forbidden"),
        )
        conn.execute(
            """INSERT INTO data_gaps(
                gap_id,dataset_id,series_key,expected_timestamp_utc,expected_timestamp_local,
                detected_at,gap_status,source_attempted)
                VALUES(?,?,?,?,?,?,?,?)""",
            ("gap-1", "intraday", "FR:QH", "2026-09-09T00:00:00+00:00",
             "2026-09-09T02:00:00+02:00", started.isoformat(), "OPEN", "nordpool"),
        )

    payload = {
        "status": "PARTIAL",
        "results": {"epex": {"received": 0, "inserted": 0, "errors": 1}},
        "errors": {"epex": "1 partition(s) failed"},
    }
    paths = write_daily_reports(db, tmp_path, date(2026, 9, 10), payload, started, finished)

    log = paths["log"].read_text(encoding="utf-8")
    completeness = paths["completeness"].read_text(encoding="utf-8")
    assert "Statut: PARTIAL" in log
    assert "epex: ERREUR" in log
    assert "**Statut : INCOMPLET**" in completeness
    assert "epex_day_ahead_fr" in completeness
    assert "ACCESS_RESTRICTED" in completeness
    assert "403 Forbidden" in completeness
    assert "FR:QH" in completeness
    assert (tmp_path / "reports/completude.md").read_text(encoding="utf-8") == completeness


def test_daily_report_says_complete_when_nothing_is_missing(tmp_path):
    db = Database(tmp_path / "data.sqlite")
    db.initialize()
    now = datetime(2026, 9, 10, 5, 15, tzinfo=UTC)
    payload = {"status": "SUCCESS", "results": {"rte": {"received": 4, "inserted": 4}}, "errors": {}}

    paths = write_daily_reports(db, tmp_path, date(2026, 9, 10), payload, now, now)

    assert "**Statut : COMPLET**" in paths["completeness"].read_text(encoding="utf-8")
