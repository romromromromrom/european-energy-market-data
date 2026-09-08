from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BackfillDataset:
    dataset_id: str
    source: str
    collector: str
    partitioning: str
    scope: str
    priority: int
    legal_class: str
    default_mode: str = "explicit"
    default_start: str | None = None


DATASETS: list[BackfillDataset] = [
    BackfillDataset("eex_fr_base_year", "EEX", "eex", "code+year", "FR Base Year settlements", 1, "public_api", "explicit", "2024-01-01"),
    BackfillDataset("eex_fr_peak_year", "EEX", "eex", "code+year", "FR Peak Year settlements", 1, "public_api", "explicit", "2024-01-01"),
    BackfillDataset("eex_ttf_year", "EEX", "eex", "code+year", "TTF Year settlements", 1, "public_api", "explicit", "2024-01-01"),
    BackfillDataset("eex_fr_base_month", "EEX", "eex", "code+maturity+window", "FR Base Month settlements", 1, "public_api", "skip"),
    BackfillDataset("eex_fr_base_quarter", "EEX", "eex", "code+maturity+window", "FR Base Quarter settlements", 1, "public_api", "skip"),
    BackfillDataset("eex_fr_peak_month", "EEX", "eex", "code+maturity+window", "FR Peak Month settlements", 1, "public_api", "skip"),
    BackfillDataset("eex_fr_peak_quarter", "EEX", "eex", "code+maturity+window", "FR Peak Quarter settlements", 1, "public_api", "skip"),
    BackfillDataset("epex_day_ahead_fr", "EPEX SPOT", "epex", "day", "FR SDAC 15-minute Day-Ahead", 1, "public_market_results", "skip"),
    BackfillDataset("rte_balancing_volumes_fr", "RTE", "rte_balancing", "day", "FR balancing volumes", 1, "open_data", "skip"),
    BackfillDataset("rte_balancing_prices_fr", "RTE", "rte_prices", "day", "FR reserve and imbalance prices", 1, "open_data", "skip"),
    BackfillDataset("nordpool_intraday_fr", "Nord Pool", "nordpool", "area+day", "FR intraday snapshots", 1, "restricted_private_use", "explicit", "2026-08-25"),
    BackfillDataset("nordpool_intraday_be", "Nord Pool", "nordpool", "area+day", "BE intraday snapshots", 1, "restricted_private_use", "explicit", "2026-08-25"),
    BackfillDataset("nordpool_intraday_de_lu", "Nord Pool", "nordpool", "area+day", "DE-LU intraday snapshots", 1, "restricted_private_use", "explicit", "2026-08-25"),
    BackfillDataset("day_ahead_fr", "ENTSO-E / Energy-Charts", "planned", "day", "FR day-ahead prices", 2, "planned_official_api", "skip"),
    BackfillDataset("day_ahead_de", "ENTSO-E / Energy-Charts", "planned", "day", "DE day-ahead prices", 2, "planned_official_api", "skip"),
    BackfillDataset("day_ahead_be", "ENTSO-E / Energy-Charts", "planned", "day", "BE day-ahead prices", 2, "planned_official_api", "skip"),
    BackfillDataset("power_state_fr", "RTE / ENTSO-E", "planned", "day", "FR power system state", 2, "planned_official_api", "skip"),
    BackfillDataset("power_state_de", "ENTSO-E", "planned", "day", "DE power system state", 2, "planned_official_api", "skip"),
    BackfillDataset("power_state_be", "ENTSO-E", "planned", "day", "BE power system state", 2, "planned_official_api", "skip"),
    BackfillDataset("interconnectors", "ENTSO-E / RTE", "planned", "day", "Interconnector flows", 3, "planned_official_api", "skip"),
    BackfillDataset("nuclear", "RTE / ENTSO-E", "planned", "day", "Nuclear production", 3, "planned_official_api", "skip"),
    BackfillDataset("hydro", "RTE / ENTSO-E", "planned", "day", "Hydro production", 3, "planned_official_api", "skip"),
    BackfillDataset("wind_solar", "RTE / ENTSO-E", "planned", "day", "Wind and solar production", 3, "planned_official_api", "skip"),
    BackfillDataset("gas_storage", "ENTSOG", "planned", "day", "Gas storage", 4, "planned_official_api", "skip"),
    BackfillDataset("gas_flows", "ENTSOG", "planned", "day", "Gas flows", 4, "planned_official_api", "skip"),
    BackfillDataset("tbx", "EPEX", "planned", "day", "TBX market data", 4, "license_review_required", "skip"),
]


