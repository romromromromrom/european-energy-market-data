from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


PARIS = ZoneInfo("Europe/Paris")


def local_intervals(day: date, minutes: int, timezone: ZoneInfo = PARIS) -> list[tuple[datetime, datetime]]:
    """Build a DST-safe local grid by iterating UTC between local midnight boundaries."""
    local_start = datetime.combine(day, datetime.min.time(), timezone)
    local_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone)
    current = local_start.astimezone(UTC)
    end = local_end.astimezone(UTC)
    values = []
    while current < end:
        values.append((current, current.astimezone(timezone)))
        current += timedelta(minutes=minutes)
    return values


def business_days(start: date, end: date) -> list[date]:
    days = []
    while start <= end:
        if start.weekday() < 5:
            days.append(start)
        start += timedelta(days=1)
    return days
