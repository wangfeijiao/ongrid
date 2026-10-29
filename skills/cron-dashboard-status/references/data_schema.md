# 数据源结构与取数逻辑（cron-dashboard-status）

本文件记录 WorkBuddy 定时任务(automation)的真实数据位置、字段含义与「坑」，供维护脚本时参考。以下结论均为在本机实测确认，非猜测。

## 数据源

| 来源 | 路径 | 内容 |
|------|------|------|
| SQLite | `~/.workbuddy/workbuddy.db` | 任务定义 + 执行记录 |
| 日志 | `~/.workbuddy/logs/automation.log` | 调度层时间线（成败、失败原因） |

## workbuddy.db 关键表

### automations（任务定义）

| 字段 | 含义 |
|------|------|
| id | 任务唯一 ID（UUID） |
| name | 任务名称 |
| schedule_type | `recurring` / `once` |
| status | `ACTIVE` / `PAUSED` |
| rrule | recurring 任务的 RRULE 字符串 |
| scheduled_at | once 任务的执行时间 |
| next_run_at / last_run_at | 毫秒时间戳 |
| deleted_at | 非空 = 软删除，默认应过滤掉 |
| owner_status | 如 `confirmed` |

### automation_runs（执行记录）

| 字段 | 含义 |
|------|------|
| automation_id | 关联任务 |
| runs_json | **核心**：JSON 数组，每条含 `success`(bool) / `startedAt` / `finishedAt` / `output` |
| thread_title | 会话标题（含输出摘要，runs_json 为空时兜底） |
| metadata_json | 含 `resultState` / `runKind` 等 |
| result_success | **误导字段**，见下方「坑」 |

### automation_runtime_state

`automation_id` / `last_run_at` / `last_error` / `running` 等，运行态快照。

## 必须知道的「坑」（实测结论）

1. **`automation_runs.status` 恒为 `'ACCEPTED'`、`result_success` 恒为 `1`**。
   它们只标记「任务被接受、会话被创建」，**不能**用来判断任务是否真正成功。

2. **真正的成败在两个地方，维度不同**：
   - `automation_runs.runs_json[].success` = **会话层**是否成功产出结果。
   - `automation.log` 里 `run finished ... success=false` / `run failed for ...` = **调度层**是否成功。
   一次调度失败（如「连接器未连接」→ 任务被终止）时，runs_json 里可能仍有一条 success=true
   的会话记录，因为那是一次独立的手动/测试会话。

3. **判断「漏跑/连续失败」必须用 automation.log 的时间线**，不要用 runs_json 的 success 序列。
   因为调度失败才是「漏跑」，会话成功不能抵消漏跑。

4. `automations` 表里可能有多条带 `deleted_at` 的软删除任务，默认应过滤。

## automation.log 行格式（解析用）

```
2026-10-13T11:51:15.058Z [INFO] [LocalAutomationScheduler] dispatch automation <id> (<name>): ...
2026-10-13T11:51:15.059Z [INFO] [LocalAutomationScheduler] run start: id=<id>, name=<name>, ...
2026-10-13T11:51:15.379Z [WARN] [LocalAutomationScheduler] run failed for <id>: [UNKNOWN] <原因>
2026-10-13T11:51:15.381Z [INFO] [LocalAutomationScheduler] run finished: id=<id>, name=<name>, success=false, finishedAt=<ms>
```

解析要点：
- 行首是 UTC 时间 `YYYY-MM-DDTHH:MM:SS(.mmm)Z`，需转本地时间展示。
- `run failed for <id>` 行携带**具体失败原因**（如「连接器 ima-mcp 未连接」）。
- `run finished ... success=(true|false)` 行只带成败标记，不带原因。

## 字段映射（脚本输出）

脚本 `scripts/collect_cron_status.py` 输出每个任务：

- `latest_success`：最近一次调度是否成功（权威，来自 log；无 log 时退回 runs_json）
- `session_success`：最近一次会话是否成功（来自 runs_json，仅参考）
- `consecutive_failures`：连续失败/漏跑次数（来自 log 时间线，从最近往前数）
- `fail_reason`：最近一次失败的具体原因
- `latest_output`：最近一次会话的输出文本（前 N 行）
- `latest_started_at` / `latest_finished_at`：最近一次会话起止时间
