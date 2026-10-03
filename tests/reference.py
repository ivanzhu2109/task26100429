"""独立的无中断参考计算(纯内存,不依赖被测库的状态管理)。

供崩溃恢复测试逐条对照:内容、等级、所属计划日、数量。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def ref_scan_time_utc(plan_date: date) -> datetime:
    """独立实现的扫描时刻计算:本地02:00,不存在则03:00。"""
    for hour in (2, 3):
        local = datetime.combine(plan_date, time(hour, 0), tzinfo=BUSINESS_TZ)
        back = local.astimezone(UTC).astimezone(BUSINESS_TZ)
        if back.replace(tzinfo=None) == local.replace(tzinfo=None):
            return local.astimezone(UTC)
    raise AssertionError("unreachable")


def ref_level(days: int):
    if 0 <= days <= 7:
        return "critical"
    if 8 <= days <= 30:
        return "warning"
    return None


def reference_run(documents, start_date: date, now_utc: datetime):
    """按计划时刻先后无中断处理,返回通知列表(字典)。"""
    docs_by_owner: dict[str, list] = {}
    for d in documents:
        docs_by_owner.setdefault(d.owner, []).append(d)

    notifications: list[dict] = []
    latest: dict[tuple, tuple] = {}  # (owner, doc, level) -> (扫描时刻, 通知)
    plan = start_date
    one_day = timedelta(days=1)
    window = timedelta(hours=24)
    while True:
        scan = ref_scan_time_utc(plan)
        if scan > now_utc:
            break
        for owner in sorted(docs_by_owner):
            cands = []
            for d in docs_by_owner[owner]:
                days = (d.expiry_date - plan).days
                lvl = ref_level(days)
                if lvl is not None:
                    cands.append((lvl, d.expiry_date, d.doc_number, days))
            cands.sort(key=lambda c: (0 if c[0] == "critical" else 1, c[1], c[2]))
            quota = 50
            for lvl, exp, num, days in cands:
                key = (owner, num, lvl)
                last = latest.get(key)
                if last is not None and scan - last[0] < window:
                    last[1]["occurrences"] += 1
                    continue
                if quota <= 0:
                    continue
                quota -= 1
                n = dict(owner=owner, doc_number=num, level=lvl,
                         plan_date=plan.isoformat(),
                         scan_time_utc=scan.isoformat(),
                         expiry_date=exp.isoformat(),
                         days_to_expiry=days, occurrences=1)
                notifications.append(n)
                latest[key] = (scan, n)
        plan += one_day
    return notifications


def sort_key(n: dict):
    return (n["owner"], n["plan_date"], n["scan_time_utc"], n["doc_number"],
            n["level"])
