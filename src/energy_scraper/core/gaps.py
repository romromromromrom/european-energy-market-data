from __future__ import annotations

import csv
import hashlib
import uuid
from datetime import date
from pathlib import Path

from .calendars import business_days, local_intervals
from .database import Database, utcnow


def build_expected_eex(db: Database, dataset: str, series_key: str, start: date, end: date) -> int:
    now = utcnow()
    rows = [(dataset,series_key,f"{day}T17:00:00+00:00",f"{day}T18:00:00+01:00",str(day),1440,
             "weekday settlement candidate; exchange holidays may be NOT_EXPECTED","eex-weekday-v1",now)
            for day in business_days(start,end)]
    with db.connect() as conn:
        before=conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO expected_observations VALUES(?,?,?,?,?,?,?,?,?)",rows)
        return conn.total_changes-before


def build_expected_grid(db: Database, dataset: str, series_key: str, start: date, end: date, minutes: int) -> int:
    rows=[]; now=utcnow(); day=start
    while day<=end:
        for utc, local in local_intervals(day,minutes):
            rows.append((dataset,series_key,utc.isoformat(),local.isoformat(),str(day),minutes,
                         "Europe/Paris local delivery grid, DST aware","paris-dst-v1",now))
        day=date.fromordinal(day.toordinal()+1)
    with db.connect() as conn:
        before=conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO expected_observations VALUES(?,?,?,?,?,?,?,?,?)",rows)
        return conn.total_changes-before


def detect_eex_gaps(db: Database, dataset: str, series_key: str, product_code: str, maturity: str) -> int:
    now=utcnow()
    with db.connect() as conn:
        expected=conn.execute("""SELECT e.* FROM expected_observations e WHERE e.dataset_id=? AND e.series_key=?
          AND NOT EXISTS (SELECT 1 FROM market_prices p JOIN instruments i ON p.instrument_id=i.instrument_id
          WHERE i.product_code=? AND i.maturity=? AND p.trading_date=e.delivery_date_local)""",(dataset,series_key,product_code,maturity)).fetchall()
        before=conn.total_changes
        conn.executemany("""INSERT OR IGNORE INTO data_gaps(gap_id,dataset_id,series_key,expected_timestamp_utc,expected_timestamp_local,
          missing_fields,detected_at,gap_status,source_attempted) VALUES(?,?,?,?,?,?,?,'OPEN','eex')""",
          [(str(uuid.uuid4()),dataset,series_key,r["expected_timestamp_utc"],r["expected_timestamp_local"],"settlement",now) for r in expected])
        return conn.total_changes-before


CSV_FIELDS=["dataset_id","series_key","timestamp","field","value","unit","source_note","open","high","low","close","vwap","vwap_1h","vwap_3h","volume","buy_volume","sell_volume"]


def export_gap_template(path: Path) -> Path:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as fh:
        writer=csv.DictWriter(fh,fieldnames=CSV_FIELDS); writer.writeheader()
    return path


def import_gap_csv(db: Database, path: Path, dry_run: bool=True) -> dict[str,int]:
    raw=path.read_bytes(); digest=hashlib.sha256(raw).hexdigest(); valid=[]; rejected=0
    with path.open(newline="",encoding="utf-8-sig") as fh:
        reader=csv.DictReader(fh)
        required={"dataset_id","series_key","timestamp","field","value","unit","source_note"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV columns required: {sorted(required)}")
        with db.connect() as conn:
            for row in reader:
                gaps=conn.execute("""SELECT gap_id FROM data_gaps WHERE dataset_id=? AND series_key=? AND
                  (expected_timestamp_utc=? OR expected_timestamp_local=?) AND gap_status IN ('OPEN','RETRY_PENDING','MANUAL_REVIEW','CONFIRMED_SOURCE_MISSING')""",
                  (row["dataset_id"],row["series_key"],row["timestamp"],row["timestamp"])).fetchall()
                if len(gaps)!=1 or not row["unit"] or not row["field"]:
                    rejected+=1; continue
                try: value=float(row["value"])
                except ValueError: rejected+=1; continue
                valid.append((gaps[0][0],row,value))
    if not dry_run:
        now=utcnow()
        with db.transaction() as conn:
            for gap_id,row,value in valid:
                conn.execute("INSERT INTO manual_gap_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()),gap_id,row["dataset_id"],row["series_key"],row["timestamp"],row["field"],value,row["unit"],row["source_note"],path.name,digest,now))
                conn.execute("UPDATE data_gaps SET gap_status='FILLED_MANUAL',resolved_at=?,resolution_method='manual_csv',resolved_by_source='manual_csv' WHERE gap_id=?",(now,gap_id))
    return {"valid":len(valid),"rejected":rejected,"inserted":0 if dry_run else len(valid)}
