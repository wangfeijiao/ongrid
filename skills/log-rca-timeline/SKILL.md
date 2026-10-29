---
name: log-rca-timeline
version: "1.0.0"
display_name: "日志根因定位"
display_name_en: "Log RCA Timeline"
description: "按时间窗口聚合日志、识别异常模式并给出根因假设排序，输出可复盘的故障时间线。支持纯文本、JSONL、CSV 三类日志，自动识别 ISO8601 / nginx / syslog / epoch 等时间戳格式，用中位数加 MAD 稳健基线做双阈异常检测，覆盖流量突增与断流、错误率飙升、致命错误突现、延迟抬升与 P99 长尾、新错误签名涌现、重试风暴、单服务集中、级联传播、阶跃漂移、日志静默、周期性毛刺、饱和度线索共十四类模式，再按形态覆盖度、证据分级、时序先后与集中度对十四条候选根因打分排序，每条附验证动作与可排除条件，产出带聚合序列图、异常模式摘要、根因假设排序与故障时间线的 HTML 看板。This skill should be used when the user asks for 日志分析、故障根因定位、根因分析、RCA、故障复盘、故障时间线、异常日志排查、错误率飙升排查、接口超时定位、502/504 排查、OOM 排查、连接池耗尽排查、慢查询定位、重试风暴分析、日志突增排查、日志断流排查、上线后报错分析、线上问题定位、事故复盘、incident postmortem、log analysis、root cause analysis 等。"
description_zh: "把日志按时间窗口聚合成序列，用中位数加 MAD 稳健基线做双阈异常检测，识别十四类异常模式，再对十四条候选根因打分排序并给出验证动作与可排除条件，输出含聚合序列图、异常模式摘要、根因假设排序与故障时间线的 HTML 看板。支持文本、JSONL、CSV 日志。"
description_en: "Aggregates logs into time windows, detects anomalies against a median-plus-MAD robust baseline with dual thresholds, recognises fourteen patterns (traffic surge and blackout, error-rate spike, fatal burst, latency climb and P99 tail, new error signature emergence, retry storm, single-service concentration, cascade propagation, step shift, log silence, periodic glitch, saturation cues), then ranks fourteen candidate root causes by pattern coverage, evidence tier, temporal precedence and concentration, each with concrete verification steps and falsification conditions, delivered as an HTML dashboard carrying the aggregated series chart, pattern summary, ranked hypotheses and an incident timeline. Accepts plain text, JSONL and CSV logs with automatic timestamp recognition. Output is a ranked hypothesis set, not a verdict, and must be cross-checked against metrics, traces and change records."
author: "svan"
license: "MIT"
agent_created: true
---

# 日志分析与故障根因定位（Log RCA Timeline）

回答一个问题：**「这段时间的日志里到底发生了什么、什么时候开始、在哪里集中、最可能是哪几个原因，以及接下来先验证什么？」**

本技能是**统计证据引擎 + 假设排序器**，不是「读一段日志猜原因」的经验工具。所有结论必须落在由日志本身算出的聚合数字上。

---

## 一、与同类技能的分界（避免重复）

| 技能 | 它做什么 | 本技能怎么做 |
|---|---|---|
| `log-trace-rca`（市场版） | 关联日志/链路/指标/配置多源数据 | 本技能**只吃日志**，但把日志榨干：逐窗统计 + 形态识别 + 假设打分，产出物是可复盘的 HTML 看板而非文本结论 |
| `log-fault-analyzer`（市场版） | 粘贴单段日志文本，输出排查清单 | 本技能面向**文件与时间跨度**，核心是时间窗聚合与异常定位；单段文本即时问答不必动用本技能 |
| `timeseries-anomaly-radar` | 纯数值序列的统计体检（离群点/变点/自相关/游程） | 本技能面向**原始日志文本**，内含解析、签名归一化与语义归因；若已有一列干净的数值指标，优先用前者 |
| 通用「故障复盘」写作类技能 | 写文档、套模板 | 本技能产出数据与假设，文档撰写属后续环节 |

---

## 二、硬约束（先读，违反即返工）

1. **先聚合后归因。** 不允许跳过统计直接对单条日志下结论。没有 `buckets` 就没有 `patterns`，没有 `patterns` 就不允许输出 `hypotheses`。
2. **结论是假设，不是定论。** 任何交付都必须带上「验证动作」与「可排除条件」，并在看板与回复中声明本性质。
3. **不得编造数值。** 所有峰值、基线、倍数、时间点必须由 `analyze_logs.py` 计算得出；引用日志原文时必须照抄且带时间。
4. **不得臆测未提供的信息。** 没有告警时刻就不输出 MTTD；没有变更记录就不给变更类假设加时序满分。
5. **不越权处置。** 本技能不下发修改、不重启服务、不改配置；只输出验证动作与修复方向。
6. **数据边界。** 日志可能含手机号、身份证、令牌、内网地址。脚本只统计与截断展示（每条样本截 180–220 字符），**不得把敏感字段整理成清单输出**；如确需展示，先脱敏。

