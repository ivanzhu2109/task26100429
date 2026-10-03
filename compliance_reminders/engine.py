"""Core scan processing: candidates, aggregation, quota, atomic commits."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable

from .schedule import BUSINESS_TZ

LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"
DAILY_QUOTA = 50
_AGGREGATION_WINDOW = timedelta(hours=24)


def _level_for(days_to_expiry: int) -> str | None:
    if 8 <= days_to_expiry <= 30:
        return LEVEL_WARNING
    if 0 <= days_to_expiry <= 7:
        return LEVEL_CRITICAL
    return None


def _load_candidates(conn: sqlite3.Connection, scan_local_date) -> dict[str, list[tuple]]:
    """Group (level, expiry_date, doc_id) candidates by owner."""
    from datetime import date as _date

    by_owner: dict[str, list[tuple]] = {}
    for doc_id, expiry_iso, owner in conn.execute(
        "SELECT doc_id, expiry_date, owner FROM documents"
    ):
        expiry = _date.fromisoformat(expiry_iso)
        level = _level_for((expiry - scan_local_date).days)
        if level is not None:
            by_owner.setdefault(owner, []).append((level, expiry, doc_id))
    return by_owner


def _latest_notifications(
    conn: sqlite3.Connection, owner: str
) -> dict[tuple[str, str], tuple[int, str]]:
    """Map (doc_id, level) -> (notification id, scan_utc) of the newest entry."""
    rows = conn.execute(
        """
        SELECT n.doc_id, n.level, n.id, n.scan_utc
        FROM notifications n
        JOIN (
            SELECT doc_id, level, MAX(scan_utc) AS mx
            FROM notifications WHERE owner = ?
            GROUP BY doc_id, level
        ) t ON n.doc_id = t.doc_id AND n.level = t.level AND n.scan_utc = t.mx
        WHERE n.owner = ?
        """,
        (owner, owner),
    ).fetchall()
    return {(doc_id, level): (nid, scan_utc) for doc_id, level, nid, scan_utc in rows}


def _process_owner_day(
    conn: sqlite3.Connection,
    owner: str,
    candidates: list[tuple],
    scan_utc: datetime,
    scan_utc_iso: str,
    scan_local_date,
) -> None:
    """Commit one owner's results for one scan day in a single transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        done = conn.execute(
            "SELECT 1 FROM scan_owner_done WHERE scan_utc = ? AND owner = ?",
            (scan_utc_iso, owner),
        ).fetchone()
        if done:
            conn.execute("COMMIT")
            return

        latest = _latest_notifications(conn, owner)
        # Critical first, then earliest expiry, then doc id.
        ordered = sorted(
            candidates,
            key=lambda c: (0 if c[0] == LEVEL_CRITICAL else 1, c[1], c[2]),
        )
        quota_used = 0
        for level, expiry, doc_id in ordered:
            combo = (doc_id, level)
            entry = latest.get(combo)
            if entry is not None:
                nid, last_scan_iso = entry
                last_scan = datetime.fromisoformat(last_scan_iso)
                if scan_utc - last_scan < _AGGREGATION_WINDOW:
                    conn.execute(
                        "UPDATE notifications SET aggregate_count = aggregate_count + 1"
                        " WHERE id = ?",
                        (nid,),
                    )
                    continue
            if quota_used >= DAILY_QUOTA:
                continue  # permanently discarded for this scan day
            conn.execute(
                """
                INSERT INTO notifications
                    (owner, doc_id, level, scan_utc, scan_local_date,
                     expiry_date, aggregate_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    owner,
                    doc_id,
                    level,
                    scan_utc_iso,
                    scan_local_date.isoformat(),
                    expiry.isoformat(),
                ),
            )
            latest[combo] = (
                conn.execute("SELECT last_insert_rowid()").fetchone()[0],
                scan_utc_iso,
            )
            quota_used += 1
        conn.execute(
            "INSERT INTO scan_owner_done (scan_utc, owner) VALUES (?, ?)",
            (scan_utc_iso, owner),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def process_scan(
    conn: sqlite3.Connection,
    scan_utc: datetime,
    commit_hook: Callable[[str], None] | None = None,
) -> None:
    """Process one scheduled scan, committing per owner-day atomically."""
    scan_utc_utc = scan_utc.astimezone(timezone.utc)
    scan_local_date = scan_utc.astimezone(BUSINESS_TZ).date()
    scan_utc_iso = scan_utc_utc.isoformat()
    by_owner = _load_candidates(conn, scan_local_date)
    for owner in sorted(by_owner):
        _process_owner_day(
            conn, owner, by_owner[owner], scan_utc_utc, scan_utc_iso, scan_local_date
        )
        if commit_hook is not None:
            commit_hook(owner)
