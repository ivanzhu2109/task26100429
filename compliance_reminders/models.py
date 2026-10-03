"""Shared type definitions for the compliance reminder library."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Document:
    """A compliance document tracked by the library."""

    doc_id: str
    expiry_date: date
    owner: str


@dataclass(frozen=True)
class Notification:
    """A single inbox entry for an owner."""

    owner: str
    doc_id: str
    level: str  # "warning" | "critical"
    scan_local_date: date
    scan_utc: datetime
    expiry_date: date
    aggregate_count: int
