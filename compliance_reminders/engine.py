"""推进引擎:处理所有计划时刻不晚于给定时刻的扫描。

- 逐计划日、逐负责人处理;每个(计划日,负责人)在单独事务中提交,
  结果完整可见或完全不可见,已提交结果不受后续失败影响。
- 崩溃恢复:已提交的 owner_day_done / scans_done 记录使重跑自动跳过,
  重复推进幂等。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from .models import (LEVEL_CRITICAL, LEVEL_WARNING, Document, Notification)
from .schedule import scan_time_utc
from .store import Store

DAILY_QUOTA = 50
AGGREGATION_WINDOW = timedelta(hours=24)


def _level_for(days: int) -> Optional[str]:
    if 0 <= days <= 7:
        return LEVEL_CRITICAL
    if 8 <= days <= 30:
        return LEVEL_WARNING
    return None


class Engine:
    def __init__(self, store: Store) -> None:
        self._store = store

    def advance_to(self, now_utc: datetime,
                   after_owner_commit: Optional[Callable[[str], None]] = None
                   ) -> None:
        store = self._store
        start = store.get_start_date()
        if start is None:
            raise RuntimeError("documents not initialized")
        docs_by_owner = store.documents_by_owner()
        owners = sorted(docs_by_owner)

        plan_date = start
        while True:
            scan_utc = scan_time_utc(plan_date)
            if scan_utc > now_utc:
                break
            if not store.is_scan_done(plan_date):
                for owner in owners:
                    if store.is_owner_day_done(plan_date, owner):
                        continue
                    new_items, aggregated_ids = self._compute_owner_day(
                        plan_date, scan_utc, docs_by_owner[owner])
                    store.commit_owner_day(plan_date, owner, new_items,
                                           aggregated_ids)
                    if after_owner_commit is not None:
                        after_owner_commit(owner)
                store.mark_scan_done(plan_date, scan_utc)
            plan_date = date.fromordinal(plan_date.toordinal() + 1)

    def _compute_owner_day(
        self, plan_date: date, scan_utc: datetime, docs: list[Document]
    ) -> tuple[list[Notification], list[int]]:
        store = self._store
        candidates = []
        for doc in docs:
            days = (doc.expiry_date - plan_date).days
            level = _level_for(days)
            if level is not None:
                candidates.append((level, doc.expiry_date, doc.doc_number, days))
        # 危急优先、到期日较早优先、文档编号字典序
        candidates.sort(key=lambda c: (0 if c[0] == LEVEL_CRITICAL else 1,
                                       c[1], c[2]))

        new_items: list[Notification] = []
        aggregated_ids: list[int] = []
        quota_left = DAILY_QUOTA
        owner = docs[0].owner
        for level, expiry, doc_number, days in candidates:
            last = store.last_notification(owner, doc_number, level)
            if last is not None and scan_utc - last[1] < AGGREGATION_WINDOW:
                aggregated_ids.append(last[0])
                continue
            if quota_left <= 0:
                continue  # 超额候选在该计划日永久舍弃
            quota_left -= 1
            new_items.append(Notification(
                id=0, owner=owner, doc_number=doc_number, level=level,
                plan_date=plan_date, scan_time_utc=scan_utc,
                expiry_date=expiry, days_to_expiry=days, occurrences=1))
        return new_items, aggregated_ids
