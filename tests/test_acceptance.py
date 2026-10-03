"""验收测试:DST聚合、每日额度、崩溃恢复对照参考计算。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from compliance_reminders import ReminderLibrary, Document
from compliance_reminders.engine import Engine
from compliance_reminders.store import Store
from tests.reference import reference_run, sort_key


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def make_lib(tmp_path, name="t.db"):
    return ReminderLibrary(str(tmp_path / name))


# ---------- 场景一:聚合依实际经过时间 ----------

def test_spring_forward_23h_interval_aggregates(tmp_path):
    """春季跳时后23小时间隔:聚合;恰好24小时(窗口未移动)可新增。"""
    lib = make_lib(tmp_path)
    lib.initialize_documents(date(2026, 3, 7), [
        Document("DOC-1", date(2026, 3, 25), "alice"),
    ])
    lib.advance_to(utc(2026, 3, 8, 8))   # 3/7 07:00Z、3/8 07:00Z(24h)-> 各新增
    inbox = lib.read_inbox("alice")
    assert len(inbox) == 2
    assert all(n.occurrences == 1 for n in inbox)

    lib.advance_to(utc(2026, 3, 9, 8))   # 3/9 06:00Z, 距3/8新建仅23h -> 聚合
    inbox = lib.read_inbox("alice")
    assert len(inbox) == 2               # 不新增条目
    agg = [n for n in inbox if n.plan_date == date(2026, 3, 8)]
    assert len(agg) == 1 and agg[0].occurrences == 2

    lib.advance_to(utc(2026, 3, 10, 8))  # 3/10 06:00Z, 距3/8新建恰好24h -> 新增
    inbox = lib.read_inbox("alice")
    assert len(inbox) == 3
    new = [n for n in inbox if n.plan_date == date(2026, 3, 10)]
    assert len(new) == 1 and new[0].occurrences == 1
    lib.close()


def test_fall_back_25h_interval_creates_new(tmp_path):
    """秋季25小时间隔:超过24小时窗口,每天都新增。"""
    lib = make_lib(tmp_path)
    lib.initialize_documents(date(2026, 10, 30), [
        Document("DOC-1", date(2026, 11, 20), "alice"),
    ])
    lib.advance_to(utc(2026, 11, 2, 8))  # 10/30~11/2 四次扫描
    inbox = lib.read_inbox("alice")
    assert len(inbox) == 4
    assert all(n.occurrences == 1 for n in inbox)
    plan_dates = sorted(n.plan_date for n in inbox)
    assert plan_dates == [date(2026, 10, 30), date(2026, 10, 31),
                          date(2026, 11, 1), date(2026, 11, 2)]
    lib.close()


def test_warning_and_critical_aggregate_separately(tmp_path):
    """警告与危急分别聚合:已有警告不吞掉危急。"""
    lib = make_lib(tmp_path)
    lib.initialize_documents(date(2026, 3, 7), [
        Document("DOC-1", date(2026, 3, 15), "alice"),  # 3/7警告(8天) 3/8危急(7天)
    ])
    lib.advance_to(utc(2026, 3, 8, 8))
    inbox = lib.read_inbox("alice")
    levels = {(n.plan_date, n.level) for n in inbox}
    assert (date(2026, 3, 7), "warning") in levels
    assert (date(2026, 3, 8), "critical") in levels  # 危急未被警告聚合吞掉
    lib.close()


# ---------- 场景二:每日额度 ----------

def test_daily_quota_50_with_aggregation(tmp_path):
    """同日60警告+10危急+5可聚合:新增恰为50且含全部10危急。"""
    # 2026-03-08 为春季跳时日(扫描03:00 EDT=07:00Z),
    # 3/9 扫描为 06:00Z,距前日仅23小时 -> 触发聚合。
    docs = []
    # 5条可聚合:3/8与3/9同为警告,3/9扫描距3/8仅23h -> 聚合不占额度
    for i in range(5):
        docs.append(Document(f"A-{i}", date(2026, 3, 21), "alice"))
    # 10条危急:3/8为警告(8天),3/9变为危急(7天),危急为新建
    for i in range(10):
        docs.append(Document(f"C-{i}", date(2026, 3, 16), "alice"))
    # 60条警告:3/8超出30天窗口,3/9进入窗口(30天)
    for i in range(60):
        docs.append(Document(f"W-{i:02d}", date(2026, 4, 8), "alice"))

    lib = make_lib(tmp_path)
    lib.initialize_documents(date(2026, 3, 8), docs)
    lib.advance_to(utc(2026, 3, 8, 8))
    assert len(lib.read_inbox("alice", 100)) == 15  # 5警告 + 10警告(未来的危急文档)

    lib.advance_to(utc(2026, 3, 9, 8))
    inbox = lib.read_inbox("alice", 200)
    day9 = [n for n in inbox if n.plan_date == date(2026, 3, 9)]
    assert len(day9) == 50                              # 新增恰为50
    crit = [n for n in day9 if n.level == "critical"]
    assert len(crit) == 10                              # 含全部10条危急
    warn_docs = sorted(n.doc_number for n in day9 if n.level == "warning")
    assert warn_docs == [f"W-{i:02d}" for i in range(40)]  # 字典序前40条
    # 可聚合候选:不新增、不占额度、occurrences+1
    for i in range(5):
        agg = [n for n in inbox if n.doc_number == f"A-{i}"]
        assert len(agg) == 1 and agg[0].occurrences == 2
        assert agg[0].plan_date == date(2026, 3, 8)     # 归属原计划日

    # 重复推进:不新增、被舍弃候选不复活
    lib.advance_to(utc(2026, 3, 9, 8))
    lib.advance_to(utc(2026, 3, 9, 12))
    assert len(lib.read_inbox("alice", 200)) == 65

    # 以后计划日仍可针对被舍弃文档产生候选:
    # 3/17 时 C 文档已过期、A 文档转为危急(5条), 额度空出45个 ->
    # 3/9 被舍弃的 W-40..W-44 首次获得通知
    lib.advance_to(utc(2026, 3, 17, 8))
    inbox = lib.read_inbox("alice", 300)
    day17 = [n for n in inbox if n.plan_date == date(2026, 3, 17)]
    assert len(day17) == 50
    revived = sorted(n.doc_number for n in day17
                     if n.doc_number >= "W-40")
    assert revived == [f"W-{i:02d}" for i in range(40, 45)]
    lib.close()


# ---------- 场景三:崩溃恢复与参考对照 ----------

def _dump_all(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT owner, doc_number, level, plan_date, scan_time_utc, "
        "expiry_date, days_to_expiry, occurrences FROM notifications"
    ).fetchall()
    conn.close()
    return [dict(owner=r[0], doc_number=r[1], level=r[2], plan_date=r[3],
                 scan_time_utc=r[4], expiry_date=r[5], days_to_expiry=r[6],
                 occurrences=r[7]) for r in rows]


class _Crash(Exception):
    pass


def test_crash_recovery_matches_reference(tmp_path):
    """部分负责人结果可见后强制终止,重启推进+重复推进,逐条对照参考。"""
    owners = [f"owner-{i:02d}" for i in range(10)]
    docs = []
    for i, owner in enumerate(owners):
        for j in range(20):
            # 到期日错开,覆盖警告/危急/无候选各档
            docs.append(Document(
                f"D-{i:02d}-{j:02d}",
                date(2026, 3, 1) .replace(day=1) if False else
                date.fromordinal(date(2026, 3, 2).toordinal() + j * 2 + i),
                owner))
    start = date(2026, 3, 1)
    t_final = utc(2026, 3, 8, 12)   # 连续7个计划日补跑

    expected = sorted(reference_run(docs, start, t_final), key=sort_key)

    db_path = str(tmp_path / "crash.db")
    store = Store(db_path)
    store.initialize_documents(start, docs)
    engine = Engine(store)
    commits = {"n": 0}

    def crash_hook(owner):
        commits["n"] += 1
        if commits["n"] >= 13:      # 第13个负责人-日提交后模拟强制终止
            raise _Crash()

    with pytest.raises(_Crash):
        engine.advance_to(utc(2026, 3, 2, 8), after_owner_commit=crash_hook)
    visible_before = _dump_all(db_path)   # 部分负责人当日结果已可见
    assert len(visible_before) > 0
    store.close()

    # 重启并推进至更晚日期,再重复推进
    store2 = Store(db_path)
    engine2 = Engine(store2)
    engine2.advance_to(t_final)
    engine2.advance_to(t_final)           # 重复推进幂等
    engine2.advance_to(utc(2026, 3, 2, 8))  # 向过去推进不产生任何变化
    store2.close()

    actual = sorted(_dump_all(db_path), key=sort_key)
    assert actual == expected             # 内容/等级/计划日/数量全部一致
    # 崩溃前已可见的通知全部保留
    for n in visible_before:
        assert n in actual