def _default_entry(dataset: BackfillDataset) -> dict[str, Any]:
    start = dataset.default_start or "today"
    return {
        "dataset_id": dataset.dataset_id,
        "source": dataset.source,
        "collector": dataset.collector,
        "partitioning": dataset.partitioning,
        "scope": dataset.scope,
        "priority": dataset.priority,
        "legal_class": dataset.legal_class,
        "requested_start": start,
        "requested_end": "today",
        "mode": dataset.default_mode,
        "status": "SKIPPED" if dataset.default_mode == "skip" else "PENDING",
    }


def default_plan() -> dict[str, dict[str, Any]]:
    return {dataset.dataset_id: _default_entry(dataset) for dataset in DATASETS}


def run_wizard(path: Path, ask=input) -> dict[str, dict[str, Any]]:
    plan: dict[str, dict[str, Any]] = {}
    for dataset in DATASETS:
        while True:
            answer = ask(
                f"\n{dataset.dataset_id} — source: {dataset.source}; partitioning: {dataset.partitioning}; scope: {dataset.scope}\n"
                "Jusqu'à quelle date veux-tu tenter de remonter ? [YYYY-MM-DD|max|skip] "
            ).strip().lower()
            if answer in {"max", "skip"}:
                break
            try:
                date.fromisoformat(answer)
                break
            except ValueError:
                print("Réponse invalide.")
        plan[dataset.dataset_id] = {
            "dataset_id": dataset.dataset_id,
            "source": dataset.source,
            "collector": dataset.collector,
            "partitioning": dataset.partitioning,
            "scope": dataset.scope,
            "priority": dataset.priority,
            "legal_class": dataset.legal_class,
            "requested_start": answer,
            "requested_end": "today",
            "mode": "max" if answer == "max" else ("skip" if answer == "skip" else "explicit"),
            "status": "SKIPPED" if answer == "skip" else "PENDING",
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return plan


def validate_plan(path: Path) -> dict[str, dict[str, Any]]:
    plan = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(plan, dict):
        raise ValueError("backfill plan must be a mapping")
    normalized: dict[str, dict[str, Any]] = {}
    known = {dataset.dataset_id: dataset for dataset in DATASETS}
    for dataset_id, item in plan.items():
        if dataset_id not in known:
            raise ValueError(f"Unknown backfill dataset: {dataset_id}")
        if not isinstance(item, dict):
            raise ValueError(f"Invalid plan entry: {dataset_id}")
        mode = item.get("mode")
        if mode not in {"explicit", "max", "skip"}:
            raise ValueError(f"Invalid plan mode for {dataset_id}")
        if mode == "explicit":
            date.fromisoformat(str(item.get("requested_start")))
        dataset = known[dataset_id]
        normalized[dataset_id] = {
            "dataset_id": dataset_id,
            "source": item.get("source", dataset.source),
            "collector": item.get("collector", dataset.collector),
            "partitioning": item.get("partitioning", dataset.partitioning),
            "scope": item.get("scope", dataset.scope),
            "priority": int(item.get("priority", dataset.priority)),
            "legal_class": item.get("legal_class", dataset.legal_class),
            "requested_start": item.get("requested_start", dataset.default_start or "today"),
            "requested_end": item.get("requested_end", "today"),
            "mode": mode,
            "status": item.get("status", "SKIPPED" if mode == "skip" else "PENDING"),
        }
    for dataset_id, dataset in known.items():
        if dataset_id not in normalized:
            normalized[dataset_id] = _default_entry(dataset)
    return serialize_plan(normalized)


def serialize_plan(plan: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ordered = sorted(plan.values(), key=lambda item: (int(item.get("priority", 999)), item["dataset_id"]))
    return {item["dataset_id"]: item for item in ordered}
