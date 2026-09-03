from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def archive_payload(raw_dir: Path, source: str, payload: Any, metadata: dict[str, Any]) -> tuple[Path, str]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    now = datetime.now(UTC)
    folder = raw_dir / source / now.date().isoformat() / now.strftime("%H%M%S_%fZ")
    folder.mkdir(parents=True, exist_ok=False)
    with gzip.open(folder / "response.json.gz", "wb") as fh:
        fh.write(encoded)
    safe = {k: v for k, v in metadata.items() if k.lower() not in {"authorization", "cookie", "token"}}
    safe.update({"collected_at": now.isoformat(), "payload_sha256": digest})
    (folder / "metadata.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return folder, digest
