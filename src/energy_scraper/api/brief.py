from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException


MAX_WINDOW_DAYS = 90
FRESHNESS_HOURS = 26


def parse_window(start: str | None, end: str | None) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    try:
        end_date = date.fromisoformat(end) if end else today
        start_date = date.fromisoformat(start) if start else end_date - timedelta(days=6)
    except ValueError as exc:
        raise HTTPException(400, "Dates must use YYYY-MM-DD") from exc
    if start_date > end_date:
        raise HTTPException(400, "start must be <= end")
    if (end_date - start_date).days + 1 > MAX_WINDOW_DAYS:
        raise HTTPException(400, f"Date window cannot exceed {MAX_WINDOW_DAYS} days")
    return start_date, end_date


def _csv_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def data_status(conn: sqlite3.Connection) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    sources: list[dict[str, Any]] = []
    fresh_count = 0
    for source_id in ("eex", "nordpool"):
        row = conn.execute(
            """SELECT finished_at, status, records_received, records_inserted
               FROM scrape_runs
               WHERE source_id=? AND status IN ('SUCCESS','SUCCESS_EMPTY')
               ORDER BY finished_at DESC LIMIT 1""",
            (source_id,),
        ).fetchone()
        last_success = row["finished_at"] if row else None
        age_hours = None
        is_fresh = False
        if last_success:
            parsed = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            age_hours = round((now - parsed.astimezone(UTC)).total_seconds() / 3600, 2)
            is_fresh = age_hours <= FRESHNESS_HOURS
        fresh_count += int(is_fresh)
        sources.append(
            {
                "source": source_id,
                "last_success_at": last_success,
                "age_hours": age_hours,
                "fresh": is_fresh,
                "records_received": row["records_received"] if row else 0,
                "records_inserted": row["records_inserted"] if row else 0,
            }
        )

    latest = {
        "futures": conn.execute("SELECT MAX(trading_date) FROM market_prices").fetchone()[0],
        "intraday": conn.execute("SELECT MAX(delivery_date) FROM intraday_contract_stats").fetchone()[0],
    }
    counts = {
        "futures": conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0],
        "intraday": conn.execute("SELECT COUNT(*) FROM intraday_contract_stats").fetchone()[0],
        "open_gaps": conn.execute("SELECT COUNT(*) FROM data_gaps WHERE gap_status='OPEN'").fetchone()[0],
        "recent_failures": conn.execute(
            "SELECT COUNT(*) FROM scrape_runs WHERE status='FAILED' AND started_at>=?",
            (cutoff.isoformat(),),
        ).fetchone()[0],
    }
    overall = "fresh" if fresh_count == len(sources) else ("partial" if fresh_count else "stale")
    return {
        "generated_at": now.isoformat(),
        "freshness_threshold_hours": FRESHNESS_HOURS,
        "status": overall,
        "latest_data_date": latest,
        "counts": counts,
        "sources": sources,
    }


def futures_settlements(
    conn: sqlite3.Connection,
    start: str | None,
    end: str | None,
    product_codes: str | None,
    maturities: str | None,
) -> dict[str, Any]:
    start_date, end_date = parse_window(start, end)
    where = ["p.trading_date BETWEEN ? AND ?"]
    params: list[Any] = [str(start_date), str(end_date)]
    codes = _csv_values(product_codes)
    selected_maturities = _csv_values(maturities)
    if codes:
        where.append(f"i.product_code IN ({_placeholders(codes)})")
        params.extend(codes)
    if selected_maturities:
        where.append(f"i.maturity IN ({_placeholders(selected_maturities)})")
        params.extend(selected_maturities)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""SELECT p.trading_date, i.product_code, i.product, i.maturity,
                       i.market_area, p.settlement, i.currency, i.unit,
                       p.volume, p.open_interest, p.quality_status,
                       s.source_name AS source, p.observation_timestamp AS collected_at
                FROM market_prices p
                JOIN instruments i USING(instrument_id)
                JOIN sources s ON p.source_id=s.source_id
                WHERE {' AND '.join(where)}
                ORDER BY p.trading_date, i.product_code, i.maturity""",
            params,
        )
    ]
    return _envelope("futures_settlements", start_date, end_date, rows)


def intraday_contracts(
    conn: sqlite3.Connection,
    start: str | None,
    end: str | None,
    areas: str | None,
    resolutions: str | None,
) -> dict[str, Any]:
    start_date, end_date = parse_window(start, end)
    where = ["delivery_date BETWEEN ? AND ?"]
    params: list[Any] = [str(start_date), str(end_date)]
    selected_areas = _csv_values(areas)
    if selected_areas:
        where.append(f"delivery_area IN ({_placeholders(selected_areas)})")
        params.extend(selected_areas)
    selected_resolutions: list[int] = []
    for value in _csv_values(resolutions):
        try:
            resolution = int(value)
        except ValueError as exc:
            raise HTTPException(400, "resolutions must be comma-separated integers") from exc
        if resolution not in {15, 30, 60}:
            raise HTTPException(400, "resolutions must contain only 15, 30 or 60")
        selected_resolutions.append(resolution)
    if selected_resolutions:
        where.append(f"resolution_minutes IN ({_placeholders(selected_resolutions)})")
        params.extend(selected_resolutions)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY source_id, delivery_area, delivery_date, contract_id
                        ORDER BY source_update_time DESC, collected_at DESC
                    ) AS rank
                    FROM intraday_contract_stats
                    WHERE {' AND '.join(where)}
                )
                SELECT delivery_date, delivery_area, contract_id, contract_name,
                       contract_type, resolution_minutes, delivery_start, delivery_end,
                       open, high, low, close, vwap, vwap_1h, vwap_3h,
                       volume, buy_volume, sell_volume, price_unit, volume_unit,
                       source_id AS source, source_update_time, collected_at, quality_status
                FROM ranked WHERE rank=1
                ORDER BY delivery_date, delivery_area, delivery_start, contract_id""",
            params,
        )
    ]
    return _envelope("intraday_contracts", start_date, end_date, rows)


def gaps(
    conn: sqlite3.Connection,
    dataset: str | None,
    start: str | None,
    end: str | None,
) -> dict[str, Any]:
    start_date, end_date = parse_window(start, end)
    where = ["date(expected_timestamp_utc) BETWEEN ? AND ?", "gap_status='OPEN'"]
    params: list[Any] = [str(start_date), str(end_date)]
    if dataset:
        where.append("dataset_id=?")
        params.append(dataset)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""SELECT dataset_id, series_key, expected_timestamp_utc,
                       missing_fields, detected_at, source_attempted,
                       attempt_count, last_error_class
                FROM data_gaps WHERE {' AND '.join(where)}
                ORDER BY expected_timestamp_utc LIMIT 5000""",
            params,
        )
    ]
    return _envelope("data_gaps", start_date, end_date, rows)


def _envelope(name: str, start: date, end: date, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {
            "dataset": name,
            "start": str(start),
            "end": str(end),
            "row_count": len(rows),
            "timezone": "UTC with local delivery fields where supplied by the source",
        },
        "data": rows,
    }
