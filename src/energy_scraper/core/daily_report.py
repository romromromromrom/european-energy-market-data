from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .database import Database


PARIS = ZoneInfo("Europe/Paris")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _result_line(source: str, result: dict, error: str | None) -> str:
    status = "ERREUR" if error else "OK"
    details = [
        f"{result.get('received', 0)} reçus",
        f"{result.get('inserted', 0)} insérés",
    ]
    if result.get("empty"):
        details.append(f"{result['empty']} vide(s)")
    if result.get("errors"):
        details.append(f"{result['errors']} erreur(s)")
    if error:
        details.append(error.replace("\n", " "))
    return f"- {source}: {status} — " + ", ".join(details)


def write_daily_reports(
    db: Database,
    root: Path,
    reference_date: date,
    payload: dict,
    started_at: datetime,
    finished_at: datetime | None = None,
) -> dict[str, Path]:
    """Write human-readable collection and completeness reports.

    The dated log is appended so manual reruns do not erase the morning attempt.
    The completeness files are snapshots of the latest known state.
    """
    finished_at = finished_at or datetime.now(UTC)
    local_start = started_at.astimezone(PARIS)
    local_finish = finished_at.astimezone(PARIS)
    errors = payload.get("errors", {})
    results = payload.get("results", {})
    source_names = list(dict.fromkeys([*results, *errors]))

    lines = [
        f"Collecte du {reference_date.isoformat()}",
        f"Début : {local_start:%Y-%m-%d %H:%M:%S %Z}",
        f"Fin   : {local_finish:%Y-%m-%d %H:%M:%S %Z}",
        f"Statut: {payload.get('status', 'INCONNU')}",
        "",
    ]
    lines.extend(_result_line(name, results.get(name, {}), errors.get(name)) for name in source_names)
    lines.extend(["", "Détail machine :", json.dumps(payload, ensure_ascii=False, indent=2), ""])
    log_content = "\n".join(lines)

    log_dir = root / "reports" / "logs"
    dated_log = log_dir / f"collecte-{reference_date.isoformat()}.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    with dated_log.open("a", encoding="utf-8") as handle:
        if dated_log.stat().st_size:
            handle.write("\n" + "=" * 72 + "\n\n")
        handle.write(log_content)
    _atomic_write(log_dir / "derniere-collecte.log", log_content)

    with db.connect(read_only=True) as conn:
        failed_partitions = conn.execute(
            """SELECT dataset_id, partition_key, status, records_received, collected_at
               FROM collection_partitions
               WHERE collected_at BETWEEN ? AND ? AND status NOT IN ('SUCCESS','SUCCESS_EMPTY')
               ORDER BY dataset_id, partition_key""",
            (started_at.isoformat(), finished_at.isoformat()),
        ).fetchall()
        incomplete_jobs = conn.execute(
            """SELECT dataset_id, partition_key, status, records_received,
                      last_attempt_at AS collected_at, error_message
               FROM backfill_jobs
               WHERE last_attempt_at BETWEEN ? AND ?
                 AND status NOT IN ('SUCCESS','SUCCESS_EMPTY')
               ORDER BY dataset_id, partition_key""",
            (started_at.isoformat(), finished_at.isoformat()),
        ).fetchall()
        open_gap_groups = conn.execute(
            """SELECT dataset_id, series_key, COUNT(*) AS gap_count,
                      MIN(expected_timestamp_local) AS first_missing,
                      MAX(expected_timestamp_local) AS last_missing,
                      MAX(last_error_message) AS last_error
               FROM data_gaps
               WHERE gap_status IN ('OPEN','RETRY_PENDING','MANUAL_REVIEW','CONFIRMED_SOURCE_MISSING')
               GROUP BY dataset_id, series_key
               ORDER BY dataset_id, series_key"""
        ).fetchall()

    # Backfill jobs carry the precise partition and error. Keep collection-only
    # failures as a fallback for collectors that do not create a backfill job.
    incomplete = {(row["dataset_id"], row["partition_key"]): dict(row) for row in failed_partitions}
    incomplete.update({(row["dataset_id"], row["partition_key"]): dict(row) for row in incomplete_jobs})
    missing_count = len(incomplete) + len(errors) + sum(row["gap_count"] for row in open_gap_groups)
    completeness_status = "COMPLET" if missing_count == 0 else "INCOMPLET"
    report = [
        f"# Complétude des données — {reference_date.isoformat()}",
        "",
        f"**Statut : {completeness_status}**",
        "",
        f"Généré le {local_finish:%Y-%m-%d à %H:%M:%S} (Europe/Paris).",
        "",
        "## Collecte du jour",
        "",
    ]
    if not errors and not incomplete:
        report.append("Aucun échec détecté pendant cette collecte.")
    else:
        for source, message in errors.items():
            report.append(f"- **{source}** : {message.replace(chr(10), ' ')}")
        for row in incomplete.values():
            detail = f" — {row['error_message'].replace(chr(10), ' ')}" if row.get("error_message") else ""
            report.append(
                f"- **{row['dataset_id']}** — `{row['partition_key']}` : "
                f"{row['status']} ({row['records_received']} enregistrement(s) reçu(s)){detail}"
            )
    report.extend(["", "## Gaps ouverts dans l’historique", ""])
    if not open_gap_groups:
        report.append("Aucun gap détaillé ouvert dans la base.")
    else:
        report.extend([
            "| Jeu de données | Série | Manquants | Première absence | Dernière absence |",
            "|---|---|---:|---|---|",
        ])
        for row in open_gap_groups:
            report.append(
                f"| {row['dataset_id']} | {row['series_key']} | {row['gap_count']} | "
                f"{row['first_missing']} | {row['last_missing']} |"
            )
    report.extend([
        "",
        "> Un échec de collecte signale une zone potentiellement manquante. Les gaps ouverts sont les absences déjà matérialisées dans la grille attendue.",
        "",
    ])
    completeness_content = "\n".join(report)
    completeness_dir = root / "reports" / "completeness"
    dated_completeness = completeness_dir / f"completude-{reference_date.isoformat()}.md"
    _atomic_write(dated_completeness, completeness_content)
    _atomic_write(root / "reports" / "completude.md", completeness_content)
    return {"log": dated_log, "completeness": dated_completeness}
