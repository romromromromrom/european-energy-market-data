from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..api.brief import data_status, futures_settlements, gaps, intraday_contracts


BRIEF_FIELDS = [
    "record_type",
    "generated_at",
    "overall_status",
    "freshness_threshold_hours",
    "source",
    "source_last_success_at",
    "source_age_hours",
    "source_fresh",
    "records_received",
    "records_inserted",
    "latest_futures_date",
    "latest_intraday_date",
    "futures_count",
    "intraday_count",
    "open_gaps_count",
    "recent_failures_count",
    "data_date",
    "market_area",
    "product_code",
    "product",
    "maturity",
    "contract_id",
    "contract_name",
    "contract_type",
    "resolution_minutes",
    "delivery_start",
    "delivery_end",
    "settlement",
    "open",
    "high",
    "low",
    "close",
    "vwap",
    "vwap_1h",
    "vwap_3h",
    "volume",
    "buy_volume",
    "sell_volume",
    "currency",
    "price_unit",
    "volume_unit",
    "source_update_time",
    "collected_at",
    "quality_status",
    "dataset_id",
    "series_key",
    "expected_timestamp_utc",
    "missing_fields",
    "detected_at",
    "attempt_count",
    "last_error_class",
    "period_start", "period_end", "baseload_eur_mwh", "peakload_eur_mwh",
    "reconstructed_baseload_eur_mwh", "minimum_eur_mwh", "maximum_eur_mwh",
    "amplitude_eur_mwh", "tb2_eur_mwh", "tb4_eur_mwh", "direction",
    "reserve_type", "metric", "value", "unit", "source_field",
]


def build_brief_rows(conn: sqlite3.Connection, end_date: date, days: int = 7) -> list[dict[str, Any]]:
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")

    start_date = end_date - timedelta(days=days - 1)
    start = start_date.isoformat()
    end = end_date.isoformat()
    status = data_status(conn)
    common = {
        "generated_at": status["generated_at"],
        "overall_status": status["status"],
        "freshness_threshold_hours": status["freshness_threshold_hours"],
    }
    rows: list[dict[str, Any]] = [
        {
            **common,
            "record_type": "status",
            "latest_futures_date": status["latest_data_date"]["futures"],
            "latest_intraday_date": status["latest_data_date"]["intraday"],
            "futures_count": status["counts"]["futures"],
            "intraday_count": status["counts"]["intraday"],
            "open_gaps_count": status["counts"]["open_gaps"],
            "recent_failures_count": status["counts"]["recent_failures"],
        }
    ]
    rows.extend(
        {
            **common,
            "record_type": "source_status",
            "source": item["source"],
            "source_last_success_at": item["last_success_at"],
            "source_age_hours": item["age_hours"],
            "source_fresh": item["fresh"],
            "records_received": item["records_received"],
            "records_inserted": item["records_inserted"],
        }
        for item in status["sources"]
    )

    for item in futures_settlements(conn, start, end, None, None)["data"]:
        rows.append(
            {
                **common,
                **item,
                "record_type": "futures",
                "data_date": item["trading_date"],
                "price_unit": item["unit"],
            }
        )

    for item in intraday_contracts(conn, start, end, None, None)["data"]:
        rows.append(
            {
                **common,
                **item,
                "record_type": "intraday",
                "data_date": item["delivery_date"],
                "market_area": item["delivery_area"],
            }
        )

    for item in conn.execute("SELECT * FROM epex_day_ahead_prices WHERE delivery_date BETWEEN ? AND ? ORDER BY delivery_date,period_start",(start,end)):
        value=dict(item); rows.append({**common,**value,"record_type":"day_ahead","data_date":value["delivery_date"],"source":"EPEX Day-Ahead / SDAC — FR","volume":value["volume_mwh"],"buy_volume":value["buy_volume_mwh"],"sell_volume":value["sell_volume_mwh"],"price_unit":"EUR/MWh"})
    for item in conn.execute("SELECT * FROM epex_day_ahead_indices WHERE delivery_date BETWEEN ? AND ? ORDER BY delivery_date",(start,end)):
        value=dict(item); rows.append({**common,**value,"record_type":"day_ahead_index","data_date":value["delivery_date"],"source":"EPEX Day-Ahead / SDAC — FR Baseload","settlement":value["baseload_eur_mwh"],"price_unit":"EUR/MWh"})
    for item in conn.execute("SELECT * FROM rte_balancing_volumes WHERE delivery_date BETWEEN ? AND ? ORDER BY delivery_date,period_start,source_field,direction",(start,end)):
        value=dict(item); rows.append({**common,**value,"record_type":"rte_balancing_volume","data_date":value["delivery_date"],"source":"RTE Balancing"})

    for item in gaps(conn, None, start, end)["data"]:
        rows.append({**common, **item, "record_type": "gap", "source": item["source_attempted"]})

    return rows


def export_brief_csv(conn: sqlite3.Connection, output: Path, end_date: date, days: int = 7) -> int:
    rows = build_brief_rows(conn, end_date, days)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=BRIEF_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return len(rows)
