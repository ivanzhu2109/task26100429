"""计划扫描时刻计算:每日本地02:00,春季跳时导致02:00不存在时改为03:00。"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _local_exists(local_dt: datetime) -> bool:
    """检测带时区的本地时间是否真实存在(往返UTC校验)。"""
    roundtrip = local_dt.astimezone(UTC).astimezone(BUSINESS_TZ)
    return roundtrip.replace(tzinfo=None) == local_dt.replace(tzinfo=None)


def scan_time_utc(plan_date: date) -> datetime:
    """返回某计划本地日期对应的扫描时刻(UTC, tz-aware)。

    正常为本地02:00;若02:00不存在(春季跳时),改为该日03:00。
    """
    candidate = datetime.combine(plan_date, time(2, 0), tzinfo=BUSINESS_TZ)
    if not _local_exists(candidate):
        candidate = datetime.combine(plan_date, time(3, 0), tzinfo=BUSINESS_TZ)
    return candidate.astimezone(UTC)