---

## 三、运行环境（本机实测）

| 项 | 结论 |
|---|---|
| 命令通道 | Bash 优先；不可用时改用 PowerShell（先设 `PYTHONIOENCODING=utf-8`） |
| Python | `%USERPROFILE%/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（标准库即可；生成图标需 Pillow） |
| Node | `%USERPROFILE%/.workbuddy/binaries/node/versions/<版本>/node.exe`（仅看板自检需要） |
| 中文落盘 | 统一 `encoding="utf-8"` |
| 工作目录 | `<当前工作目录>/日志根因定位/<对象>_<YYYY-MM-DD>/`，内含 `data.json`、`report.html` |

---

## 四、工作流

### Step 0 · 确认输入与口径

至少问清三件事（缺省则按默认值推进并在回复中说明，不要反问阻塞）：

1. **日志在哪里**：文件路径或目录（目录用 `--glob` 限定，默认 `*.log`）
2. **时间范围**：默认取全量；有明确故障时段时用 `--start` / `--end` 聚焦
3. **窗口粒度**：默认 `auto`（约 80 个窗口）；已知是瞬时抖动类故障时改小到 `30s` / `1m`

可选补充（显著提升结论质量）：`--changes` 变更记录文件、`--alert` 告警时刻、`--assume-date` 补日期。

### Step 1 · 跑聚合与检测

```bash
PY="%USERPROFILE%/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" -X utf8 "<技能目录>/scripts/analyze_logs.py" \
    --input <日志文件或目录> \
    --window auto \
    --changes <变更记录.txt> \
    --alert "2026-09-28 10:47" \
    --top 5 \
    --title "<对象> 故障分析" \
    --out "<run_dir>/data.json"
```

**先看退出码与解析率**：

- 退出码非 0 → 按提示修输入（多半是时间戳解析不出来）
- 解析率 < 90% → **停**，先修解析。堆栈行、多行异常往往正是漏掉的部分，会系统性低估错误量

### Step 2 · 读结果并对齐业务事实

打开 `data.json` 逐项核对：

1. `patterns` 里是否有「明显是业务周期」的误报（日切、批处理、对账）；有则在后续回复中显式说明，**不要静默丢弃**
2. `cascade` 的 `chain` 是否给出合理的服务恶化顺序
3. `summary` 中的影响时长与业务侧感知是否一致；不一致则检查窗口粒度是否过大
4. `flags` 中的每一条局限是否需要在结论里降权

### Step 3 · 生成看板并自检

```bash
"$PY" -X utf8 "<技能目录>/scripts/build_board.py" "<run_dir>" --out report.html

