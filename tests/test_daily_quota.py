"""Per-owner daily quota: 50 new notifications, critical first."""
from datetime import date, datetime, timezone

import pytest

from compliance_reminders import Document, ReminderLibrary

OWNER = "olivia"
# 2026-03-08 is spring-forward day: the Mar 9 scan is only 23h after the
# Mar 8 scan, so Mar 8 warning notifications aggregate on Mar 9.
DAY1 = date(2026, 3, 8)
DAY2 = date(2026, 3, 9)
DAY3 = date(2026, 3, 10)


def _docs():
    docs = []
    # 5 docs that are warnings on both days -> aggregate on DAY2 (23h gap),
    # consuming no quota.
    for i in range(1, 6):
        docs.append(Document(f"A{i:03d}", date(2026, 3, 29), OWNER))
    # 10 docs that are warnings on DAY1 (8 days out) and critical on DAY2
    # (7 days out): the critical level must not be swallowed by the warning.
    for i in range(1, 11):
        docs.append(Document(f"C{i:03d}", date(2026, 3, 16), OWNER))
    # 60 docs that first become warnings on DAY2 (30 days out).
    for i in range(1, 61):
        docs.append(Document(f"W{i:03d}", date(2026, 4, 8), OWNER))
    return docs


@pytest.fixture()
def lib(tmp_path):
    library = ReminderLibrary(str(tmp_path / "test.db"))
    library.initialize_documents(DAY1, _docs())
    yield library
    library.close()


def _rows(lib, day):
    return [n for n in lib.read_inbox(OWNER, limit=500) if n.scan_local_date == day]


def test_quota_exactly_50_with_all_criticals(lib):
    lib.advance_to(datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc))

    day2 = _rows(lib, DAY2)
    assert len(day2) == 50

    criticals = [n for n in day2 if n.level == "critical"]
    assert len(criticals) == 10
    assert {n.doc_id for n in criticals} == {f"C{i:03d}" for i in range(1, 11)}

    # Remaining 40 slots go to warnings by (expiry, doc_id): all W-docs share
    # the same expiry, so the lexicographically smallest 40 win.
    warnings = [n for n in day2 if n.level == "warning"]
    assert len(warnings) == 40
    assert {n.doc_id for n in warnings} == {f"W{i:03d}" for i in range(1, 41)}

    # The 5 aggregatable docs produced no DAY2 rows; their DAY1 rows bumped.
    day1 = {n.doc_id: n for n in _rows(lib, DAY1)}
    for i in range(1, 6):
        assert day1[f"A{i:03d}"].aggregate_count == 2
    # C-docs have separate DAY1 warning entries (level-specific aggregation).
    for i in range(1, 11):
        assert day1[f"C{i:03d}"].level == "warning"
        assert day1[f"C{i:03d}"].aggregate_count == 1

    # Discarded warnings W041..W060 have no rows at all.
    all_ids = {n.doc_id for n in lib.read_inbox(OWNER, limit=500)}
    for i in range(41, 61):
        assert f"W{i:03d}" not in all_ids


def test_readvance_is_idempotent_and_quota_refreshes_next_day(lib):
    lib.advance_to(datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc))
    snapshot = lib.read_inbox(OWNER, limit=500)

    # Re-advancing to the same instant must not add or change anything.
    lib.advance_to(datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc))
    assert lib.read_inbox(OWNER, limit=500) == snapshot

    # The next scan day gets a fresh quota of 50.
    lib.advance_to(datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc))
    day3 = _rows(lib, DAY3)
    assert len(day3) == 50
    assert len([n for n in day3 if n.level == "critical"]) == 10
    # DAY2 results are untouched by the later run.
    assert len(_rows(lib, DAY2)) == 50
