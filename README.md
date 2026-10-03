# 合规文档到期提醒库

EOR 运营团队使用:每日扫描工作人员合规文档,按负责人生成到期提醒收件箱。
文档、处理进度、收件箱全部保存在本地 SQLite 数据库中。

## 能力

- `initialize_documents(start_date, documents)`: 初始化一批文档(唯一编号、
  到期本地日期、负责人),指定起始本地日期;初始化后不可重复调用。
- `advance_to(now_utc)`: 推进至给定 UTC 时刻,补齐所有计划时刻不晚于该时刻
  的扫描(可跨多个计划日补跑)。
- `read_inbox(owner, limit=50)`: 读取负责人最近 N 条通知。

## 规则摘要

- 业务时区固定 `America/New_York`,每日本地 02:00 扫描;春季跳时 02:00
  不存在时改为该日 03:00。
- 距到期 8~30 天产生警告,0~7 天产生危急(均含端点);过期或超过 30 天无候选。
- 同(负责人, 文档, 等级)距上次新建通知不足 24 小时则聚合(不新增、不占
  额度、不移动窗口起点);恰好 24 小时可新增。警告与危急分别聚合。
- 每负责人每计划日最多新增 50 条;候选按危急优先、到期日较早、编号字典序
  排列;聚合不占额度;超额候选当日永久舍弃,以后计划日可再产生候选。
- 每个(计划日, 负责人)的结果单事务提交:完整可见或完全不可见;已可见
  结果不受后续失败影响。进程被kill后重开数据库继续推进,结果与无中断
  处理完全一致;重复推进幂等。

## 用法

```python
from datetime import date, datetime, timezone
from compliance_reminders import ReminderLibrary, Document

lib = ReminderLibrary("reminders.db")
lib.initialize_documents(date(2026, 3, 1), [
    Document("DOC-001", date(2026, 3, 20), "alice"),
    Document("DOC-002", date(2026, 4, 1), "bob"),
])

# 每日定时调用;停跑恢复后同样调用,自动补齐漏掉的计划日
lib.advance_to(datetime.now(timezone.utc))

for n in lib.read_inbox("alice"):
    print(n.level, n.doc_number, n.plan_date, n.days_to_expiry)
lib.close()
```

`Notification` 字段: `owner` / `doc_number` / `level`(`warning`|
`critical`) / `plan_date`(所属计划本地日期) / `scan_time_utc` /
`expiry_date` / `days_to_expiry` / `occurrences`(聚合次数)。

## 测试

```bash
python3 -m pytest tests/            # 验收测试(DST聚合、额度、崩溃恢复)
python3 -m pytest tests/ -m slow    # 10万文档/2000负责人/7日补跑性能测试
```