NODE="%USERPROFILE%/.workbuddy/binaries/node/versions/<版本>/node.exe"
"$NODE" "<技能目录>/scripts/render_check.js" "<run_dir>/report.html" "<run_dir>/render_check.txt"
```

`render_check.js` 退出码 `0` 才可交付。它会在最小 DOM 桩里真实执行内联渲染脚本，并触发筛选、搜索、序列切换三条交互路径——**静态检查全绿但页面空白**的问题只能靠这一步发现。

### Step 4 · 交付

`present_files` 打开 `report.html`，回复中给出：

- **影响与时间线**：首个异常时刻、影响时长、峰值错误率、恢复时刻（含 MTTD/MTTR，若可算）
- **异常模式**：高严重度模式逐条（形态、集中服务、峰值/基线倍数）
- **根因假设 Top 3**：名称、分数、置信度、**最关键的验证动作**、**什么情况可排除**
- **必须区分的三件事**：真实流量放大 vs 日志放大；业务故障 vs 采集断流；首发环节 vs 被拖垮环节
- **全部待核验项**：解析率不足、周期误报、缺耗时字段、窗口数偏少等
- **固定声明**：结论为假设排序，须与指标/调用链/变更记录交叉验证；日志不能证明「为什么」

---

## 五、异常模式库（14 类，判据见 `references/anomaly-patterns.md`）

| 严重度 | 模式 | 一句话判据 |
|---|---|---|
| 高 | `traffic_spike` 流量突增 | 日志量稳健 z ≥ 3.5 且 ≥ 2 × 基线 |
| 高 | `traffic_drop` 断崖/断流 | 日志量 ≤ 25% 基线且 z ≤ −3.0 |
| 高 | `error_burst` 错误率飙升 | 错误量 z ≥ 3.5 且 ≥ 3 × 基线 |
| 高 | `fatal_spike` 致命错误突现 | FATAL ≥ 3 且 > 3 × 中位数 |
| 高 | `retry_storm` 重试风暴 | 同签名单窗 ≥ 50 且 ≥ 5 × 上一窗 |
| 高 | `cascade` 级联传播 | ≥ 2 服务错误起始相差 ≥ 2 窗 |
| 中 | `latency_climb` 延迟抬升 | P95 z ≥ 3.5 且 ≥ max(2×基线, 基线+100ms) |
| 中 | `slow_p99` 长尾恶化 | 同窗 P99 ≥ 3 × P95 |
| 中 | `new_error_pattern` 新签名涌现 | 新错误签名 ≥ 2 且占 ≥ 50% |
| 中 | `concentrate_service` 单服务集中 | 单服务占窗口错误 ≥ 70% |
| 中 | `step_shift` 阶跃/漂移 | 后半段中位数 ≥ 2 × 前半段 |
| 中 | `saturation` 饱和度线索 | 命中池/内存/磁盘/队列关键词 |
| 中 | `silent_gap` 日志静默 | 连续 ≥ 2 个非首尾窗口零日志 |
| 低 | `periodic_glitch` 周期性毛刺 | ≥ 3 个异常窗且间隔 CV < 0.35 且平均间隔 ≥ 2 窗 |

**统计底座**：两遍法 `median + 1.4826 × MAD` 稳健 z，**双阈**（z 阈 + 幅度阈）联合判定，只为控制误报。

---

## 六、根因假设排序（详见 `references/rca-ranking.md`）

```
score = 100 × clamp(
    0.40 × 形态覆盖度 + 0.28 × 证据强度 + 0.14 × 时序吻合
  + 0.10 × 形态先验  + 0.08 × 集中度    − 惩罚项 )
