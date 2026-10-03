"""Aggregation must follow elapsed wall-clock time, not calendar days."""
from datetime import date, datetime, timezone

import pytest

from compliance_reminders import Document, ReminderLibrary


@pytest.fixture()
def lib(tmp_path):
    library = ReminderLibrary(str(tmp_path / "test.db"))
    yield library
    library.close()


def _by_day(notifications):
    return {n.scan_local_date: n for n in notifications}


def test_spring_forward_23h_gap_aggregates(lib):
    # 2026-03-08 is the US spring-forward day: local 02:00 does not exist,
    # so the scan runs at 03:00 EDT. The gap to the next day's 02:00 EDT
    # scan is only 23 hours -> the second scan must aggregate.
    lib.initialize_documents(
        date(2026, 3, 7), [Document("D1", date(2026, 3, 12), "alice")]
    )
    lib.advance_to(datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc))

    inbox = _by_day(lib.read_inbox("alice"))
    assert set(inbox) == {date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 10)}

    mar7 = inbox[date(2026, 3, 7)]
    mar8 = inbox[date(2026, 3, 8)]
    mar10 = inbox[date(2026, 3, 10)]
    assert mar7.aggregate_count == 1
    # Mar 8 scan ran at 03:00 EDT (07:00 UTC) because 02:00 did not exist.
    assert mar8.scan_utc == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)
    # Mar 9 scan (06:00 UTC) was 23h after Mar 8 -> aggregated into Mar 8.
    assert mar8.aggregate_count == 2
    # Mar 10 scan is 47h after the Mar 8 window anchor -> new entry.
    assert mar10.aggregate_count == 1
    assert all(n.level == "critical" for n in inbox.values())


def test_fall_back_25h_gap_creates_new_entry(lib):
    # 2026-11-01 is the US fall-back day: the gap between the Oct 31 and
    # Nov 1 scans is 25 hours -> a new entry must be created, not aggregated.
    lib.initialize_documents(
        date(2026, 10, 31), [Document("D1", date(2026, 11, 5), "alice")]
    )
    lib.advance_to(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc))

    inbox = _by_day(lib.read_inbox("alice"))
    assert set(inbox) == {date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)}
    assert all(n.aggregate_count == 1 for n in inbox.values())
    assert inbox[date(2026, 10, 31)].scan_utc == datetime(
        2026, 10, 31, 6, 0, tzinfo=timezone.utc
    )
    assert inbox[date(2026, 11, 1)].scan_utc == datetime(
        2026, 11, 1, 7, 0, tzinfo=timezone.utc
    )


def test_exactly_24h_gap_creates_new_entry(lib):
    # Consecutive regular days are exactly 24h apart: the boundary is
    # inclusive, so each day produces a new notification.
    lib.initialize_documents(
        date(2026, 6, 1), [Document("D1", date(2026, 6, 6), "alice")]
    )
    lib.advance_to(datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc))

    inbox = _by_day(lib.read_inbox("alice"))
    assert set(inbox) == {
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
    }
    assert all(n.aggregate_count == 1 for n in inbox.values())
