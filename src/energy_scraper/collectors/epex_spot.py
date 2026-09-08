from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from energy_scraper.core.archive import archive_payload
from energy_scraper.core.calendars import local_intervals
from energy_scraper.core.database import Database, utcnow
from energy_scraper.core.error_classifier import classify_error
from energy_scraper.core.retry_policy import with_retry


DATASET = "epex_day_ahead_fr"


def _number(text: str) -> float:
    value = text.strip().replace("\xa0", "").replace(",", "")
    if value in {"", "-", "—", "N/A"}:
        raise ValueError("missing numeric EPEX value")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"invalid EPEX number: {text!r}") from exc


def extract_ajax(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, list):
        raise ValueError("EPEX AJAX response must be a command array")
    query_url = None
    html = None
    for command in payload:
        if not isinstance(command, dict):
            continue
        settings = command.get("settings")
        if isinstance(settings, dict) and settings.get("getQueryUrl"):
            query_url = settings["getQueryUrl"]
        if (command.get("command") == "invoke" and command.get("selector") == ".js-md-widget"
                and command.get("method") == "html" and command.get("args")):
            html = command["args"][0]
    if not isinstance(query_url, str):
        raise ValueError("EPEX getQueryUrl not found")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("EPEX market table HTML not found")
    return query_url, html


def _validate_query(query_url: str, delivery_day: date, trading_day: date | None) -> date:
    query = parse_qs(urlparse(query_url).query)
    required = {"market_area": "FR", "auction": "MRC", "modality": "Auction",
                "sub_modality": "DayAhead", "product": "15", "data_mode": "table",
                "delivery_date": delivery_day.isoformat()}
    for key, expected in required.items():
        if query.get(key) != [expected]:
            raise ValueError(f"EPEX query mismatch for {key}: expected {expected!r}")
    actual_trading = date.fromisoformat(query.get("trading_date", [""])[0])
    if trading_day is not None and actual_trading != trading_day:
        raise ValueError("EPEX trading_date mismatch")
    return actual_trading


