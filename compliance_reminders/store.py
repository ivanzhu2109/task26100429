"""SQLite 持久化层。

- WAL 模式:推进写入期间允许并发读取收件箱。
- 每个(计划日,负责人)的结果在单独事务中提交,保证完整可见或完全不可见。
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Iterable, Optional

from .models import Document, Notification

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_number TEXT PRIMARY KEY,
    expiry_date TEXT NOT NULL,
    owner TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    doc_number TEXT NOT NULL,
    level TEXT NOT NULL,
    plan_date TEXT NOT NULL,
    scan_time_utc TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    days_to_expiry INTEGER NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_notifications_owner_id
    ON notifications(owner, id);
CREATE INDEX IF NOT EXISTS idx_notifications_combo
    ON notifications(owner, doc_number, level, id);
CREATE TABLE IF NOT EXISTS owner_day_done (
    plan_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    PRIMARY KEY (plan_date, owner)
);
CREATE TABLE IF NOT EXISTS scans_done (
    plan_date TEXT PRIMARY KEY,
    scan_time_utc TEXT NOT NULL
);
"""


def _dt_to_str(dt: datetime) -> str:
    return dt.isoformat()


def _dt_from_str(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _row_to_notification(row: sqlite3.Row) -> Notification:
    return Notification(
        id=row[0], owner=row[1], doc_number=row[2], level=row[3],
        plan_date=date.fromisoformat(row[4]),
        scan_time_utc=_dt_from_str(row[5]),
        expiry_date=date.fromisoformat(row[6]),
        days_to_expiry=row[7], occurrences=row[8],
    )


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = None
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def initialize_documents(self, start_date: date, documents: Iterable[Document]) -> None:
        docs = list(documents)
        with self._conn:  # 单事务
            existing = self._conn.execute(
                "SELECT value FROM meta WHERE key='start_date'").fetchone()
            if existing is not None:
                raise RuntimeError("documents already initialized")
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES('start_date', ?)",
                (start_date.isoformat(),))
            self._conn.executemany(
                "INSERT INTO documents(doc_number, expiry_date, owner) VALUES (?, ?, ?)",
                [(d.doc_number, d.expiry_date.isoformat(), d.owner) for d in docs])

    def get_start_date(self) -> Optional[date]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='start_date'").fetchone()
        return date.fromisoformat(row[0]) if row else None

    def is_scan_done(self, plan_date: date) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM scans_done WHERE plan_date=?",
            (plan_date.isoformat(),)).fetchone() is not None

    def mark_scan_done(self, plan_date: date, scan_utc: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO scans_done(plan_date, scan_time_utc) VALUES (?, ?)",
                (plan_date.isoformat(), _dt_to_str(scan_utc)))

    def owners(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT owner FROM documents ORDER BY owner").fetchall()
        return [r[0] for r in rows]

    def documents_by_owner(self) -> dict[str, list[Document]]:
        result: dict[str, list[Document]] = {}
        for num, exp, owner in self._conn.execute(
                "SELECT doc_number, expiry_date, owner FROM documents"):
            result.setdefault(owner, []).append(
                Document(num, date.fromisoformat(exp), owner))
        return result

    def is_owner_day_done(self, plan_date: date, owner: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM owner_day_done WHERE plan_date=? AND owner=?",
            (plan_date.isoformat(), owner)).fetchone() is not None

    def last_notification(self, owner: str, doc_number: str,
                          level: str) -> Optional[tuple[int, datetime]]:
        """返回该组合最近一次新建通知的 (id, 计划扫描时刻UTC)。"""
        row = self._conn.execute(
            "SELECT id, scan_time_utc FROM notifications "
            "WHERE owner=? AND doc_number=? AND level=? ORDER BY id DESC LIMIT 1",
            (owner, doc_number, level)).fetchone()
        return (row[0], _dt_from_str(row[1])) if row else None

    def commit_owner_day(self, plan_date: date, owner: str,
                         new_notifications: list[Notification],
                         aggregated_ids: list[int]) -> None:
        """单事务提交某负责人某计划日的全部结果(原子可见)。"""
        with self._conn:
            self._conn.executemany(
                "INSERT INTO notifications(owner, doc_number, level, plan_date, "
                "scan_time_utc, expiry_date, days_to_expiry, occurrences) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                [(n.owner, n.doc_number, n.level, n.plan_date.isoformat(),
                  _dt_to_str(n.scan_time_utc), n.expiry_date.isoformat(),
                  n.days_to_expiry) for n in new_notifications])
            self._conn.executemany(
                "UPDATE notifications SET occurrences = occurrences + 1 WHERE id=?",
                [(i,) for i in aggregated_ids])
            self._conn.execute(
                "INSERT INTO owner_day_done(plan_date, owner) VALUES (?, ?)",
                (plan_date.isoformat(), owner))

    def read_inbox(self, owner: str, limit: int = 50) -> list[Notification]:
        rows = self._conn.execute(
            "SELECT id, owner, doc_number, level, plan_date, scan_time_utc, "
            "expiry_date, days_to_expiry, occurrences "
            "FROM notifications WHERE owner=? ORDER BY id DESC LIMIT ?",
            (owner, limit)).fetchall()
        return [_row_to_notification(r) for r in rows]
