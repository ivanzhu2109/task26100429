"""规模与延迟验证:10万文档、2000负责人、连续7个计划日补跑。

推进执行者处理期间,读取任一负责人最近50条应在200ms内返回。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pytest

from compliance_reminders import ReminderLibrary, Document
from compliance_reminders.engine import Engine
from compliance_reminders.store import Store

N_OWNERS = 2000
DOCS_PER_OWNER = 50          # 共10万份文档
CATCH_UP_DAYS = 7


def _make_docs():
    docs = []
    base = date(2026, 3, 1)
    for i in range(N_OWNERS):
        owner = f"owner-{i:04d}"
        for j in range(DOCS_PER_OWNER):
            # 到期日分散在 0~45 天,覆盖危急/警告/无候选
            expiry = base + timedelta(days=(i * 7 + j * 13) % 46)
            docs.append(Document(f"D-{i:04d}-{j:02d}", expiry, owner))
    return docs


@pytest.mark.slow
def test_scale_catch_up_and_read_latency(tmp_path):
    db_path = str(tmp_path / "perf.db")
    start = date(2026, 3, 1)
    store = Store(db_path)
    store.initialize_documents(start, _make_docs())
    engine = Engine(store)

    now = datetime(2026, 3, 1, 7, 0, tzinfo=timezone.utc) + timedelta(
        days=CATCH_UP_DAYS - 1, hours=1)

    read_latencies = []

    def probe_read(owner):
        t0 = time.perf_counter()
        rows = store.read_inbox(owner, 50)   # 处理期间读取收件箱
        read_latencies.append(time.perf_counter() - t0)

    t0 = time.perf_counter()
    engine.advance_to(now, after_owner_commit=probe_read)
    elapsed = time.perf_counter() - t0
    store.close()

    print(f"\n7日补跑({N_OWNERS * DOCS_PER_OWNER}文档)耗时: {elapsed:.1f}s")
    print(f"处理期间读取{len(read_latencies)}次, "
          f"最大延迟: {max(read_latencies) * 1000:.1f}ms")
    assert max(read_latencies) < 0.2        # 200ms内返回
    assert len(read_latencies) == N_OWNERS * CATCH_UP_DAYS

    # 推进结束后读取延迟
    lib = ReminderLibrary(db_path)
    t0 = time.perf_counter()
    inbox = lib.read_inbox("owner-0000", 50)
    assert time.perf_counter() - t0 < 0.2
    assert len(inbox) <= 50
    lib.close()
