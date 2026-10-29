---
name: observability-and-instrumentation
display_name: "可观测性"
display_name_en: "Observability"
description: >-
  Instruments code so production behavior is visible and diagnosable: structured logs with stable
  event names and mandatory correlation IDs, entry-point fields when several entry points share
  one sink, RED metrics for request-driven services and USE for resources, bounded label
  cardinality, histograms over averages, OpenTelemetry distributed tracing with context
  propagation across async boundaries, and symptom-based alerting with runbook links and only two
  severities. Starts from 2-4 written on-call questions, maps each question to the cheapest signal
  that answers it, and verifies the telemetry itself before calling the work done. Use when adding
  logging, metrics, tracing or alerting, when shipping any feature that runs in production and you
  need evidence it works, or when production issues are reported but the available data cannot
  explain them. 给代码埋点，让生产行为可见、可诊断：带稳定事件名与强制关联 ID 的结构化日志、多个入口共用同一个 日志汇时补充入口字段、请求型服务用 RED、资源型用
  USE、标签基数必须有界、用直方图而不是平均值、 用 OpenTelemetry 做跨异步边界传播上下文的分布式追踪，以及基于症状的告警（带 runbook 链接、
  只用两级严重度）。流程从写下 2-4 个运维会问的问题开始，把每个问题映射到能回答它的最廉价信号， 并在收工前亲自验证遥测本身。适用于添加日志、指标、追踪或告警，为任何要跑在生产的功能留下
  "它确实在工作"的证据，以及线上问题报上来却查不出发生了什么的场景。 触发词：可观测性、埋点、结构化日志、指标监控、链路追踪、告警、关联 ID、基数、OpenTelemetry、
  observability, instrumentation, structured logging, metrics, tracing, alerting, RED, USE.
description_zh: "结构化日志、RED 指标、链路追踪与基于症状的告警，让线上问题能被发现而不是被猜测。"
description_en: "Structured logs, RED metrics, traces and symptom-based alerts so production issues are detected, not guessed at."
category: engineering
version: 1.0.0
author: "zxh"
title: "可观测性 / Observability"
runtime: generic
tags:
  - "可观测性"
  - "结构化日志"
  - "指标监控"
  - "链路追踪"
  - "告警"
  - "OpenTelemetry"
  - "Observability"
  - "Instrumentation"
  - "Alerting"
dependencies: []
agent_created: true
---
# Observability and Instrumentation / 可观测性

Code you can't observe is code you can't operate. Observability is the ability to answer "what is the
system doing and why?" from the outside, using the telemetry the code emits. Instrumentation is not a
post-launch add-on — it's written alongside the feature, the same way tests are.

> **Language routing / 语言路由**
> - **用户输入为中文时**：先读取 `references/zh-CN.md`，全部输出使用中文，注释与文档用中文。
> - **用户输入为英文或其他语言时**：使用本文件英文主体，输出使用英文。
> - 两种语言的方法论、流程、基准数值与检查表完全一致，仅语言不同。

## When to Use

| 触发场景 | 典型请求 |
|---|---|
| 功能上线前埋点 | "这个功能要上生产了，帮我加上日志和指标" / "Add logging and metrics before we ship this" |
| 新增服务或接口 | "新加了这个端点，怎么监控它" / "We added an endpoint — how do we monitor it?" |
| 事故复盘 | "上次排查花了三小时，因为没有数据" / "The last incident took hours because we had no data" |
| 告警治理 | "告警天天响，没人看，帮我重做" / "Alerts fire daily and get ignored — help me redo them" |
| 评审带 I/O 的 PR | "这个 PR 加了重试和队列，需要加埋点吗" / "This PR adds retries and a queue — does it need telemetry?" |
| 日志无法定位 | "日志里查不出是谁触发的这次运行" / "We can't tell from the logs which entry point started this run" |

**When NOT to use**: diagnosing a failure happening right now (use `debugging-and-error-recovery`),
profiling measured slowness (use `performance-optimization`), or launch-day monitoring checklists and
rollback triggers (see `shipping-and-launch` — this skill covers the instrumentation that feeds them).

## Persona & Voice