```

- **证据分级**：直接证据（`OOMKilled` / `pool exhausted` / `no space left` / `deadlock`）＞ 强相关（时间吻合 + 集中度）＞ 弱线索（仅时间吻合）
- **时序硬约束**：线索出现晚于效应起始的，判为次生现象并降权；变更类假设在变更与异常相差 ≤ 30 分钟时给时序满分
- **置信度**：A ≥ 65 分，B 40–64 分，C < 40 分（一般不输出）
- **候选因库 14 条**：下游依赖故障、数据库慢查询/锁等待、连接池耗尽、内存溢出/GC、CPU 饱和、磁盘写满、网络/DNS/证书、近期发布或配置变更、外部流量突增、缓存击穿、MQ 堆积重试、限流鉴权、代码缺陷、日志采集侧异常

每条假设必须写全五项：**陈述 / 证据 / 验证动作 / 可排除条件 / 修复方向**。缺「可排除条件」的不算假设。

---

## 七、故障时间线（详见 `references/timeline-postmortem.md`）

七阶段：**观察起点 → 潜伏 → 触发 → 放大 → 发现（告警）→ 缓解/恢复 → 观察终点**。

- 恢复判定：异常结束后**连续 3 个窗口**错误率 ≤ 基线 1.5 倍且未断流
- **潜伏段是最有改进价值的一段**：若从潜伏到触发有 20 分钟而监控毫无反应，改进项就是补告警
- 口径：`MTTD = 告警时刻 − 首个异常窗口`（需用户提供告警时刻）；`MTTR = 恢复窗口 − 首个异常窗口`
- 时间线只放事实，推测一律进「根因假设」章节

---

## 八、命令参数速查

| 参数 | 说明 |
|---|---|
| `--input` | 必填，日志文件/目录/通配符，可多个 |
| `--glob` | 目录内匹配模式，默认 `*.log`（同时收 `*.txt`） |
| `--window` | `auto` 或 `30s` / `5m` / `1h` / `1d` |
| `--start` / `--end` | 时间过滤，格式 `YYYY-MM-DD HH:MM:SS` |
| `--changes` | 变更记录文件，每行一条：时间 + 说明 |
| `--alert` | 告警触发时刻，用于算 MTTD |
| `--assume-date` | 日志只有时分秒时补齐日期 |
| `--top` | 保留的根因假设条数，默认 5 |
| `--title` | 看板标题 |
| `--out` | 输出 `data.json` 路径（目录会自动创建） |

排除日志中的已知噪声（健康检查、采样日志）建议**在采集侧或预处理阶段完成**，不要在分析脚本里塞过滤规则——否则口径无法复现。

---

## 九、data.json 字段规范

```jsonc
{
  "meta": {
    "title": "订单服务 2026-09-28 故障分析",
    "subtitle": "时间窗聚合 · 异常模式识别 · 根因假设排序 · 故障时间线",
    "scope": "3 个文件 / 128940 行",
    "window": "1m",                  // 窗口粒度标签
    "bucket_size": 60,                // 秒
    "generated_at": "2026-09-28 16:20",
    "files": 3, "lines": 128940, "parsed": 128201,
    "parse_rate": 0.9942,
    "formats": { "text": 2, "jsonl": 1 },
    "buckets": 96,
    "span_label": "2026-09-28 10:00:00 → 2026-09-28 11:36:00",
    "top_services": [{ "name": "order-service", "count": 506 }]
  },
  "summary": ["要点一（含解析量、影响时长、Top1 假设）", "…"],
  "impact": {
    "onset": "2026-09-28 10:40:00", "last": "2026-09-28 10:56:00",
    "impact_min": 16.0, "windows": 16, "peak_rate": 100.0,
    "mttd_min": 7.0, "mttr_min": 16.0     // 无告警时刻时 mttd_min 不出现
  },
  "buckets": [
    {
      "t": 1759027200.0, "t_label": "2026-09-28 10:40:00",
      "total": 88, "INFO": 79, "WARN": 9, "ERROR": 0, "FATAL": 0,
      "TRACE": 0, "DEBUG": 0,
      "errors": 0, "error_rate": 0.0,
      "p50": 96.0, "p95": 1180.0, "p99": 3020.0,   // 无耗时字段时为 null
      "top_sig": "slow query detected cost=<n>ms sql=<str>",
      "top_sig_share": 0.10, "top_svc": "order-service", "top_svc_share": 1.0,
      "z_total": 1.2, "z_errors": -0.4, "z_rate": -0.3, "z_p95": 4.8
    }
  ],
  "patterns": [
    {
      "id": "error_burst-43", "type": "error_burst", "name": "错误率飙升",
      "severity": "高", "sev": 3, "unit": "条/窗",
      "window_start": 1759027380.0, "window_end": 1759027440.0,
      "t_label": "2026-09-28 10:43:00", "peak_label": "2026-09-28 10:51:00",
      "peak_value": 93, "baseline": 2, "ratio": 46.5, "z": 21.4,
      "duration_min": 13.0, "count": 9,
      "service": "order-service",
      "top_sig": "java.net.SocketTimeoutException: Read timed out calling pay-service rt=<n>ms",
      "rate": 0.62,
      "why": "错误条数为基线 46.5 倍，窗口错误率 62.0%（基线 2.3%）。",
      "samples": ["2026-09-28 10:43:04 [ERROR] [order-service] …"]
    }
  ],
  "hypotheses": [
    {
      "id": "dep-downstream", "name": "下游依赖故障（超时 / 拒绝连接 / 5xx）",
      "cat": "依赖故障", "rank": 1, "score": 89, "confidence": "A",
      "coverage": 0.8, "evidence": 1.0, "temporal": 1.0, "hits": 134,
      "first_hit": "2026-09-28 10:43:00",
      "explains": ["级联传播", "错误率飙升", "致命错误突现", "重试风暴"],
      "evidence_items": [{ "type": "关键词", "strength": "强", "text": "…" }],
      "verify": ["查下游服务同一时段的错误率与 P99 是否同步抬升", "…"],
      "falsify": "下游指标全程平稳、且调用链无任何超时/5xx 记录时可排除",
      "fix": ["为下游调用设置更短的超时与熔断阈值", "…"]
    }
  ],
  "timeline": [
    { "t": 1759027200.0, "t_label": "2026-09-28 10:40:00", "phase": "观察起点",
      "kind": "start", "severity": "低", "title": "分析窗口起点",
      "detail": "共 75 个时间窗，粒度 1m。", "source": "聚合" }
  ],
  "changes": [{ "t_label": "2026-09-28 10:38:00", "text": "order-service 发布 v1.4.2" }],
  "flags": ["根因结论为「假设排序」而非定论：须用指标、调用链与变更记录交叉验证后确认。"],
  "sources": [{ "name": "Google SRE · 事故复盘文化（Postmortem Culture）",
                "kind": "方法论", "url": "https://sre.google/sre-book/postmortem-culture/" }]
}
```

**枚举约束（`build_board.py` 强校验）**

- `patterns[].severity` ∈ `高 | 中 | 低`；`sev` ∈ `1 | 2 | 3`
- `hypotheses[].confidence` ∈ `A | B | C`；`score` 为 0–100 整数；`rank` 为 1..N 连续
- `patterns` 为空时，`flags` 必须含「未检出」字样，否则构建报错
- `buckets` 至少 1 个，少于 6 个会告警（基线可靠性有限）
- `sources[].url` 必须为 http(s)

---

## 十、交付前自检清单

- [ ] `analyze_logs.py` 退出码 0，**解析率 ≥ 90%**（不足已在 `flags` 说明）
- [ ] `buckets` 数量 ≥ 12（否则在 `flags` 声明基线不可靠）
- [ ] 所有 `patterns` 的严重度枚举合法、`unit` 齐全
- [ ] 高严重度模式逐条能在时间线上找到对应事件
- [ ] 所有 `hypotheses` 均含 `verify` 与 `falsify` 非空
- [ ] 假设分数按降序排列、`rank` 为 1..N
- [ ] 未提供告警时刻时**没有**输出 `mttd_min`
- [ ] `flags` 已写明：解析局限、周期误报、缺耗时字段、窗口数偏少等
- [ ] 未输出任何敏感字段清单（手机号/身份证/令牌）
- [ ] `render_check.js` 退出码为 0
- [ ] 看板与回复中均已声明「结论为假设排序，不是定论」

---

## 十一、参考文件

- `references/log-parsing.md` —— 格式识别、时间戳解析、字段映射、时间窗口选择、聚合指标定义、解析失败降级
- `references/anomaly-patterns.md` —— 十四类异常模式判据、稳健统计底座、误报控制清单、模式到假设的映射
- `references/rca-ranking.md` —— 证据分级、时序硬约束、打分公式、十四条候选因库、假设书写规范、组合因取舍
- `references/timeline-postmortem.md` —— 七阶段划分、事件记录规范、MTTD/MTTR 口径、无责复盘清单、交付模板
- `scripts/analyze_logs.py` —— 日志解析 → 时间窗聚合 → 异常检测 → 模式识别 → 假设排序 → data.json
- `scripts/build_board.py` —— data.json 校验与派生 → report.html
- `scripts/render_check.js` —— 看板自检：静态检查 + 最小 DOM 桩真实渲染 + 交互路径触发
- `scripts/selfcheck.py` —— 技能包自检，支持 `--e2e <run_dir>` 与 `--log <日志路径>` 完整链路
- `scripts/gen_icon.py` —— 生成 512×512 技能图标（Pillow，字体路径取环境变量）
- `assets/board_template.html` —— 看板模板（浅色信息密度型，含内联 SVG 聚合图）

---

## 十二、打包交付（本机实测）

```bash
PY="%USERPROFILE%/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
SC="%USERPROFILE%/…/workbuddy-builtin/skills/skill-creator"

