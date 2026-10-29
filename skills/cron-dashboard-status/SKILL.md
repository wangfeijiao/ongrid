---
name: cron-dashboard-status
version: 0.1.0
display_name: "定时任务仪表板状态"
display_name_en: "Cron Dashboard Status"
description: "定时任务(cron/automation)运行状态仪表板。当用户需要一眼看清所有定时任务的运行状态、查看每个任务最近一次执行时间与成功/失败结果、调取最近输出内容，或标记连续漏跑 2 次及以上的任务以排查稳定性问题时使用。触发词如「定时任务状态」「cron 仪表板」「自动化任务看板」「哪些定时任务失败了」「任务最近输出」。"
description_zh: "定时任务(cron/automation)运行状态仪表板。列出所有已配置定时任务、展示最近执行时间与成功/失败状态、最近输出内容，并标记连续漏跑 2 次及以上的任务，用于快速定位失败任务和排查稳定性问题。"
description_en: "A status dashboard for scheduled tasks (cron/automation). List all configured tasks, show last-run time and success/failure, recent output, and flag tasks with 2 or more consecutive misses to quickly locate failures and stability issues."
agent_created: true
---

# 定时任务仪表板状态（Cron Dashboard Status）

## 用途

把 WorkBuddy 里的定时任务(automation)运行状态汇总成一张可快速扫读的仪表板，让用户一眼看清：

1. 有哪些已配置的定时任务；
2. 每个任务最近一次执行时间，以及成功/失败状态；
3. 每个任务最近的输出内容（默认前 10 行）；
4. 哪些任务连续漏跑(失败) 2 次及以上，需要提前处理稳定性问题。

## 何时使用

- 用户问「我的定时任务都什么状态」「哪些 cron 失败了」「任务有没有漏跑」；
- 用户想快速定位失败任务及其最近输出，缩短排查时间；
- 用户想识别连续漏跑的任务，提前发现稳定性隐患。

## 执行流程

### 第 1 步：运行采集脚本

先运行脚本拿到结构化数据（脚本已封装好全部取数逻辑，无需手写 SQL 或解析日志）：

```bash
python scripts/collect_cron_status.py
```

- 脚本默认读 `~/.workbuddy/workbuddy.db` 和 `~/.workbuddy/logs/automation.log`。
- 参数：`--db` 指定数据库、`--log` 指定日志、`--out` 写 JSON 文件、`--tail N` 控制「最近输出」截取行数（默认 10）。
- 输出是一个 JSON，顶层含 `task_count` 和 `tasks` 数组，每个任务含 `name`、`latest_success`、`consecutive_failures`、`fail_reason`、`latest_output` 等字段。

### 第 2 步：组织成仪表板

拿到 JSON 后，按以下结构组织成清晰的仪表板（推荐用表格 + 状态标记）：

1. **概览区**：任务总数、失败任务数、连续漏跑(≥2次)任务数，给出一句话结论。
2. **任务状态表**：每行一个任务，列包含：
   - 任务名称
   - 调度方式（recurring 的 RRULE 用大白话表达，once 显示执行时间）
   - 最近执行时间
   - 状态标记（✅ 成功 / ❌ 失败，连续漏跑≥2 次加 ⚠️）
   - 连续失败次数
3. **失败任务明细**（如有）：逐个列出失败原因（`fail_reason`，通常是「某连接器未连接」之类）。
4. **最近输出区**：每个任务展示 `latest_output`（默认前 10 行），让用户直接看到上次跑出来的结论。

### 第 3 步：解读与建议

- **连续漏跑 ≥2 次**的任务要重点标注，并给出可执行的下一步（如「检查连接器授权，可能需要重新登录」）。
- 若某任务 `fail_reason` 提到具体连接器未连接，直接提示用户去重连该连接器。

## 数据来源与关键事实（务必先读）

脚本内部封装了取数逻辑，但维护或扩展前必须先读 `references/data_schema.md`，其中有几个实测确认、容易踩的坑：

- `automation_runs.status` 恒为 `ACCEPTED`、`result_success` 恒为 `1`，**不能**用来判断任务成败。
- 真正的成败分两层：`runs_json[].success`（会话层）与 `automation.log`（调度层）。
- **判断「漏跑/连续失败」必须用 `automation.log` 时间线**，不能用 `runs_json` 的成功序列。

## 资源

- `scripts/collect_cron_status.py` — 采集脚本，封装全部取数逻辑，输出 JSON。
- `references/data_schema.md` — 数据源结构、字段含义与已知坑的详细说明。
