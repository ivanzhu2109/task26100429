"""Public API: initialize documents, advance time, read owner inbox."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

from . import engine, store
from .models import Document, Notification
from .schedule import BUSINESS_TZ, scheduled_scans


class ReminderLibrary:
    """Compliance document expiry reminder library backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        self._conn = store.connect(db_path)

    def initialize_documents(
        self, start_date: date, documents: list[Document]
    ) -> None:
        """Load the immutable document batch and the starting local date."""
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT value FROM meta WHERE key = 'start_date'"
            ).fetchone()
            if existing is not None:
                raise ValueError(
                    f"already initialized with start_date {existing[0]}"
                )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('start_date', ?)",
                (start_date.isoformat(),),
            )
            conn.executemany(
                "INSERT INTO documents (doc_id, expiry_date, owner)"
                " VALUES (?, ?, ?)",
                [(d.doc_id, d.expiry_date.isoformat(), d.owner) for d in documents],
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def advance_to(
        self,
        now_utc: datetime,
        commit_hook: Callable[[str], None] | None = None,
    ) -> None:
        """Process every scheduled scan with scan instant <= now_utc."""
        conn = self._conn
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'start_date'"
        ).fetchone()
        if row is None:
            raise RuntimeError("initialize_documents must be called first")
        start = date.fromisoformat(row[0])
        now_utc = now_utc.astimezone(timezone.utc)
        end_local = now_utc.astimezone(BUSINESS_TZ).date()
        watermark_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'last_scan_utc'"
        ).fetchone()
        watermark = watermark_row[0] if watermark_row else ""
        for scan in scheduled_scans(start, end_local):
            scan_utc = scan.astimezone(timezone.utc)
            if scan_utc > now_utc:
                break
            scan_iso = scan_utc.isoformat()
            if scan_iso <= watermark:
                continue
            engine.process_scan(conn, scan_utc, commit_hook=commit_hook)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_scan_utc', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (scan_iso,),
            )

    def read_inbox(self, owner: str, limit: int = 50) -> list[Notification]:
        """Return the owner's most recent notifications (newest first)."""
        rows = self._conn.execute(
            """
            SELECT owner, doc_id, level, scan_local_date, scan_utc,
                   expiry_date, aggregate_count
            FROM notifications WHERE owner = ?
            ORDER BY id DESC LIMIT ?
            """,
            (owner, limit),
        ).fetchall()
        return [
            Notification(
                owner=r[0],
                doc_id=r[1],
                level=r[2],
                scan_local_date=date.fromisoformat(r[3]),
                scan_utc=datetime.fromisoformat(r[4]),
                expiry_date=date.fromisoformat(r[5]),
                aggregate_count=r[6],
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
