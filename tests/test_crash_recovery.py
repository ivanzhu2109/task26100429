"""Crash mid-run, restart, catch up, re-advance: must match a reference
computation that processes every scheduled scan without interruption."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from compliance_reminders import Document, ReminderLibrary

TZ = ZoneInfo("America/New_York")
START = date(2026, 3, 5)  # spans the 2026-03-08 spring forward
OWNERS = [f"owner{j:03d}" for j in range(30)]
DOCS_PER_OWNER = 20


class SimulatedKill(Exception):
    pass


def _docs():
    docs = []
    for j, owner in enumerate(OWNERS):
        for i in range(DOCS_PER_OWNER):
            expiry = START + timedelta(days=(i * 7 + j * 3) % 40)
            docs.append(Document(f"D{j:03d}-{i:03d}", expiry, owner))
    return docs


def _reference_scan_times(start, end):
    """Independent schedule: 02:00 local, or 03:00 when 02:00 doesn't exist."""
    times = []
    day = start
    while day <= end:
        naive = datetime.combine(day, time(2, 0))
        aware = naive.replace(tzinfo=TZ)
        roundtrip = aware.astimezone(timezone.utc).astimezone(TZ).replace(tzinfo=None)
        if roundtrip != naive:
            aware = datetime.combine(day, time(3, 0)).replace(tzinfo=TZ)
        times.append(aware.astimezone(timezone.utc))
        day += timedelta(days=1)
    return times


def _reference_notifications(docs, start, now_utc):
    """Independent in-memory replay of the business rules.

    Returns {(owner, doc_id, level, scan_utc, scan_local_date): agg_count}.
    """
    end_local = now_utc.astimezone(TZ).date()
    result = {}
    last_created = {}  # (owner, doc_id, level) -> scan datetime
    for scan in _reference_scan_times(start, end_local):
        if scan > now_utc:
            break
        scan_date = scan.astimezone(TZ).date()
        by_owner = {}
        for doc in docs:
            days = (doc.expiry_date - scan_date).days
            if 8 <= days <= 30:
                level = "warning"
            elif 0 <= days <= 7:
                level = "critical"
            else:
                continue
            by_owner.setdefault(doc.owner, []).append(
                (level, doc.expiry_date, doc.doc_id)
            )
        for owner in sorted(by_owner):
            ordered = sorted(
                by_owner[owner],
                key=lambda c: (0 if c[0] == "critical" else 1, c[1], c[2]),
            )
            quota = 0
            for level, _expiry, doc_id in ordered:
                combo = (owner, doc_id, level)
                last = last_created.get(combo)
                if last is not None and scan - last < timedelta(hours=24):
                    result[combo + (last,)] += 1
                    continue
                if quota >= 50:
                    continue
                result[combo + (scan,)] = 1
                last_created[combo] = scan
                quota += 1
    return {
        (owner, doc_id, level, scan, scan.astimezone(TZ).date()): count
        for (owner, doc_id, level, scan), count in result.items()
    }


def _db_rows(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT owner, doc_id, level, scan_utc, scan_local_date, aggregate_count"
        " FROM notifications"
    ).fetchall()
    conn.close()
    return {
        (o, d, lv, datetime.fromisoformat(s), date.fromisoformat(sd)): c
        for o, d, lv, s, sd, c in rows
    }


def test_crash_restart_catchup_matches_reference(tmp_path):
    db_path = str(tmp_path / "crash.db")
    docs = _docs()
    final_now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)

    # Phase 1: process partway, then "kill" after 17 owner-day commits.
    lib = ReminderLibrary(db_path)
    lib.initialize_documents(START, docs)
    commits = {"n": 0}

    def kill_hook(_owner):
        commits["n"] += 1
        if commits["n"] >= 17:
            raise SimulatedKill

    with pytest.raises(SimulatedKill):
        lib.advance_to(
            datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc),
            commit_hook=kill_hook,
        )

    # Whatever is visible after the kill must be whole owner-days only.
    partial = _db_rows(db_path)
    ref_partial = _reference_notifications(
        docs, START, datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
    )
    by_owner_day = {}
    for key, count in ref_partial.items():
        owner, sdate = key[0], key[4]
        by_owner_day.setdefault((owner, sdate), {})[key] = count
    for (owner, sdate), expected_rows in by_owner_day.items():
        actual = {
            k: v for k, v in partial.items() if k[0] == owner and k[4] == sdate
        }
        assert actual == {} or actual == expected_rows, (
            f"partial owner-day visible for {owner} {sdate}"
        )
    assert partial, "expected some committed owner-days before the kill"
    lib.close()

    # Phase 2: reopen the same database and catch up to a later time.
    lib = ReminderLibrary(db_path)
    lib.advance_to(final_now)

    # Phase 3: re-advancing (same and earlier instants) changes nothing.
    before = _db_rows(db_path)
    lib.advance_to(final_now)
    lib.advance_to(datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc))
    after = _db_rows(db_path)
    assert before == after
    lib.close()

    # Final state must equal the uninterrupted reference exactly.
    reference = _reference_notifications(docs, START, final_now)
    assert after == reference
    assert len(after) > 0
