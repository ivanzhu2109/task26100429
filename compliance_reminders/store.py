"""SQLite persistence layer."""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    expiry_date TEXT NOT NULL,
    owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    level TEXT NOT NULL,
    scan_utc TEXT NOT NULL,
    scan_local_date TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    aggregate_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_notifications_owner
    ON notifications (owner, id);
CREATE INDEX IF NOT EXISTS idx_notifications_combo
    ON notifications (owner, doc_id, level, scan_utc);
CREATE TABLE IF NOT EXISTS scan_owner_done (
    scan_utc TEXT NOT NULL,
    owner TEXT NOT NULL,
    PRIMARY KEY (scan_utc, owner)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (and if needed initialize) the database."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn
