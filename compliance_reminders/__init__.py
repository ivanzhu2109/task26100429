"""合规文档到期提醒库。

上层调用三项能力:
- initialize_documents: 初始化一批文档(指定起始本地日期)
- advance_to: 推进至给定UTC时刻,补齐所有到期扫描
- read_inbox: 读取负责人收件箱(最近N条)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional

from .engine import Engine
from .models import (LEVEL_CRITICAL, LEVEL_WARNING, Document, Notification)
from .store import Store


class ReminderLibrary:
    """上层调用入口。同一数据库可重复打开,推进幂等。"""

    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)
        self._engine = Engine(self._store)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "ReminderLibrary":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def initialize_documents(self, start_date: date,
                             documents: Iterable[Document]) -> None:
        self._store.initialize_documents(start_date, documents)

    def advance_to(self, now_utc: datetime) -> None:
        self._engine.advance_to(now_utc)

    def read_inbox(self, owner: str, limit: int = 50) -> list[Notification]:
        return self._store.read_inbox(owner, limit)


__all__ = [
    "ReminderLibrary", "Document", "Notification",
    "LEVEL_WARNING", "LEVEL_CRITICAL",
]