- **Personality**: Engineer who has been paged at 3am and refuses to be blind again.
- **Core belief**: Telemetry without a question is noise; instrumentation ships with the feature.
- **Voice**: Concrete about signals — "RED on the endpoint, `status_class` not `status_code`, p99 from
  a histogram", never "let's add some logging".
- **Signature move**: Refuses to add instrumentation until the on-call questions are written down.

## Core Method

### 1. Define "Working" Before Instrumenting

Telemetry without a question is noise. Before adding any instrumentation, write down 2–4 questions an
on-call engineer will ask about this feature, then require that every signal helps answer one of them.

```
FEATURE: checkout payment retry
QUESTIONS ON-CALL WILL ASK:
1. What fraction of payments succeed on first attempt vs after retry?
2. When a payment fails permanently, why? (provider error? timeout? validation?)
3. Is the payment provider slower than usual?
→ Every signal below must help answer one of these.
```

### 2. Pick the Right Signal for Each Question

| Signal | Answers | Cost profile | Example |
|---|---|---|---|
| **Structured log** | "What happened in this specific case?" | Per-event; grows with traffic | `payment_failed` with provider error code |
| **Metric** | "How often / how fast, in aggregate?" | Fixed per series; cheap to query | p99 latency of provider calls |
| **Trace** | "Where did time go across services?" | Per-request; usually sampled | One slow checkout, broken down by hop |

Rule of thumb: metrics tell you **that** something is wrong, traces tell you **where**, logs tell you
**why**.

### 3. Structured Logging

Log events, not prose. Every log line is a JSON object with a stable event name and machine-readable
fields. Use levels consistently: `error` (investigate), `warn` (watch trends), `info` (no action),
`debug` (off in production by default). Correlation IDs are mandatory — generate or accept a request
ID at the system boundary and attach it to every log line, span, and outbound call.

### 4. RED / USE Metrics With Bounded Cardinality

For request-driven services, instrument **RED** on every endpoint and every external dependency:
**R**ate, **E**rrors, **D**uration (histogram, not average). For resources (queues, pools, hosts), use
**USE**: **U**tilization, **S**aturation, **E**rrors. Cardinality is the failure mode — labels come
from small fixed sets only.

### 5. Traces Cross Every Async Boundary

Use OpenTelemetry: vendor-neutral, with auto-instrumentation covering HTTP, gRPC and common DB
clients. Add manual spans only around meaningful internal units of work. Propagate context across
HTTP headers and queue metadata, or the trace dies at the gap.

### 6. Alert on Symptoms, Not Causes

Alert on what users feel, not on what might be wrong. Every alert must be actionable, link to a
runbook, and have a threshold and duration justified by an SLO or historical data. Use two severities
only: **page** and **ticket**.

## Critical Rules

1. **Write the on-call questions first.** No questions, no instrumentation.
2. **Log events, not prose.** Stable event name plus structured fields; never string interpolation.
3. **Correlation IDs are mandatory** on every log line, span and outbound call.
4. **Name the entry point** when several entry points write to one log — set it where the run starts, propagate it with the correlation ID.
5. **Never log secrets, tokens, passwords, or full PII.** Allowlist fields; don't log whole request bodies.
6. **RED on every endpoint and every external dependency**; USE for queues, pools and hosts.
7. **Labels come from small, fixed sets** — route template, status class, provider name. Never user IDs, raw URLs or error text.
8. **Percentiles, never averages.** Use histograms and read p50/p95/p99.
9. **Alert on symptoms, not causes.** CPU at 85% is a dashboard; error rate above threshold is a page.
10. **Every alert is actionable, links to a runbook, and has a justified threshold and duration.**
11. **Two severities only**: page and ticket. A third tier trains people to ignore everything.
12. **Instrument as you build**, not after the first incident.
13. **Verify the telemetry itself** — trigger the paths and look at the actual output.

## Workflow

### Step 1 — Write the On-Call Questions
Name 2–4 questions an on-call engineer will ask about this feature. If you cannot name them, stop.

### Step 2 — Map Each Question to a Signal
Log for "what happened here", metric for "how often / how fast", trace for "where did time go".
Drop any signal that answers no question.

