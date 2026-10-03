"""数据类型定义。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"


@dataclass(frozen=True)
class Document:
    doc_number: str
    expiry_date: date  # 业务本地日期
    owner: str


@dataclass(frozen=True)
class Notification:
    id: int
    owner: str
    doc_number: str
    level: str
    plan_date: date          # 所属计划本地日期
    scan_time_utc: datetime  # 产生该通知的计划扫描时刻(UTC)
    expiry_date: date
    days_to_expiry: int
    occurrences: int         # 聚合次数(含首次)
