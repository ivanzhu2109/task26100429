# 合规文档到期提醒库

基于 Python + SQLite 的本地库，由上层应用调用。文档、处理进度与收件箱全部保存在本地 SQLite 数据库中，无需任何外部服务。

## 使用

```python
from datetime import date, datetime, timezone
from compliance_reminders import ReminderLibrary, Document

lib = ReminderLibrary("reminders.db")

# 1. 初始化一批文档（仅可调用一次；起始本地日期 + 不可变文档批次）
lib.initialize_documents(date(2026, 3, 1), [
    Document(doc_id="D001", expiry_date=date(2026, 3, 20), owner="alice"),
    Document(doc_id="D002", expiry_date=date(2026, 4, 1), owner="bob"),
])

# 2. 推进至给定 UTC 时刻：处理所有计划时刻不晚于该时刻的扫描（可补跑多日）
lib.advance_to(datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc))

# 3. 读取负责人收件箱（最新在前，默认最近 50 条）
for n in lib.read_inbox("alice"):
    print(n.scan_local_date, n.level, n.doc_id, n.aggregate_count)

lib.close()
```

## 规则摘要

- 业务时区固定 `America/New_York`，每日本地 02:00 扫描；春季跳时当天 02:00 不存在时改 03:00。
- 距到期 8–30 天产生警告，0–7 天产生危急（含端点）；过期或超过 30 天不产生候选。
- 同负责人 + 同文档 + 同等级：距最近一次新建通知的计划时刻不足 24 小时则聚合（`aggregate_count` 递增），不新增、不占额度；恰好 24 小时可新增。
- 每负责人每计划日最多新增 50 条；危急优先，其次到期日较早、文档编号字典序。超额候选当日永久舍弃，次日重新按候选评估。
- 每个负责人的当日结果在单个事务中提交：要么完整可见，要么完全不可见；进程崩溃后重开数据库继续推进，结果与无中断执行完全一致，重复推进幂等。

## 测试

```bash
python3 -m pytest tests/ -q
```

覆盖：春季 23 小时间隔聚合、秋季 25 小时间隔与恰好 24 小时边界、单日 50 条额度与危急优先、崩溃重启后与独立参考计算逐条对照、10 万文档 / 2000 负责人 / 7 天补跑的性能与处理期间并发可读。