"$PY" -X utf8 "$SC/scripts/package_skill.py" \
    "<技能目录>/log-rca-timeline" \
    "<输出目录>"
```

三条实测约束：

1. **必须加 `-X utf8`**：校验脚本用裸 `read_text()` 读 SKILL.md，中文 Windows 下按 GBK 解码会抛 `UnicodeDecodeError`，打包直接失败。
2. **必须显式传第二个参数（输出目录）**：不传时 zip 落在程序安装目录下，用户极难找到。
3. **包内不得有盘符字面量路径与临时文件**：`selfcheck.py` 会把「包内无盘符绝对路径残留」作为一项独立检查。

打包后必须做**解包回填验证**（压缩与编码问题只有解压后才暴露）：

```bash
"$PY" -X utf8 -c "import zipfile; zipfile.ZipFile('<输出目录>/log-rca-timeline.zip').extractall('.tmp_verify')"
"$PY" -X utf8 ".tmp_verify/log-rca-timeline/scripts/selfcheck.py" --log "<任一示例日志>"
```

技能包自检与看板自检双双通过，该 zip 才可对外分享。

---

## 十三、重要限制

- **日志不是全知视角**：没有指标、链路与变更记录时，本技能只能给出「日志能支撑的假设排序」，无法排除「日志里根本没记录的原因」（如静默的配置变更、上游未记录的重试、网络设备丢包）。
- **多行堆栈按行处理**：堆栈行通常无时间戳而被丢弃，建议在采集侧开启 multiline 合并，否则错误量被系统性低估。
- **耗时字段缺失时**延迟类模式不可判定，`flags` 会明确提示，此时不要凭错误名猜测性能问题。
- **敏感数据**：日志常含个人信息与凭据，交付前须确认脱敏与保密边界；本技能不会把敏感字段整理成清单。
- 本技能不执行任何处置动作（重启、回滚、改配置），处置需由人或变更流程完成。
