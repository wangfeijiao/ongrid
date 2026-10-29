# 可观测性 / Observability

> **一句话定位**：指标告诉你出事了，追踪告诉你在哪出的事，日志告诉你为什么出的事。
> **One-liner**: Metrics tell you that something is wrong, traces tell you where, logs tell you why.

---

## 它能解决什么 / What It Solves

| 场景 | 你会遇到的问题 | 交付物 |
|---|---|---|
| 功能上线前埋点 | 上线后两眼一抹黑，靠用户投诉发现问题 | 从 2–4 个运维问题出发的信号选型表 |
| 日志查不动 | 散文式日志，grep 半天拼不出一次请求 | 结构化事件 + 强制关联 ID |
| 多入口共用一个日志汇 | 查不出这次运行是调度器、重放接口还是手工触发的 | 入口字段，在起点设置并随关联 ID 传播 |
| 指标后端被打爆 | 把 user_id 当标签，序列数量爆炸 | 有界标签集 + 基数红线清单 |
| 平均值骗人 | p50 很好看，1% 用户卡到超时 | 延迟直方图 + p50/p95/p99 |
| 跨服务慢 | 只知道慢，不知道慢在哪一跳 | OpenTelemetry 追踪 + 跨异步边界传播 |
| 告警没人看 | 天天响，被训练成无视 | 症状型告警 + runbook 链接 + 两级严重度 |
| 埋点自己写错了 | 出事时发现日志是 `[object Object]` | 遥测自验证：注入故障只靠遥测定位 |

---

## 快速开始 / Quick Start

中文：

```
这个支付重试功能要上线，帮我设计埋点：先列运维会问的问题，再决定加哪些日志和指标
```

```
我们的日志查不出一次请求，帮我改成结构化日志并加上关联 ID
```

```
这个任务被调度器、重放接口和 CLI 三种方式触发，日志里分不清是谁跑的，怎么办
```

```
帮我检查一下这些指标的标签有没有基数问题，延迟是不是只记了平均值
```

```
告警天天响没人看，帮我改成基于症状的告警，带 runbook 链接，只用两级严重度
```

English:

```
We're shipping payment retry — design the telemetry: on-call questions first, then which logs and metrics.
```

```
Our logs can't reconstruct a single request. Convert them to structured logs with correlation IDs.
```

```
This job is triggered by a scheduler, a replay endpoint and a CLI — the logs can't tell which ran it.
```

```
Review these metrics for cardinality problems and check whether latency is only tracked as an average.
```

```
Our alerts fire daily and get ignored. Make them symptom-based, with runbook links and two severities.
```

---

## 核心方法 / Core Method

1. **先定义"正常工作"**：写下 2–4 个运维问题，说不出来就不要埋点。
2. **按问题选信号**：日志答"这个 case 发生了什么"，指标答"多频繁/多快"，追踪答"时间花在哪"。
3. **结构化日志**：JSON 事件 + 稳定事件名 + 关联 ID + 入口字段 + 四级级别约定。
4. **RED / USE 指标**：请求型用 Rate/Errors/Duration，资源型用 Utilization/Saturation/Errors。
5. **基数即失效模式**：标签只取小固定集；高基数信息放日志和追踪。
6. **症状型告警**：可行动、带 runbook、阈值与持续时长有依据、只有 page 与 ticket 两级。
7. **验证遥测本身**：注入的故障必须只靠遥测定位，不需要读源码。

---

## 关键基准 / Key Benchmarks

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
| 追踪初始化 | 必须在任何其他模块之前导入 |
| 上下文传播 | HTTP 头、队列元数据，跨每个异步边界 |
| 采样 | 默认头部低采样；错误 100% 保留（若支持尾部采样） |
| 告警对象 | 症状（用户可感知），不是原因（CPU、内存） |
| 告警要素 | 可行动 + runbook 链接 + 有依据的阈值与持续时长 |
| 告警严重度 | 仅 2 级：page（立即处理）/ ticket（本周处理） |
| 遥测自验证 | 注入错误可查、序列正常、链路无断 span、每条告警试触发一次 |

---

## 文件结构 / Structure

```
observability-and-instrumentation/
├── SKILL.md                 # 平台规范 frontmatter + 英文核心方法论 + 语言路由
├── references/
│   ├── playbook.md          # 英文完整手册：七步流程、信号选型、结构化日志、RED/USE、追踪、告警、门禁
│   └── zh-CN.md             # 完整中文手册（结构与英文版一一对应）
├── README.md                # 本文件
├── _meta.json               # 发布元信息
└── _icon.png                # 技能图标
```

---

## 双语支持 / Bilingual

- 中文提问 → 自动加载 `references/zh-CN.md`，全程中文输出，注释与文档用中文。
- English or any other language → uses the English body and `references/playbook.md`.
- 两种语言的方法论、流程、基准数值与检查表完全一致，仅语言不同。

---

## 作者与版本 / Author & Version

- **作者 / Author**: zxh
- **版本 / Version**: 1.0.0
- **分类 / Category**: engineering
- **运行时 / Runtime**: generic
