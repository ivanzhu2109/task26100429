"""Scan schedule computation for the America/New_York business timezone."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/New_York")
_SCAN_HOUR = 2
_FALLBACK_SCAN_HOUR = 3


def _local_exists(naive: datetime, tz: ZoneInfo) -> bool:
    aware = naive.replace(tzinfo=tz)
    return aware.astimezone(ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None) == naive


def scan_time_for(local_date: date) -> datetime:
    """Return the UTC-aware scan instant for a local business date.

    Normally 02:00 local; on the spring-forward day where 02:00 does not
    exist, 03:00 local is used instead.
    """
    naive = datetime.combine(local_date, time(_SCAN_HOUR))
    if not _local_exists(naive, BUSINESS_TZ):
        naive = datetime.combine(local_date, time(_FALLBACK_SCAN_HOUR))
    return naive.replace(tzinfo=BUSINESS_TZ)


def scheduled_scans(start: date, end: date) -> list[datetime]:
    """Return scan instants for every local date in [start, end]."""
    days = (end - start).days
    return [scan_time_for(start + timedelta(days=i)) for i in range(days + 1)]