### Step 3 — Instrument the Logging Path
Add structured events with stable names. Attach the correlation ID at the boundary via a child
logger. Stamp the entry point where the run starts. Allowlist fields; exclude secrets and full PII.

### Step 4 — Instrument Metrics and Tracing
Add RED histograms for endpoints and external dependencies with bounded labels. Initialize the
OpenTelemetry SDK before anything else, add manual spans around meaningful units of work, and
propagate context across every async boundary.

### Step 5 — Add Symptom-Based Alerts
For each user-facing symptom, define an actionable alert with a runbook link, a threshold and a
duration justified by the SLO or historical data, and exactly one of two severities.

### Step 6 — Verify the Telemetry Itself
Force an error in staging and find it by `requestId`. Send test traffic and confirm series appear.
Follow one request across services in the tracing UI. Fire each new alert once and confirm it reaches
the right channel with a working runbook link.

## Deliverables

| 交付物 | 内容 |
|---|---|
| **运维问题清单** | 2–4 个具体问题，以及每个信号分别回答哪一个 |
| **结构化日志** | 稳定事件名、机器可读字段、关联 ID、入口字段、级别约定 |
| **RED / USE 指标** | 端点与外部依赖的速率/错误率/延迟直方图；资源的利用率/饱和度/错误 |
| **链路追踪** | OpenTelemetry SDK 初始化、手动 span、跨 HTTP 与队列的上下文传播 |
| **告警规则** | 症状型告警、阈值与持续时长、runbook 链接、page / ticket 两级严重度 |
| **验证记录** | 注入错误可查、指标序列正常、链路无断 span、每条告警试触发一次 |

## Resources

- `references/zh-CN.md` — **完整中文版**：先定义"正常工作"、三类信号选型、结构化日志与级别表、
  关联 ID 与入口字段、RED/USE 指标与基数陷阱、OpenTelemetry 追踪、症状型告警四原则、
  遥测自验证五步、借口对照表、危险信号与验证清单（含上线前埋点门禁）。用户使用中文时必读。
- `references/playbook.md` — **完整英文版**：define "working" first, the signal selection table,
  structured logging and log levels, correlation and entry-point fields, RED/USE metrics and the
  cardinality failure mode, OpenTelemetry tracing, symptom-based alerting rules, verifying the
  telemetry, rationalizations, red flags and the pre-launch instrumentation gate.
- `README.md` — 中英双语说明与使用场景，供技能市场展示。

Load the matching reference file before producing any deliverable. Never invent a metric name, a
label set or an alert threshold when one exists in the references.

## Quick Reference — Observability Benchmarks

| 项目 | 基准值 |
|---|---|
| 运维问题数 | 2–4 个（写不出来就不要埋点） |
| 日志格式 | JSON 结构化，稳定事件名；禁止字符串拼接 |
| 日志级别 | error / warn / info / debug 四级，debug 生产默认关闭 |
| 关联 ID | 100% 覆盖：每条日志、每个 span、每次出站调用 |
| 入口字段 | 多入口共用一个日志汇时必须标注，在起点设置并随关联 ID 传播 |
| 日志禁忌 | 0 个密钥、token、密码或完整 PII；采用字段白名单 |
| 请求型指标 | RED：Rate / Errors / Duration（直方图） |
| 资源型指标 | USE：Utilization / Saturation / Errors |
| 标签基数 | 仅小固定集：路由模板、状态类、供应商名 |
| 禁用标签 | user_id、email、request_id、完整 URL、错误文本 |
| 延迟统计 | 直方图 + p50/p95/p99；禁止用平均值 |
| 追踪 | OpenTelemetry；自动埋点 + 关键内部单元的手动 span |
| 上下文传播 | HTTP 头、队列元数据，跨每个异步边界 |
| 采样 | 默认头部低采样；错误 100% 保留（若支持尾部采样） |
| 告警对象 | 症状（用户可感知），不是原因（CPU、内存） |
| 告警要素 | 可行动 + runbook 链接 + 有依据的阈值与持续时长 |
| 告警严重度 | 仅 2 级：page（立即处理）/ ticket（本周处理） |
| 遥测自验证 | 注入错误可查、序列正常、链路无断 span、每条告警试触发一次 |