def parse_epex_ajax(payload: Any, delivery_day: date, trading_day: date | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query_url, html = extract_ajax(payload)
    actual_trading = _validate_query(query_url, delivery_day, trading_day)
    soup = BeautifulSoup(html, "html.parser")
    labels = [a.get_text(" ", strip=True) for a in soup.select(".js-table-times li a")]
    table = soup.select_one(".js-table-values table")
    if table is None:
        raise ValueError("EPEX values table not found")
    value_rows = table.select("tbody tr")
    grid = local_intervals(delivery_day, 15)
    if len(grid) not in {92, 96, 100} or len(labels) != len(grid) or len(value_rows) != len(grid):
        raise ValueError(f"EPEX MTU mismatch: calendar={len(grid)}, labels={len(labels)}, rows={len(value_rows)}")
    rows: list[dict[str, Any]] = []
    for index, ((start_utc, start_local), tr) in enumerate(zip(grid, value_rows, strict=True)):
        cells = [cell.get_text(" ", strip=True) for cell in tr.select("td")]
        if len(cells) != 4:
            raise ValueError("EPEX value row must contain buy, sell, volume and price")
        end_utc = start_utc + timedelta(minutes=15)
        end_local = end_utc.astimezone(start_local.tzinfo)
        expected_label = f"{start_local:%H:%M} - {end_local:%H:%M}"
        if end_local.date() > start_local.date():
            expected_label = f"{start_local:%H:%M} - 24:00"
        normalized_label = re.sub(r"\s+", " ", labels[index]).strip()
        if normalized_label != expected_label:
            raise ValueError(f"EPEX period mismatch at {index}: {normalized_label!r} != {expected_label!r}")
        rows.append({"period_start": start_local.isoformat(), "period_end": end_local.isoformat(),
                     "buy_volume_mwh": _number(cells[0]), "sell_volume_mwh": _number(cells[1]),
                     "volume_mwh": _number(cells[2]), "price_eur_mwh": _number(cells[3])})
    indices: dict[str, float] = {}
    for tr in table.select("thead tr.flex-row"):
        cells = tr.select("th")
        if len(cells) >= 2:
            indices[cells[0].get_text(" ", strip=True).lower()] = _number(cells[1].get_text(" ", strip=True))
    if "baseload" not in indices:
        raise ValueError("EPEX published Baseload is missing")
    prices = [row["price_eur_mwh"] for row in rows]
    reconstructed = sum(prices) / len(prices)
    difference = reconstructed - indices["baseload"]
    if abs(difference) > 0.02:
        raise ValueError(f"EPEX Baseload quality check failed: difference={difference:.6f}")
    ordered = sorted(prices)
    update = soup.select_one(".last-update")
    result = {"trading_date": actual_trading.isoformat(), "delivery_date": delivery_day.isoformat(),
              "baseload_eur_mwh": indices["baseload"], "peakload_eur_mwh": indices.get("peakload"),
              "reconstructed_baseload_eur_mwh": reconstructed, "baseload_difference_eur_mwh": difference,
              "minimum_eur_mwh": min(prices), "maximum_eur_mwh": max(prices),
              "amplitude_eur_mwh": max(prices) - min(prices),
              "tb2_eur_mwh": sum(ordered[-8:]) / 8 - sum(ordered[:8]) / 8,
              "tb4_eur_mwh": sum(ordered[-16:]) / 16 - sum(ordered[:16]) / 16,
              "source_update_time": update.get_text(" ", strip=True).removeprefix("Last update:").strip() if update else None}
    return rows, result


class EpexSpotCollector:
    def __init__(self, settings: Any, db: Database):
        self.settings, self.db = settings, db
        self.url = settings.sources()["epex"]["base_url"]

    def collect(self, start: date, end: date, force: bool = False) -> dict[str, int]:
        totals = {"partitions": 0, "received": 0, "inserted": 0, "errors": 0}
        day = start
        while day <= end:
            partition = day.isoformat()
            with self.db.connect() as conn:
                complete = conn.execute("SELECT 1 FROM collection_partitions WHERE dataset_id=? AND partition_key=? AND status='SUCCESS'", (DATASET, partition)).fetchone()
            if force or not complete:
                totals["partitions"] += 1
                try:
                    received, inserted = self._collect_day(day, partition)
                    totals["received"] += received; totals["inserted"] += inserted
                except Exception as exc:
                    totals["errors"] += 1; self._failed(partition, day, exc)
            day += timedelta(days=1)
        return totals

    def _collect_day(self, day: date, partition: str) -> tuple[int, int]:
        run_id = self.db.start_run("epex", self.url)
        try:
            with httpx.Client(timeout=self.settings.timeout, follow_redirects=True, headers={"accept": "text/html,application/json"}) as client:
                initial = with_retry(lambda: client.get(self.url), self.settings.max_attempts); initial.raise_for_status()
                soup = BeautifulSoup(initial.text, "html.parser")
                form = soup.select_one("form input[name=form_build_id]")
                if form is None or not form.get("value"):
                    raise ValueError("EPEX form_build_id not found")
                trading = day - timedelta(days=1)
                data = {"form_build_id": form["value"], "form_id": "market_data_filters_form",
                        "filters[market_area]": "FR", "filters[auction]": "MRC", "filters[modality]": "Auction",
                        "filters[sub_modality]": "DayAhead", "filters[product]": "15", "filters[data_mode]": "table",
                        "filters[trading_date]": trading.isoformat(), "filters[delivery_date]": day.isoformat(),
                        "triggered_element": "filters[delivery_date]", "_triggering_element_name": "submit_js"}
                response = with_retry(lambda: client.post(self.url + "?ajax_form=1&_wrapper_format=html&_wrapper_format=drupal_ajax", data=data), self.settings.max_attempts)
                response.raise_for_status(); payload = response.json()
            _, digest = archive_payload(self.settings.raw_dir, "epex", payload, {"delivery_date": partition})
            rows, index = parse_epex_ajax(payload, day, trading)
            collected = utcnow()
            with self.db.transaction() as conn:
                before = conn.total_changes
                conn.executemany("""INSERT OR IGNORE INTO epex_day_ahead_prices
                  (source_id,market_area,market,modality,sub_modality,product,trading_date,delivery_date,period_start,period_end,timezone,
                   price_eur_mwh,buy_volume_mwh,sell_volume_mwh,volume_mwh,source_update_time,collected_at,run_id,raw_payload_hash,quality_status)
                  VALUES('epex','FR','SDAC','Auction','DayAhead','15min',?,?,?,?,'Europe/Paris',?,?,?,?,?,?,?,?,'valid')""",
                  [(index["trading_date"], partition, r["period_start"], r["period_end"], r["price_eur_mwh"], r["buy_volume_mwh"], r["sell_volume_mwh"], r["volume_mwh"], index["source_update_time"], collected, run_id, digest) for r in rows])
                conn.execute("""INSERT OR IGNORE INTO epex_day_ahead_indices
                  (source_id,market_area,trading_date,delivery_date,baseload_eur_mwh,peakload_eur_mwh,reconstructed_baseload_eur_mwh,
                   baseload_difference_eur_mwh,minimum_eur_mwh,maximum_eur_mwh,amplitude_eur_mwh,tb2_eur_mwh,tb4_eur_mwh,
                   source_update_time,collected_at,run_id,raw_payload_hash,quality_status) VALUES('epex','FR',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'valid')""",
                  tuple(index[k] for k in ("trading_date","delivery_date","baseload_eur_mwh","peakload_eur_mwh","reconstructed_baseload_eur_mwh","baseload_difference_eur_mwh","minimum_eur_mwh","maximum_eur_mwh","amplitude_eur_mwh","tb2_eur_mwh","tb4_eur_mwh","source_update_time")) + (collected,run_id,digest))
                inserted = conn.total_changes - before
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)", (DATASET,partition,"SUCCESS",collected,len(rows),run_id))
            self.db.finish_run(run_id,"SUCCESS",len(rows)+1,inserted,payload_hash=digest)
            return len(rows)+1, inserted
        except Exception as exc:
            self.db.finish_run(run_id,"FAILED",error=str(exc)); raise

    def _failed(self, partition: str, day: date, exc: Exception) -> None:
        kind, status = classify_error(exc); now = utcnow()
        with self.db.connect() as conn:
            run = conn.execute("SELECT run_id FROM scrape_runs WHERE source_id='epex' ORDER BY started_at DESC LIMIT 1").fetchone()
            if run:
                conn.execute("INSERT OR REPLACE INTO collection_partitions VALUES(?,?,?,?,?,?)",(DATASET,partition,"FAILED",now,0,run[0]))
            conn.execute("""INSERT INTO backfill_jobs(job_id,dataset_id,source_id,partition_key,requested_start,requested_end,chunk_start,chunk_end,attempt_count,status,first_attempt_at,last_attempt_at,error_class,http_status,error_message,next_action)
              VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?) ON CONFLICT(dataset_id,partition_key,chunk_start,chunk_end) DO UPDATE SET attempt_count=attempt_count+1,status=excluded.status,last_attempt_at=excluded.last_attempt_at,error_class=excluded.error_class,http_status=excluded.http_status,error_message=excluded.error_message""",
              (str(uuid.uuid4()),DATASET,"epex",partition,str(day),str(day),str(day),str(day),kind,now,now,kind,status,str(exc)[:2000],"repair-gaps"))
