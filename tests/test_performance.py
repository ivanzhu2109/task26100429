"""Non-functional checks: 100k docs / 2000 owners / 7-day catch-up,
inbox reads stay fast and work while a catch-up is in progress."""
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from compliance_reminders import Document, ReminderLibrary

START = date(2026, 6, 1)
N_OWNERS = 2000
DOCS_PER_OWNER = 50  # 100k documents total


def _docs():
    docs = []
    for j in range(N_OWNERS):
        owner = f"owner{j:05d}"
        for i in range(DOCS_PER_OWNER):
            # Spread expiries so roughly half the docs are candidates daily.
            expiry = START + timedelta(days=(i * 13 + j * 7) % 60)
            docs.append(Document(f"D{j:05d}-{i:03d}", expiry, owner))
    return docs


def test_seven_day_catchup(tmp_path):
    path = str(tmp_path / "catchup.db")
    lib = ReminderLibrary(path)
    lib.initialize_documents(START, _docs())
    t0 = time.monotonic()
    lib.advance_to(datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc))
    elapsed = time.monotonic() - t0
    print(f"\n7-day catch-up over 100k docs took {elapsed:.1f}s")
    t0 = time.monotonic()
    inbox = lib.read_inbox("owner00042", limit=50)
    read_ms = (time.monotonic() - t0) * 1000
    assert read_ms < 200, f"inbox read took {read_ms:.1f}ms"
    assert 0 < len(inbox) <= 50
    lib.close()


def test_inbox_readable_during_catchup(tmp_path):
    path = str(tmp_path / "concurrent.db")
    lib = ReminderLibrary(path)
    lib.initialize_documents(START, _docs())
    reads = {"done": 0, "rows": 0}

    def read_during_run(owner):
        # A separate connection (another reader) must see committed
        # owner-days immediately, without waiting for the whole run.
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE owner = ?", (owner,)
        ).fetchone()[0]
        conn.close()
        reads["done"] += 1
        reads["rows"] += rows
        if reads["done"] >= 25:
            raise RuntimeError("stop early; read checks complete")

    with pytest.raises(RuntimeError, match="stop early"):
        lib.advance_to(
            datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            commit_hook=read_during_run,
        )
    assert reads["done"] == 25
    assert reads["rows"] > 0  # committed owner-days were visible mid-run
    lib.close()
