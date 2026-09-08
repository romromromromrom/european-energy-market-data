from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL, source_url TEXT NOT NULL,
 source_type TEXT NOT NULL, license_notes TEXT, access_method TEXT NOT NULL,
 primary_source INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS scrape_runs (
 run_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
 started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, request_url TEXT,
 records_received INTEGER NOT NULL DEFAULT 0, records_inserted INTEGER NOT NULL DEFAULT 0,
 records_rejected INTEGER NOT NULL DEFAULT 0, error_message TEXT, payload_hash TEXT
);
CREATE TABLE IF NOT EXISTS backfill_jobs (
 job_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, source_id TEXT NOT NULL,
 partition_key TEXT NOT NULL, requested_start TEXT, requested_end TEXT, chunk_start TEXT,
 chunk_end TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 first_attempt_at TEXT, last_attempt_at TEXT, records_received INTEGER DEFAULT 0,
 error_class TEXT, http_status INTEGER, error_message TEXT, next_action TEXT,
 UNIQUE(dataset_id, partition_key, chunk_start, chunk_end)
);
CREATE TABLE IF NOT EXISTS backfill_executions (
 execution_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, trigger_source TEXT NOT NULL, datasets_total INTEGER NOT NULL DEFAULT 0,
 datasets_completed INTEGER NOT NULL DEFAULT 0, records_received INTEGER NOT NULL DEFAULT 0,
 records_inserted INTEGER NOT NULL DEFAULT 0, error_message TEXT
);
CREATE TABLE IF NOT EXISTS historical_limits (
 dataset_id TEXT NOT NULL, series_key TEXT NOT NULL, earliest_available_date TEXT,
 first_unavailable_date TEXT, detection_method TEXT NOT NULL, detected_at TEXT NOT NULL,
 evidence TEXT, PRIMARY KEY(dataset_id, series_key)
);
CREATE TABLE IF NOT EXISTS expected_observations (
 dataset_id TEXT NOT NULL, series_key TEXT NOT NULL, expected_timestamp_utc TEXT NOT NULL,
 expected_timestamp_local TEXT NOT NULL, delivery_date_local TEXT NOT NULL,
 resolution_minutes INTEGER, expectation_rule TEXT NOT NULL, calendar_version TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(dataset_id, series_key, expected_timestamp_utc)
);
CREATE TABLE IF NOT EXISTS data_gaps (
 gap_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, series_key TEXT NOT NULL,
 expected_timestamp_utc TEXT NOT NULL, expected_timestamp_local TEXT NOT NULL,
 missing_fields TEXT, detected_at TEXT NOT NULL, gap_status TEXT NOT NULL,
 source_attempted TEXT, attempt_count INTEGER DEFAULT 0, last_http_status INTEGER,
 last_error_class TEXT, last_error_message TEXT, manual_fill_allowed INTEGER DEFAULT 1,
 resolved_at TEXT, resolution_method TEXT, resolved_by_source TEXT,
 UNIQUE(dataset_id, series_key, expected_timestamp_utc)
);
CREATE TABLE IF NOT EXISTS instruments (
 instrument_id TEXT PRIMARY KEY, exchange TEXT NOT NULL, commodity TEXT NOT NULL,
 market_area TEXT NOT NULL, product TEXT NOT NULL, product_code TEXT NOT NULL,
 profile TEXT, maturity TEXT NOT NULL, delivery_start TEXT, delivery_end TEXT,
 currency TEXT, unit TEXT, UNIQUE(exchange, product_code, maturity)
);
CREATE TABLE IF NOT EXISTS market_prices (
 observation_id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
 observation_timestamp TEXT NOT NULL, trading_date TEXT NOT NULL, price_type TEXT NOT NULL,
 price REAL, bid REAL, ask REAL, open REAL, high REAL, low REAL, settlement REAL,
 volume REAL, open_interest REAL, traded_volume_mwh REAL,
 gross_open_interest_contracts REAL, gross_open_interest_mwh REAL,
 net_open_interest_contracts REAL, net_open_interest_mwh REAL,
 source_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES scrape_runs(run_id),
 data_type TEXT NOT NULL DEFAULT 'raw', quality_status TEXT NOT NULL DEFAULT 'valid',
 UNIQUE(instrument_id, trading_date, price_type, source_id)
);
CREATE TABLE IF NOT EXISTS ticker_snapshots (
 snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id TEXT NOT NULL,
 source_update_time TEXT, collected_at TEXT NOT NULL, settlement REAL, currency TEXT,
 diff_settlement REAL, no_previous_value_found INTEGER, raw_payload_hash TEXT,
 source_id TEXT NOT NULL, run_id TEXT NOT NULL,
 UNIQUE(instrument_id, source_update_time, collected_at)
);
CREATE TABLE IF NOT EXISTS intraday_contract_stats (
 snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_area TEXT NOT NULL,
 delivery_date TEXT NOT NULL, delivery_start TEXT, delivery_end TEXT, contract_id TEXT NOT NULL,
 contract_name TEXT, contract_type TEXT, resolution_minutes INTEGER, is_local_contract INTEGER,
 contract_open_time TEXT, contract_close_time TEXT, open REAL, high REAL, low REAL, close REAL,
 vwap REAL, vwap_1h REAL, vwap_3h REAL, volume REAL, buy_volume REAL, sell_volume REAL,
 first_trade_time TEXT, last_trade_time TEXT, source_update_time TEXT NOT NULL,
 price_unit TEXT, volume_unit TEXT, source_id TEXT NOT NULL, run_id TEXT NOT NULL,
 collected_at TEXT NOT NULL, data_type TEXT NOT NULL DEFAULT 'raw',
 quality_status TEXT NOT NULL DEFAULT 'valid', raw_payload_hash TEXT,
 UNIQUE(source_id, delivery_area, contract_id, source_update_time)
);
CREATE TABLE IF NOT EXISTS epex_day_ahead_prices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, market_area TEXT NOT NULL,
 market TEXT NOT NULL, modality TEXT NOT NULL, sub_modality TEXT NOT NULL, product TEXT NOT NULL,
 trading_date TEXT NOT NULL, delivery_date TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
 timezone TEXT NOT NULL, price_eur_mwh REAL, buy_volume_mwh REAL, sell_volume_mwh REAL, volume_mwh REAL,
 source_update_time TEXT, collected_at TEXT NOT NULL, run_id TEXT NOT NULL, raw_payload_hash TEXT,
 quality_status TEXT NOT NULL DEFAULT 'valid', UNIQUE(source_id,market_area,delivery_date,period_start)
);
CREATE TABLE IF NOT EXISTS epex_day_ahead_indices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, market_area TEXT NOT NULL,
 trading_date TEXT NOT NULL, delivery_date TEXT NOT NULL, baseload_eur_mwh REAL, peakload_eur_mwh REAL,
 reconstructed_baseload_eur_mwh REAL, baseload_difference_eur_mwh REAL, minimum_eur_mwh REAL,
 maximum_eur_mwh REAL, amplitude_eur_mwh REAL, tb2_eur_mwh REAL, tb4_eur_mwh REAL,
 source_update_time TEXT, collected_at TEXT NOT NULL, run_id TEXT NOT NULL, raw_payload_hash TEXT,
 quality_status TEXT NOT NULL DEFAULT 'valid', UNIQUE(source_id,market_area,delivery_date)
);
CREATE TABLE IF NOT EXISTS rte_balancing_volumes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_date TEXT NOT NULL, period_start TEXT NOT NULL,
 period_end TEXT NOT NULL, timezone TEXT NOT NULL, direction TEXT, reserve_type TEXT, metric TEXT NOT NULL DEFAULT 'volume',
 value REAL, unit TEXT, source_field TEXT NOT NULL, source_update_time TEXT, collected_at TEXT NOT NULL,
 source_id TEXT NOT NULL, run_id TEXT NOT NULL, raw_payload_hash TEXT, quality_status TEXT NOT NULL DEFAULT 'valid',
 UNIQUE(source_id,delivery_date,period_start,direction,reserve_type,metric,source_field)
);
CREATE TABLE IF NOT EXISTS collection_partitions (
 dataset_id TEXT NOT NULL, partition_key TEXT NOT NULL, status TEXT NOT NULL,
 collected_at TEXT NOT NULL, records_received INTEGER NOT NULL, run_id TEXT NOT NULL,
 PRIMARY KEY(dataset_id, partition_key)
);
CREATE TABLE IF NOT EXISTS power_system_state (
 timestamp TEXT, country TEXT, load_mw REAL, nuclear_mw REAL, hydro_mw REAL,
 wind_onshore_mw REAL, wind_offshore_mw REAL, solar_mw REAL, gas_mw REAL, coal_mw REAL,
 biomass_mw REAL, imports_mw REAL, exports_mw REAL, residual_load_mw REAL, source_id TEXT
);
CREATE TABLE IF NOT EXISTS interconnector_flows (
 timestamp TEXT, from_area TEXT, to_area TEXT, physical_flow_mw REAL,
 scheduled_exchange_mw REAL, available_capacity_mw REAL, source_id TEXT
);
CREATE TABLE IF NOT EXISTS gas_storage (
 gas_day TEXT, country TEXT, stock_twh REAL, capacity_twh REAL, fill_pct REAL,
 injection_gwh REAL, withdrawal_gwh REAL, eu_stock_twh REAL, eu_capacity_twh REAL,
 eu_fill_pct REAL, country_share_eu_stock_pct REAL, source_id TEXT
);
CREATE TABLE IF NOT EXISTS gas_flows (
 gas_day TEXT, point_name TEXT, from_area TEXT, to_area TEXT, physical_flow_gwh_day REAL,
 technical_capacity_gwh_day REAL, utilization_pct REAL, source_id TEXT
);
CREATE TABLE IF NOT EXISTS flexibility_metrics (
 observation_date TEXT, company_or_market TEXT, technology TEXT, metric TEXT,
 value REAL, unit TEXT, geography TEXT, source_url TEXT, source_type TEXT
);
CREATE TABLE IF NOT EXISTS tbx_market_data (
 trading_date TEXT, market_area TEXT, product TEXT, product_type TEXT, maturity TEXT,
 price REAL, currency TEXT, source_id TEXT
);
CREATE TABLE IF NOT EXISTS news_fundamentals (
 published_at TEXT, source TEXT, author TEXT, url TEXT, topic TEXT, geography TEXT,
 headline TEXT, metric_text TEXT, market_impact TEXT
);
CREATE TABLE IF NOT EXISTS manual_gap_fills (
 fill_id TEXT PRIMARY KEY, gap_id TEXT NOT NULL, dataset_id TEXT NOT NULL, series_key TEXT NOT NULL,
 timestamp TEXT NOT NULL, field TEXT NOT NULL, value REAL, unit TEXT NOT NULL,
 source_note TEXT, original_filename TEXT NOT NULL, file_hash TEXT NOT NULL,
 imported_at TEXT NOT NULL, provenance TEXT NOT NULL DEFAULT 'manual_csv'
);
CREATE INDEX IF NOT EXISTS ix_market_prices_date ON market_prices(trading_date, instrument_id);
CREATE INDEX IF NOT EXISTS ix_intraday_area_date ON intraday_contract_stats(delivery_area, delivery_date);
CREATE INDEX IF NOT EXISTS ix_epex_date ON epex_day_ahead_prices(market_area,delivery_date);
CREATE INDEX IF NOT EXISTS ix_rte_balancing_date ON rte_balancing_volumes(delivery_date);
CREATE INDEX IF NOT EXISTS ix_gaps_query ON data_gaps(dataset_id, gap_status, expected_timestamp_utc);
CREATE INDEX IF NOT EXISTS ix_runs_started ON scrape_runs(started_at);
CREATE INDEX IF NOT EXISTS ix_backfill_executions_started ON backfill_executions(started_at);
CREATE VIEW IF NOT EXISTS latest_intraday_snapshots AS
 SELECT i.* FROM intraday_contract_stats i JOIN (
  SELECT delivery_area, contract_id, MAX(source_update_time) AS max_update
  FROM intraday_contract_stats GROUP BY delivery_area, contract_id
 ) x ON i.delivery_area=x.delivery_area AND i.contract_id=x.contract_id AND i.source_update_time=x.max_update;
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executemany(
                "INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)",
                [
                    ("eex", "EEX Market Data", "https://api.eex-group.com/pub/market-data", "exchange_api", "Public endpoint; verify EEX terms before redistribution.", "direct_api", 1),
                    ("nordpool", "Nord Pool Data Portal", "https://dataportal-api.nordpoolgroup.com/api/IntradayMarketStatistics", "exchange_api", "Use subject to Nord Pool data terms; private historical use only.", "direct_api", 1),
                    ("epex", "EPEX SPOT Market Results", "https://www.epexspot.com/en/market-results", "exchange_web", "Public market-results page; redistribution remains subject to EPEX SPOT terms.", "drupal_ajax", 1),
                    ("rte", "RTE Balancing Volumes", "https://www.services-rte.com/cms/open_data/v1/balancing_volumes_prices/volumes/table", "public_api", "RTE open-data terms apply; preserve source attribution.", "direct_api", 1),
                ],
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def start_run(self, source: str, url: str) -> str:
        run_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute("INSERT INTO scrape_runs(run_id,source_id,started_at,status,request_url) VALUES(?,?,?,?,?)", (run_id, source, utcnow(), "RUNNING", url))
        return run_id

    def finish_run(self, run_id: str, status: str, received: int = 0, inserted: int = 0,
                   rejected: int = 0, error: str | None = None, payload_hash: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("""UPDATE scrape_runs SET finished_at=?,status=?,records_received=?,records_inserted=?,
                         records_rejected=?,error_message=?,payload_hash=? WHERE run_id=?""",
                         (utcnow(), status, received, inserted, rejected, error, payload_hash, run_id))

    @staticmethod
    def insert_many(conn: sqlite3.Connection, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        before = conn.total_changes
        conn.executemany(sql, rows)
        return conn.total_changes - before
