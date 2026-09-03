from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    db_path: Path = ROOT / "data" / "power_europe_market_history.sqlite"
    raw_dir: Path = ROOT / "data" / "raw"
    parquet_dir: Path = ROOT / "data" / "parquet"
    sources_path: Path = ROOT / "config" / "sources.yaml"
    backfill_plan_path: Path = ROOT / "config" / "backfill_plan.yaml"
    timeout: float = 30.0
    max_attempts: int = 3
    user_agent: str = "EuropeanEnergyMarketData/0.1 (+local research collector)"

    @classmethod
    def load(cls) -> "Settings":
        root = Path(os.getenv("ENERGY_SCRAPER_ROOT", ROOT)).resolve()
        return cls(
            root=root,
            db_path=Path(os.getenv("ENERGY_DB_PATH", root / "data" / "power_europe_market_history.sqlite")),
            raw_dir=Path(os.getenv("ENERGY_RAW_DIR", root / "data" / "raw")),
            parquet_dir=Path(os.getenv("ENERGY_PARQUET_DIR", root / "data" / "parquet")),
            sources_path=root / "config" / "sources.yaml",
            backfill_plan_path=root / "config" / "backfill_plan.yaml",
            timeout=float(os.getenv("ENERGY_HTTP_TIMEOUT", "30")),
            max_attempts=int(os.getenv("ENERGY_MAX_ATTEMPTS", "3")),
            user_agent=os.getenv("ENERGY_USER_AGENT", cls.user_agent),
        )

    def sources(self) -> dict[str, Any]:
        with self.sources_path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
