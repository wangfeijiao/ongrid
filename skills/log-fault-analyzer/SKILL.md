---
name: log-fault-analyzer
description: 报错翻译官：把任何报错/红字/异常翻译成大白话，告诉你什么意思、怎么修。粘贴代码报错、命令行报错、npm/pip/git/docker 报错、Python traceback、Java 异常、接口报错、线上日志、浏览器控制台错误，自动输出：这个报错是什么意思（人话）、最可能的原因、一步步排查清单、修复命令、还缺什么信息。内置 60+ 错误模式库，覆盖 Java/Python/Go/数据库/Redis/MQ/K8s/npm/pip/git/docker/浏览器报错。适用于所有开发者（后端/前端/测试/学生/运维），日常写代码报错、命令行报错、部署报错、线上故障都能问。触发场景：用户粘贴任何报错文本/红字/堆栈/异常，或说「这段报错什么意思」「为什么报错了」「帮我看看这个错误」「Exception 什么原因」「npm ERR」「ModuleNotFoundError」「Permission denied」「Connection refused」「Traceback」「NullPointerException」「Error」「失败了」「什么情况」「报错了」「出问题了」「调用报错翻译官」「帮我排错」「这个红字什么意思」等时使用。
version: 1.1.0
display_name: 故障日志诊断专家
display_name_en: Log Fault Diagnosis Expert
description_zh: 报错翻译官：把任何报错/红字/异常翻译成大白话，告诉你什么意思、怎么修。粘贴代码报错、命令行报错、npm/pip/git/docker 报错、Python traceback、Java 异常、接口报错、线上日志、浏览器控制台错误，自动输出人话解释+根因+排查步骤+修复命令。覆盖 Java/Python/Go/数据库/Redis/MQ/K8s/npm/pip/git/docker/浏览器报错，所有开发者都能用。
description_en: "A log fault diagnosis expert: paste application logs, exception stack traces, API errors, data warehouse task logs, database/Redis/MQ middleware errors, or alert text, and it automatically performs root cause analysis, outputting an exception summary, key log evidence, root cause candidates (grouped by main cause/trigger/effect with confidence labels), step-by-step troubleshooting checklists, fix suggestions, and risk/missing-info hints. Bundles an executable Python script log_analyzer.py for command-line log analysis. Suitable for backend development, API automation testing, data warehouse testing, online issue troubleshooting, and quick alert triage."
---

# 故障日志诊断专家

## 目录

- [快速开始（建议先看示例再使用）](#快速开始建议先看示例再使用)
- [角色定位](#角色定位)
- [核心实现](#核心实现)
- [参考资料](#参考资料)
- [用户输入与规范](#用户输入与规范)
- [强制执行流程，顺序不可跳过](#强制执行流程顺序不可跳过)
- [根因置信度判定标准](#根因置信度判定标准)
- [复杂场景处理](#复杂场景处理)
- [输出固定 Markdown 结构](#输出固定markdown结构)
- [能力边界](#能力边界)
- [常见问题 FAQ（遇到问题先查这里）](#常见问题-faq遇到问题先查这里)
- [最佳实践](#最佳实践)
- [用户常犯错误（反模式）](#用户常犯错误反模式)
- [硬性约束](#硬性约束)
- [调用方式](#调用方式)
- [使用场景](#使用场景)

本 Skill 内置 13 个完整真实示例，每个包含原始输入日志和分析输出报告，**强烈建议先浏览一遍示例再使用**，能显著提升分析效果：

| 示例 | 场景 | 输入文件 | 输出文件 | 学习要点 |
|------|------|----------|----------|----------|
| 示例 1 | Java NullPointerException | `examples/example1-java-npe/input.txt` | `examples/example1-java-npe/output.md` | 堆栈定位、null 根因、traceId 排查 |
| 示例 2 | Redis OOM + 消息消费失败 | `examples/example2-redis-oom/input.txt` | `examples/example2-redis-oom/output.md` | 复合故障、内存满、队列积压关联分析 |
| 示例 3 | PostgreSQL SQL 执行超时 | `examples/example3-db-timeout/input.txt` | `examples/example3-db-timeout/output.md` | 慢查询、statement timeout、索引排查 |
| 示例 4 | 信息不足的泛化报错 | `examples/example4-insufficient-info/input.txt` | `examples/example4-insufficient-info/output.md` | 证据不足标注、需要补充什么信息 |
| 示例 5 | 多异常嵌套（Caused by 链） | `examples/example5-multi-exception-nested/input.txt` | `examples/example5-multi-exception-nested/output.md` | 事务回滚→数据访问异常→唯一键冲突，逐层定位最深层根因 |
| 示例 6 | 复合故障（连接池耗尽+超时） | `examples/example6-composite-fault/input.txt` | `examples/example6-composite-fault/output.md` | HikariCP 连接池耗尽导致查询超时，连锁反应判断，连接池监控排查 |
| 示例 7 | 同日志多独立故障 | `examples/example7-multi-independent-faults/input.txt` | `examples/example7-multi-independent-faults/output.md` | Redis OOM + DB 超时 + Kafka 重平衡同时发生，故障关联分析，区分连锁反应与独立故障 |
| 示例 8 | JSON 结构化日志 | `examples/example8-json-structured-logs/input.txt` | `examples/example8-json-structured-logs/output.md` | zap/logrus 格式 JSON 日志解析，自动提取 level/msg/error/trace_id 字段，多故障识别 |
| 示例 9 | Go panic 堆栈 | `examples/example9-go-panic/input.txt` | `examples/example9-go-panic/output.md` | Go runtime panic（nil pointer dereference），goroutine 堆栈提取，recover 机制分析 |
| 示例 10 | Python traceback 多异常 | `examples/example10-python-traceback-multi/input.txt` | `examples/example10-python-traceback-multi/output.md` | Python 双层 traceback（连接拒绝→死锁），During handling 异常链，psycopg2 死锁检测 |
| 示例 11 | K8s CrashLoopBackOff + OOMKilled | `examples/example11-k8s-crashloop-oom/input.txt` | `examples/example11-k8s-crashloop-oom/output.md` | 云原生复合故障：DB连接超时→启动失败→Liveness探针失败→CrashLoopBackOff→OOMKilled→Istio 503，kubectl 排查命令 |
| 示例 12 | K8s 调度失败 + 资源不足 + HPA异常 | `examples/example12-k8s-scheduling-resource/input.txt` | `examples/example12-k8s-scheduling-resource/output.md` | 云原生集群级故障：Pod调度失败（Insufficient cpu/memory + Taint不匹配）、节点NotReady、HPA无法获取指标、Redis连接拒绝，kubectl describe node/资源配额排查 |
| 示例 13 | Spring Cloud Feign 调用链 + 中文告警 | `examples/example13-springcloud-feign-alert/input.txt` | `examples/example13-springcloud-feign-alert/output.md` | 微服务调用链故障：Feign超时→Hystrix熔断→SQL慢查询→Sentinel限流→Gateway 503→服务不可用，含中文告警文本（【告警】【严重】【紧急】），链路追踪分析 |

## 角色定位

你是「报错翻译官」——任何报错的第一反应助手。不管是代码红字、命令行报错、npm/pip 装包失败、Git 冲突、Docker 起不来、Python traceback、Java 异常、线上日志、浏览器控制台错误，用户粘贴过来，你先用人话说清楚"这个报错是什么意思"，再给最可能的原因和一步步修复方法。

**语气要求**：
- 先讲人话，不要一上来就堆术语。比如用户看到 `ModuleNotFoundError: No module named 'requests'`，先告诉"你没装 requests 这个库"，再给修复命令 `pip install requests`。
- 不管报错多复杂，先给一句"人话翻译"，再展开分析。
- 面向所有开发者（后端/前端/测试/学生/运维），不假设用户是资深工程师。

> 重要：**不能编造报错信息，所有推断必须引用原文片段作为证据；信息不足时明确标注【证据不足】，并告诉用户应该补充什么信息，不做确定性猜测**。

## 核心实现

本 Skill 包含可执行的日志分析脚本：

- **`scripts/log_analyzer.py`**：核心分析脚本，支持日志预处理、62 种错误模式自动匹配、多异常嵌套识别、故障关联分析、JSON 结构化日志解析、Go/Python/Java 多语言堆栈提取、结构化报告生成。可通过命令行直接分析日志文件。

### 支持的日志格式

| 格式类型 | 说明 | 示例 |
|----------|------|------|
| 标准文本日志 | 时间戳 + 级别 + 消息，Java/Go/Python 通用 | `2026-08-30 14:23:11.234 ERROR [svc] msg` |
| JSON 结构化日志 | zap/logrus/pino 等 JSON 格式，自动提取 level/msg/error/trace_id | `{"time":"...","level":"error","msg":"...","error":"..."}` |
| Java 异常堆栈 | Exception + Caused by 链 + at 堆栈帧 | `java.lang.NullPointerException: ... at com.example...` |
| Python traceback | Traceback + File 行 + During handling 异常链 | `Traceback (most recent call last): File "x.py", line N` |
| Go panic | panic: + goroutine + 文件:行号 堆栈 | `panic: runtime error: nil pointer dereference goroutine 34 [running]:` |
| 数仓/大数据日志 | Spark/Flink/Hive 任务失败日志 | `FetchFailedException`, `Container killed by YARN` |
| 中间件报错 | Redis/Kafka/MySQL/Nginx 等中间件错误 | `OOM command not allowed`, `RebalanceInProgressException` |

### 脚本使用方式

```bash
# 分析日志文件，报告输出到终端
python scripts/log_analyzer.py app.log

# 分析日志文件，报告保存到文件
python scripts/log_analyzer.py app.log -o report.md

# 通过管道传入日志
cat app.log | python scripts/log_analyzer.py

# 显示详细分析过程
python scripts/log_analyzer.py app.log -v
```

脚本输出与 AI 分析遵循相同的报告结构和置信度标准。对于复杂或脚本未覆盖的场景，由 AI 基于日志内容进行深度分析。

## 参考资料

- **`references/error-patterns.md`**：常见错误模式速查表，按 Java/Python/Go/数据库/Redis/MQ/HTTP/数仓/OS/K8s/微服务/网络/安全/云服务/中文告警 15 类整理，含典型报错、最可能根因、第一步排查命令。
- **`references/examples.md`**：4 个标准输入输出示例（Java NPE、Redis OOM 复合故障、信息不足边界场景、Spark ETL），帮助快速理解调用方式和预期输出结构。
- **`examples/`**：13 个完整示例（输入 + 输出），覆盖 Java/Python/Go/K8s/微服务/JSON 日志等场景，见上方「快速开始」表格。

## 用户输入与规范

### 推荐提供的上下文

- 服务名称 / 应用名
- 环境（生产/预发/测试）
- 故障现象（接口报错？任务失败？服务不可用？）
- 触发操作（做了什么之后出现的）
- traceId / 请求ID / 任务ID
- 发生时间

### 输入格式要求

1. 优先粘贴**纯文本日志**，不要只发截图（截图 OCR 可能丢失堆栈细节）。
2. 堆栈信息尽量**完整**，至少包含异常类型、异常消息、最顶层 5-10 行堆栈。
3. 一次只分析**一个故障**；多个不相关报错请分开调用。
4. 日志中如含密码、token、密钥等敏感信息，请先脱敏再粘贴。

### 日志长度说明

- 单条日志建议控制在 **3000 字以内**，超过时优先保留 ERROR 行、完整堆栈、报错前后各 5-10 行上下文。
- 超长日志会自动过滤 INFO/WARN 噪音，只摘录关键报错片段分析。
- 关键信息被截断时，会在「缺失信息」部分明确指出需要补充哪一段。

### 异常输入处理

遇到以下情况不强行输出分析报告，直接说明原因并给出正确输入方式：

1. **空输入或纯无关文本**：说明需要粘贴日志/堆栈/告警文本。
2. **纯截图无法识别**：提示复制纯文本日志。
3. **日志不含任何 ERROR/Exception/失败关键词**：说明未检测到错误信息。
4. **多个不相关故障混在一起**：提示一次只分析一个故障。
5. **日志编码乱码**：提示检查编码（建议 UTF-8）后重新粘贴。

## 强制执行流程，顺序不可跳过

1. 【预处理】过滤日志噪音，提取关键错误：ERROR、Exception、堆栈、报错码、超时、拒绝连接、SQL异常、任务失败、OOM、死锁等。同一异常重复出现时只保留首次完整堆栈 + 出现次数。如遇异常输入，直接终止并提示用户。
2. 【异常摘要】一句话概括故障现象和业务影响。
3. 【关键证据摘录】摘抄日志中支撑分析的原始片段。
4. 【根因候选列表】按置信度从高到低排序，每条写明推测原因 + 对应日志证据。互斥根因分别列出并标注区分验证点。
5. 【分步排查操作清单】区分立即执行排查和深度排查，给出可复制的命令/检查点。
6. 【修复方案】按「立即止血 / 短期修复 / 长期优化」分层给出方案。standard/brief 模式每方案给出「立即止血 + 短期/长期要点一行」，仅 --detail full 输出完整三层（含风险评估与回滚方案），控制复杂案例报告长度。
7. 【风险&缺失信息提示】明确标出日志中没有的信息，具体说明需要补充什么。
8. 【延伸分析】（信息充分时）给出预防建议、监控告警建议、同类故障模式提醒。

## 根因置信度判定标准

| 等级 | 判定条件 | 标注方式 |
|------|----------|----------|
| 🔴 高置信 | 日志中存在直接指向该原因的明确错误信息（如 `Connection refused`+端口号=服务未运行），且有完整堆栈或多条日志交叉验证 | 直接给出结论 |
| 🟡 中置信 | 日志中有间接线索但需进一步确认，或证据有限（无堆栈、仅单条日志） | 给出推测 + 需确认的点 |
| ⚪ 低置信 | 日志中无任何直接证据，仅为基于经验的可能性列举 | 必须标注【证据不足】 |

> **低证据保守原则**：当日志无堆栈信息、仅单条错误、或缺少上下文时，根因置信度应自动下调一级（高→中，中→低），并在报告中明确标注"基于有限证据，置信度已保守下调"。禁止在证据不足时给出高置信结论。

> **同证据多模式降级（备选标注）**：当同一条日志证据同时命中多个模式时，脚本只保留最特异的一个作为主推测，其余更泛化的兜底模式（如"服务连接被拒绝"命中任意 `Connection refused` 文本，是 Nginx 502/探针失败/数据库连接拒绝等的泛化分支；"限流/熔断触发""中文告警文本"同理）自动降级为**备选**，在根因候选行尾标注"（另有备选：「XX」为更泛化推测，已折叠）"。备选不参与故障关联分析、不计入摘要模式数，避免对同一条证据平铺多条推测。判断优先级：泛化模式降级 → 模式自身置信度（high > medium > low）→ 命中文本长度。

> **故障链分组（主因 / 诱因 / 结果）**：根因候选章节会识别**同一时间链**上的多个模式并合并呈现，避免平铺感。同链判定：traceId 相同（跨服务调用链）、实例标识相同（同一 K8s Pod，如 `order-service-7d8f9c6b5d-x2k4p`）、或同类故障间隔 ≤ 10 分钟。链内角色：**主因**=时间最早的非表象模式（表象类如 CrashLoopBackOff/OOMKilled/熔断降级/告警文本等不作主因）；**结果**=时间最晚的表象模式；**诱因**=中间环节。无同链关联的单个故障标注"（独立故障）"。JSON 输出中每项新增 `chain`（链编号）与 `role`（main/trigger/effect/independent）字段。

## 复杂场景处理

### 多异常嵌套（Caused by 链）

Java/Spring 应用中常见异常链：外层是事务回滚异常，中间是数据访问异常，最深层才是真正的根因（如唯一键冲突、SQL 语法错误）。

**处理规则：**
1. 完整提取整个 Caused by 链，不截断
2. 从最深层 Caused by 开始匹配错误模式，最深层通常是真正根因
3. 根因候选中优先展示最深层异常对应的模式
4. 摘要中说明异常链层次（如"事务回滚→数据访问异常→唯一键冲突"）
5. 排查步骤优先针对最深层根因，外层异常作为上下文参考

**参考示例**：示例 5（`examples/example5-multi-exception-nested/`）

### 复合故障（连锁反应）

一个基础故障引发连锁反应，如连接池耗尽→查询超时→接口 500。日志中会出现多个错误，但它们有因果关系。

**处理规则：**
1. 按时间顺序排列所有错误，最早发生的通常是根因
2. 分析后一个错误的报错信息是否包含前一个故障的特征
3. 摘要中明确指出"可能存在连锁反应"，并列出故障链
4. 排查步骤优先针对最早发生的故障
5. 修复建议区分：根因修复（解决最早故障）+ 连锁防护（增加超时/降级避免级联失败）

**参考示例**：示例 6（`examples/example6-composite-fault/`）

### 同日志多独立故障

日志中包含多个互不关联的故障，如 Redis OOM + Kafka 重平衡 + DB 超时同时发生。它们可能是同一时间窗口内的不同问题，也可能有共同诱因（如服务器资源耗尽）。

**处理规则：**
1. 所有独立故障都必须匹配对应的错误模式，不遗漏
2. 输出"故障关联分析"章节，帮助用户判断是连锁反应还是独立故障
3. 给出连锁反应和独立故障的判断依据（时间顺序、traceId、故障类型相关性）
4. 排查步骤按故障分别列出，或合并同类项
5. 建议用户优先排查时间最早的故障，确认是否为共同根因

**参考示例**：示例 7（`examples/example7-multi-independent-faults/`）

## 输出固定Markdown结构

```
# 故障分析报告
## 1.异常摘要
## 2.关键日志证据摘录
## 3.根因候选（按主因/诱因/结果分组）
## 3.5 故障关联分析（多个故障时输出）
## 4.排查操作清单
  ### 🔹立即排查（优先做）
  ### 🔹深度排查（上面无结果再执行）
## 5.修复建议
  ### 立即止血
  ### 短期修复
  ### 长期优化
## 6.缺失信息/待补充
## 7.延伸分析（可选）
```

### 输出详略控制

- **简单点/简要分析**：只输出异常摘要 + 根因候选 + 修复建议。
- **详细点/深入分析**：排查清单每步增加命令示例和预期结果，修复建议增加风险评估，延伸分析必须输出。
- **未指定**：按标准结构输出。

> **长报告控制**：standard 模式下，缺失信息章节最多列出 8 条（超出部分提示用 `--detail full` 查看完整列表）；3.5 故障关联分析的判断依据以紧凑单行呈现。复杂案例（多故障链）如需更精简输出，可配合 `--detail brief`（仅摘要、Top3 根因、修复建议）或 `--sections summary,root_causes,fixes`。

> **brief 泛化过滤**：`--detail brief` 的 Top3 根因默认过滤泛化模式（服务连接被拒绝、限流/熔断触发、中文告警文本），只保留精准推测；若全部候选均为泛化模式则回退保留，避免空报告。JSON 输出（`--format json --detail brief`）同步过滤。摘要仍如实报告检测到的全部错误模式数。

### 输出格式与详细程度（脚本参数）

脚本支持通过命令行参数灵活控制输出，避免"一次分析不满意就需要重新粘贴日志"：

| 参数 | 可选值 | 说明 |
|------|--------|------|
| `--detail` | `brief` / `standard` / `full` | 输出详细程度。brief 仅 3 个核心章节（约 20~40 行，视故障数量而定），standard 完整标准报告，full 含延伸分析和监控建议 |
| `--format` | `markdown` / `json` | 输出格式。markdown 适合人阅读，json 适合程序解析和后续自动化处理 |
| `--sections` | 逗号分隔的章节名 | 自定义输出章节，如 `summary,root_causes,fixes` 只输出这三章 |
| `--deep` | 开关 | 深度分析模式：排查步骤增至 8 条、修复建议含风险评估和回滚方案、增加监控配置和复盘建议 |

**常用组合：**
- 快速定位：`--detail brief`（约 20~40 行，只看根因和修复）
- 标准报告：默认参数（约 50~120 行，视故障数量而定，完整分析）
- 深度复盘：`--detail full --deep`（100+ 行，含风险评估、回滚方案、监控配置、复盘建议）
- 程序对接：`--format json`（结构化 JSON，可直接入库或发送到告警系统）

### 超长日志处理

当日志超过 500 行时，脚本会自动进行智能截取，避免处理超时或输出不完整：

**截取策略：**
1. 保留所有 ERROR/FATAL/CRITICAL 行及其前后 5 行上下文
2. 保留所有异常堆栈帧（at / Caused by / Traceback / File / panic / goroutine）
3. 保留所有 WARN 行
4. 过滤纯 INFO/DEBUG 行（无错误关键词的）
5. 如截取后仍超过阈值，进一步压缩为错误行前后 2 行上下文

**处理说明：**
- 报告顶部会输出明确的"日志处理说明"，告知原始行数、截取后行数、保留了哪些内容
- 报告底部会输出"日志处理：原始 N 行 → 智能截取后 M 行"的统计信息
- 如截取后仍有遗漏，可用 `--max-lines` 参数调大阈值，或分段提供日志
- 短日志（≤500 行）不受影响，完整处理

**相关参数：**
- `--max-lines N`：设置最大处理行数（默认 500），设为 0 表示不限制
- `-v`：verbose 模式下显示处理进度（读取行数、提取错误数、匹配模式数）

### 自定义错误模式（支持新框架）

错误模式库持续更新，但如果遇到未覆盖的新框架/新异常类型，可通过自定义模式文件扩展：

**使用方式：**
```bash
python log_analyzer.py app.log --patterns my_custom_patterns.json
```

**自定义模式 JSON 格式：**
```json
[
  {
    "pattern": "MyCustomException|custom error code",
    "name": "自定义异常名称",
    "root_cause": "根因描述",
    "confidence": "high",
    "category": "custom",
    "troubleshoot": ["排查步骤1", "排查步骤2"],
    "deep_troubleshoot": ["深度排查1"],
    "fix_immediate": "立即修复",
    "fix_short": "短期修复",
    "fix_long": "长期优化",
    "missing_info": ["缺失信息1"]
  }
]
```

**模式库版本：**
- 当前版本：v2.6.0（更新于 2026-08-30）
- 内置模式：62 种，覆盖 Java/Python/Go/数据库/中间件/网络/安全/容器/K8s/云服务/Spring Cloud微服务/中文告警/数仓等场景
- 术语解释：57 个专业术语的通俗解释
- 查看版本：`python log_analyzer.py --version`
- 新模式需求：可在使用反馈中提交新异常类型和示例日志，后续版本会纳入内置模式库

### 超时保护与处理进度

为避免长日志处理时用户以为程序卡住，内置超时保护和处理进度输出：

**超时保护（--timeout）：**
- 默认超时时间 30 秒，可通过 `--timeout N` 自定义
- 处理耗时超过阈值时，报告顶部会输出明确的超时警告：
  > ⚠️ 处理耗时 35.2s 超过阈值 30s，建议使用 --quick 快速模式或 --max-lines 限制行数以提升处理速度。
- 超时不会中断处理，只是提示用户优化输入方式

**处理进度输出（-v / --verbose）：**
- 读取日志完成：输出字符数、行数
- 分析完成：输出提取的错误数、匹配的根因数、耗时（秒）
- 输出配置：输出 detail/format/deep 等配置信息
- 超时警告：输出超时提示

```bash
# 带进度输出分析
python log_analyzer.py app.log -v

# 自定义超时时间（60秒）
python log_analyzer.py huge.log --timeout 60

# 快速模式 + 进度输出（适合超大日志）
python log_analyzer.py huge.log --quick -v
```

### 术语解释模式（--explain）

针对非技术用户理解专业术语的困难，新增 `--explain` 术语解释模式：

**通俗摘要（报告开头）：**
- 用一句话非技术语言说明发生了什么、最可能的原因、建议先做什么
- 示例：`📝 一句话总结：很可能是「Feign 远程调用失败」导致的——微服务之间的调用出了问题，同时发现 7 个相关问题。建议先做：临时调大 Feign 超时时间，或重启目标服务恢复可用性。`

**术语解释章节（报告末尾）：**
- 自动扫描报告中出现的专业术语，匹配内置 57 个术语的通俗解释
- 每个术语用生活化比喻解释（如"连接池像餐厅提前备好的餐具"、"熔断像电路过载时自动跳闸"）
- 最多解释 15 个术语，避免报告过长
- 覆盖：Java/Python/Go 异常、数据库、中间件、K8s、微服务、网络、监控等领域术语

**使用方式：**
```bash
# 带术语解释的完整报告（适合非技术用户/管理层阅读）
python log_analyzer.py app.log --explain

# 精简报告 + 术语解释（适合快速了解）
python log_analyzer.py app.log --detail brief --explain
```

### 输出长度控制

复杂场景（多故障/长日志）输出可能较长，提供多种控制方式：

| 方式 | 效果 | 适用场景 |
|------|------|---------|
| `--detail brief` | 仅保留摘要、Top3 根因、修复建议，输出减少约 63%（108行→40行） | 快速定位、非技术用户 |
| `--detail standard` | 默认，完整根因列表、关键证据、排查步骤，修复建议 Top3 | 标准分析 |
| `--detail full` | 完整根因+证据+排查，修复建议含三层（立即/短期/长期）与风险评估，延伸分析 | 深度复盘 |
| `--sections` | 只输出指定章节（如 `summary,root_causes,fixes`） | 按需获取 |
| `--quick` | 快速模式，不展开完整堆栈 | 超大日志 |
| 智能提示 | 根因超过 5 个时自动提示使用精简模式 | 复杂场景 |

**复杂场景智能提示：**
当匹配到超过 5 个错误模式时，报告中会自动输出精简提示：
> 💡 输出精简提示：本次匹配到 6 个错误模式，报告较长。如需更简洁的输出，可使用 `--detail brief` 参数（仅保留摘要、Top3 根因和修复建议），或使用 `--sections summary,root_causes,fixes` 只输出关键章节。

### 异常处理与友好提示

针对输入格式异常或操作失误，所有错误提示均采用通俗语言 + 解决建议的形式，非技术用户也能看懂：

| 异常场景 | 友好提示示例 |
|---------|------------|
| 文件不存在 | 📁 找不到这个文件：path。💡 建议：请检查文件路径是否正确，文件名是否拼写正确，文件是否还在原来的位置。 |
| 空文件/空输入 | 📭 日志内容是空的，没有可以分析的内容。💡 建议：请粘贴包含 ERROR/Exception/报错 的日志片段，至少需要有一条错误信息才能分析。 |
| 无错误日志 | 📝 这段日志中没有找到明显的错误信息（ERROR/Exception/报错），看起来都是正常运行记录。如果确实有问题，请提供包含报错信息的日志片段，或检查是否选错了时间段/日志文件。 |
| 二进制文件 | 🖥️ 这个文件看起来不是文本日志（可能是二进制文件）。💡 建议：请提供文本格式的日志文件（.log/.txt），不支持压缩包、图片、可执行文件等二进制格式。 |
| 无权限读取 | 📄 读取文件时出了点问题：没有权限。💡 建议：请检查文件权限，或将文件复制到其他位置后重试。 |
| 未传入日志 | 💡 还没有收到日志内容。💡 建议：请在命令后面加上日志文件路径，例如：python log_analyzer.py app.log |
| 意外错误 | ⚠️ 处理时遇到了意外问题。💡 建议：请尝试使用 --quick 快速模式，或减少日志行数后重试。 |

**输入验证机制：**
- 空内容检测：自动识别空文件和空输入
- 二进制检测：自动识别非文本文件（可打印字符比例 < 70%）
- 内容有效性检测：自动识别全是 INFO/DEBUG 的正常日志，给出无错误提示
- 编码容错：使用 UTF-8 编码读取，遇到无法解码的字符自动替换而非崩溃
- 全局异常兜底：任何未预期的异常都给出友好提示，不会直接崩溃或输出技术堆栈（除非使用 -v 详细模式）

**用户取消操作：**
- 按 Ctrl+C 取消时，输出 "⏹️ 已取消操作。" 而非错误堆栈

### Spring Cloud 微服务场景支持

针对 Spring Cloud 微服务架构的调用链故障，内置 6 种专属故障模式：

| 微服务模式 | 触发关键词 | 针对性排查要点 |
|-----------|-----------|---------------|
| **Feign 远程调用失败** | `FeignException`、`RetryableException`、`Read timed out feign` | 目标服务名/接口路径提取、服务注册中心实例检查、Feign 超时配置（ribbon.ReadTimeout/ConnectTimeout）、链路追踪耗时分析 |
| **Hystrix 熔断降级失败** | `HystrixRuntimeException`、`circuit open`、`fallback failed` | Hystrix command 名称、熔断器状态（/actuator/hystrix.stream）、下游服务错误率、线程池隔离配置、fallback 方法签名检查 |
| **Sentinel 限流降级** | `BlockException`、`FlowException`、`DegradeException` | BlockException 类型区分（流控/降级/热点/系统）、Sentinel 控制台规则配置、QPS/响应时间/异常率阈值检查、热点参数限流排查 |
| **负载均衡无可用实例** | `No instances available`、`LoadBalancerException`、`NoServerAvailable` | 服务注册中心实例列表检查、实例健康状态（UP/DOWN）、区域亲和性过滤、元数据过滤、Eureka 保护模式排查 |
| **服务注册发现失败** | `DiscoveryClient not available`、`Eureka unavailable`、`Nacos connect failed` | 注册中心连通性测试、地址配置检查、认证/命名空间配置、注册中心集群状态、Spring Cloud 版本兼容性 |
| **Gateway 路由失败** | `503 gateway`、`NoRouteException`、`Unable to find instance` | 路由配置检查（Predicate/Filter）、已加载路由列表（/actuator/gateway/routes）、lb:// 服务名拼写、限流过滤器触发、自定义 GlobalFilter 异常 |

**微服务调用链典型故障链路：**
> 下游服务慢查询 → Feign 调用超时 → Hystrix 熔断 → fallback 失败 → 上游服务 500 → Gateway 503 → 全链路告警

参考示例 13（`examples/example13-springcloud-feign-alert/`），完整展示了这条微服务调用链故障的分析过程。

### 中文告警文本支持

针对国内运维平台的中文告警文本（如 Prometheus Alertmanager、Zabbix、云监控、自研告警系统），内置中文告警模式和关键词扩展：

**支持的中文告警类型：**
- 资源类：CPU使用率超过阈值、内存使用率告警、磁盘空间不足、网络延迟/丢包
- 服务类：服务不可用、宕机、接口响应超时、健康检查失败
- 中间件：数据库连接数满、消息堆积、线程池耗尽、连接池耗尽
- 系统类：【告警】【警告】【严重】【紧急】级别标识、告警触发/恢复状态

**中文关键词扩展（从 7 个扩充到 30+ 个）：**
不存在、失败、超时、拒绝、溢出、耗尽、崩溃、异常、告警、警告、不可用、宕机、熔断、降级、限流、阻塞、死锁、泄漏、满了、不足、超过阈值、达到上限、连接失败、调用失败、请求失败、服务异常、系统异常、业务异常、数据异常、配置错误、参数错误、权限不足

**中文告警分析输出：**
- 自动提取告警级别（紧急/严重/警告/提示）
- 自动识别告警类型（资源/服务/中间件/系统）
- 提取告警中的服务名、主机、指标、阈值
- 给出对应类型的排查命令和应急措施
- 区分首次触发和反复触发，给出不同建议

### 云原生场景支持

针对 Kubernetes/Docker/Service Mesh/云服务等云原生环境，内置 12 种专属故障模式，每种都包含具体的 kubectl/docker 排查命令，不再是通用建议：

| 云原生模式 | 触发关键词 | 针对性排查命令 |
|-----------|-----------|---------------|
| K8s Pod CrashLoopBackOff | `CrashLoopBackOff`、`back-off restarting` | `kubectl describe pod`、`kubectl logs --previous`、退出码分析（137=OOM/1=应用错误/126=不可执行/127=未找到） |
| K8s 健康检查探针失败 | `Liveness probe failed`、`Readiness probe failed` | `kubectl exec curl localhost:port/path`、探针配置检查、`initialDelaySeconds`/`timeoutSeconds` 调优 |
| K8s 镜像拉取失败 | `ImagePullBackOff`、`ErrImagePull`、`Failed to pull image` | `kubectl describe pod Events`、`docker pull` 测试、`imagePullSecret` 检查、镜像标签验证 |
| K8s 存储卷挂载失败 | `FailedMount`、`could not mount volume`、`PVC pending` | `kubectl get pvc`、`kubectl describe pvc`、`kubectl get pv`、StorageClass 检查 |
| 容器启动命令执行失败 | `exec format error`、`no such file or directory exec` | `docker inspect Cmd`、脚本换行符检查（CRLF→LF）、架构匹配检查（amd64/arm64）、shebang 验证 |
| Service Mesh / 网关 503 | `503 Service Unavailable`、`no healthy upstream`、`UH` | `kubectl get endpoints`、Pod Ready 状态检查、VirtualService/DestinationRule 配置、mTLS 兼容性 |
| 容器 OOMKilled | `OOMKilled`、`memory cgroup out of memory` | `kubectl describe pod`、`kubectl top pod --containers`、JVM Xmx 配置（≤limit 70%）、堆 dump 分析 |
| 云服务 API 限流/配额超限 | `Throttling`、`Rate exceeded`、`Quota exceeded` | 云控制台配额检查、调用频率统计、重试风暴排查、缓存策略优化 |
| K8s Pod 调度失败 | `FailedScheduling`、`Insufficient cpu/memory`、`node(s) had taint` | `kubectl describe pod` 看 FailedScheduling 事件、`kubectl describe node` 看资源容量/Taint、nodeSelector/affinity 检查、ResourceQuota 检查 |
| K8s ConfigMap/Secret 挂载失败 | `failed to sync configmap`、`configmap not found`、`volume configmap not found` | `kubectl get configmap/secret` 确认存在、命名空间检查、RBAC 权限检查、subPath 热更新问题、1MB 限制检查 |
| K8s HPA 自动扩缩容异常 | `failed to get cpu utilization`、`HPA unable to get metrics` | `kubectl get hpa` 看 TARGETS、`kubectl top pods` 验证 metrics-server、目标资源 request 配置检查、API 聚合层检查 |
| K8s 节点 NotReady | `NotReady`、`kubelet stopped`、`container runtime is down` | `kubectl describe node` 看 Conditions、`systemctl status kubelet/containerd`、`journalctl -u kubelet` 日志、节点资源/网络/时间同步检查 |
| K8s API Server 连接异常 | `context deadline exceeded`、`TLS handshake timeout`、`certificate has expired` | `kubectl get nodes --v=6`、API Server 状态检查、证书有效期检查（openssl/kubeadm certs check-expiration）、etcd 状态检查 |

**云原生复合故障典型链路：**
应用启动失败（DB连接超时）→ Liveness 探针失败 → 容器重启 → CrashLoopBackOff → 多次重启后 OOMKilled → Service endpoints 为空 → Istio/网关返回 503。

参考示例 11（`examples/example11-k8s-crashloop-oom/`），完整展示了这条故障链的分析过程。

### 快速模式（--quick）

对于超大日志（数千行以上），可使用 `--quick` 快速模式：
- 仅提取关键错误信息，不展开完整堆栈
- 处理速度更快，适合快速定位故障类型
- 报告顶部会标注"快速模式"提示
- 如需完整分析，去掉 `--quick` 参数重新运行

```bash
# 快速扫描超大日志
python log_analyzer.py huge_app.log --quick

# 完整分析（默认）
python log_analyzer.py huge_app.log
```

### 复杂场景多轮交互策略

复杂故障（多异常嵌套、复合故障、大日志）通常需要多轮分析，不要期望一次输出就完全满意。推荐按以下策略交互：

**第一轮：快速扫描定位**
- 粘贴完整日志，要求"先快速扫描，列出所有错误模式和可能根因"
- 目标：确认日志被正确解析，识别有几个故障、分别是什么类型
- 如果第一轮输出格式不符合预期，可以直接说"输出简洁点"或"只看根因和修复"

**第二轮：针对特定根因深入**
- 从第一轮的根因候选中选择 1-2 个最可能的，要求"针对第 X 个根因深入分析，给出详细排查步骤和验证命令"
- 可以要求"只分析连接池耗尽这个根因，其他的不用展开"
- 可以要求"给出可直接复制执行的排查命令，包括预期输出"

**第三轮：补充信息后重新分析**
- 如果第一轮标注了"证据不足"或"置信度已下调"，按缺失信息清单补充日志后重新分析
- 可以说"补充了堆栈信息，重新分析"或"这是完整的调用链日志，重新分析"
- 补充信息后置信度会相应提升，分析结果更准确

**追问示例：**
- "针对第一个根因（连接池耗尽），给出详细的排查步骤和每条命令的预期输出"
- "只保留根因候选和修复建议，其他章节去掉"
- "这个分析太简略了，增加风险评估和回滚方案"
- "我补充了完整堆栈，重新分析一下，重点看最深层的 Caused by"
- "用 JSON 格式输出，我要接入告警系统"

## 能力边界

### 支持的场景

- Java/Python/Go 异常堆栈分析
- HTTP 接口报错（500/502/503/504）、Nginx/Apache 网关日志
- 数据库报错（MySQL/PostgreSQL/Oracle/MongoDB）
- 中间件报错（Redis/RocketMQ/Kafka/Elasticsearch）
- 数仓/大数据任务失败（Spark/Hive/Flink/YARN）
- 操作系统/资源层面报错（OOM Killer/磁盘满/文件描述符耗尽）
- Kubernetes/云原生故障（CrashLoopBackOff/探针失败/镜像拉取失败/PVC挂载失败/OOMKilled/调度失败/节点NotReady/HPA异常）
- Service Mesh/微服务网关报错（Istio 503/无健康后端/mTLS 失败）
- Spring Cloud 微服务调用链（Feign超时/Hystrix熔断/Sentinel限流/Ribbon无实例/服务注册发现失败/Gateway路由失败）
- 中文运维告警文本（【告警】【严重】【紧急】/CPU内存磁盘告警/服务不可用/接口超时/消息堆积）
- 云服务 API 限流/配额超限（AWS/Azure/GCP/阿里云）
- JSON 结构化日志（zap/logrus/pino）
- 告警文本快速定位

### 不支持的场景

- 直接分析图片截图（请先转为纯文本）
- 分析二进制日志文件（请先导出为文本）
- 实时监控/持续跟踪日志流
- 自动执行排查命令（只给出命令建议，需用户手动执行）
- 直接修改用户代码（只给出修复方案和建议）

## 常见问题 FAQ（遇到问题先查这里）

### 使用类

**Q1：日志太长怎么办？**
A：优先保留 ERROR 行、完整堆栈、报错前后各 5-10 行，去掉重复的 INFO 心跳/健康检查。也可全量粘贴，会自动过滤噪音。

**Q2：只有一句"系统内部错误"没有堆栈能分析吗？**
A：能分析但结论有限，会标注【证据不足】并明确说明需要补充什么（完整堆栈、traceId、请求参数）。建议尽量提供完整堆栈。

**Q3：支持哪些日志格式？**
A：纯文本格式的应用日志、异常堆栈、告警文本。包括 Java（Logback/Log4j）、Python（logging/traceback）、Go（panic/stack）、Nginx/Apache、数据库错误日志、YARN/Spark 任务日志、RocketMQ/Kafka 消费日志。不支持直接分析图片截图。

**Q4：能同时分析多个服务的日志吗？**
A：一次只分析一个故障点。分布式调用链问题可分别粘贴各服务日志并提供同一 traceId，会关联分析。

**Q5：输出可以简化吗？**
A：可以。说「简单点」只输出摘要+根因+修复；说「详细点」增加命令示例和预期结果。默认按标准结构输出。

**Q6：有命令行工具可以直接用吗？**
A：有。`scripts/log_analyzer.py` 可直接分析日志文件，用法见上方「脚本使用方式」。

### 结果类

**Q7：分析出来的根因不对怎么办？**
A：根因候选是分组呈现的可能性列表（同一时间链按主因/诱因/结果组织，独立故障按置信度标注），不是确定性结论。请按「排查操作清单」逐步验证，用实际命令输出确认或排除每个候选。验证后发现都不对，把排查结果贴回来，会基于新证据重新分析。

**Q8：分析结果能直接作为结论吗？**
A：不能。必须通过排查清单中的命令实际验证后才能确认。高置信根因也需要验证，因为日志可能存在误导性信息。

**Q9：输出不完整/被截断了怎么办？**
A：可能是日志太长导致。请筛选关键报错片段后重新分析，或说「详细点」要求完整输出。

**Q10：分析时间太长怎么办？**
A：日志过长会增加处理时间。建议先筛选关键片段（ERROR+堆栈+上下文），减少噪音。

### 输入类

**Q11：日志是图片格式怎么办？**
A：请将图片中的日志文字复制出来粘贴为纯文本。截图 OCR 可能丢失关键信息，不建议直接使用。

**Q12：日志里有敏感信息怎么办？**
A：粘贴前自行脱敏（替换密码、token、密钥、手机号为 `***`），脱敏不影响故障分析。

**Q13：日志编码乱码怎么办？**
A：请检查日志文件编码（建议 UTF-8），用正确编码打开后复制文本重新粘贴。常见于 GBK 编码的日志在 UTF-8 环境下显示乱码。

**Q14：如何获得更准确的分析结果？**
A：①提供完整堆栈（首尾各 5-10 行）②附带服务名、环境、traceId ③保留报错前后 5-10 行上下文 ④一次只分析一个故障 ⑤先看示例了解预期输出格式。

**Q15：脚本运行报错怎么办？**
A：①确认 Python 版本 ≥ 3.7 ②确认日志文件路径正确 ③确认日志文件编码为 UTF-8 ④使用 `-v` 参数查看详细分析过程定位问题 ⑤如仍无法解决，可直接将日志粘贴给 AI 分析，不依赖脚本。

**Q16：为什么有些根因标了"置信度已下调"？**
A：当日志证据不足时（无堆栈信息、仅单条错误、缺少上下文），系统会自动将根因置信度下调一级（高→中，中→低），并标注"基于有限证据，置信度已保守下调"。这是为了避免在证据不足时给出过于自信的结论。补充完整堆栈和上下文后重新分析，置信度会相应提升。

**Q17：报告里的错误信息数量准确吗？**
A：错误计数经过误报过滤：①INFO/DEBUG/TRACE 级别行如果不含明确异常/堆栈，不计入 ②变量名中的 error（如 errorCode、errorMsg）不计入 ③同一异常重复出现只计一次。如果发现计数异常，请检查日志格式是否规范。

**Q18：日志中有多个错误，怎么判断是连锁反应还是独立故障？**
A：报告中会输出"3.5 故障关联分析"章节，给出判断依据：①连锁反应：时间上先后发生、后一个错误包含前一个故障的特征、同一 traceId 关联；②独立故障：不同 traceId、故障类型完全无关、时间上同时发生无因果。建议优先排查时间最早的故障，确认是否为根因。

**Q19：Caused by 链式堆栈怎么定位真正根因？**
A：异常链中外层异常通常是包装异常（如事务回滚、数据访问异常），最深层的 Caused by 才是真正根因（如唯一键冲突、SQL 语法错误、空指针）。分析时从最深层开始匹配错误模式，排查步骤优先针对最深层根因。参考示例 5。

**Q20：支持 JSON 结构化日志吗？**
A：支持。脚本会自动识别 JSON 格式日志行，提取 time/level/msg/error/trace_id/caller 等常见字段并转换为纯文本格式进行分析。如果 JSON 中包含 stack/stack_trace 字段，也会自动提取堆栈信息。参考示例 8。

**Q21：支持 Go panic 和 Python traceback 吗？**
A：支持。脚本可识别 Go panic 堆栈（panic: + goroutine + 文件:行号）和 Python traceback（Traceback + File 行 + During handling 异常链），并自动提取完整堆栈。Go panic 会匹配"Go panic (nil pointer/越界)"模式，Python 异常会匹配对应的数据库/网络错误模式。参考示例 9 和示例 10。

**Q22：输出格式太固定，能调整吗？**
A：可以。脚本支持 4 种输出控制参数：`--detail brief|standard|full` 控制详细程度（brief 约 20~40 行核心内容）、`--format markdown|json` 控制输出格式、`--sections` 自定义输出章节、`--deep` 开启深度分析模式（含风险评估和回滚方案）。也可以在对话中直接说"输出简洁点""只看根因和修复""增加风险评估"等，AI 会相应调整输出。

**Q23：复杂场景一次分析不满意怎么办？**
A：复杂故障（多异常嵌套、复合故障）建议多轮交互：第一轮先快速扫描列出所有错误模式，第二轮针对特定根因深入分析，第三轮补充信息后重新分析。可以直接说"针对第一个根因深入分析""只保留根因和修复""补充了堆栈重新分析"等。不要期望一次输出就完全满意，多轮追问才能获得最精准的结果。

**Q24：日志太长处理慢或输出不完整怎么办？**
A：脚本内置超长日志智能截取（默认 500 行阈值），超过时自动保留所有 ERROR/FATAL 行及其上下文、所有异常堆栈帧、所有 WARN 行，过滤纯 INFO/DEBUG 行。报告顶部会明确告知处理情况（原始行数→截取后行数）。如需完整分析，可用 `--max-lines 0` 取消限制，或分段提供日志。短日志（≤500 行）完整处理不受影响。

**Q25：遇到未覆盖的新框架/新异常类型怎么办？**
A：错误模式库持续更新（当前 v2.6.0，62 种内置模式）。如遇未覆盖的异常，可通过 `--patterns custom.json` 参数加载自定义模式文件（JSON 格式，包含 pattern/name/root_cause/troubleshoot/fixes 等字段），与内置模式合并使用。也可在使用反馈中提交新异常类型和示例日志，后续版本会纳入内置模式库。

**Q26：支持 Kubernetes/云原生环境的故障分析吗？**
A：支持。内置 12 种云原生专属故障模式：K8s Pod CrashLoopBackOff、健康检查探针失败、镜像拉取失败、存储卷挂载失败、容器启动命令执行失败、Service Mesh/Istio 503、容器 OOMKilled、云服务 API 限流、Pod 调度失败、ConfigMap/Secret 挂载失败、HPA 异常、节点 NotReady、API Server 连接异常。每种模式都包含具体的 kubectl/docker 排查命令（如 kubectl describe pod、kubectl logs --previous、kubectl get endpoints 等），不再是通用空泛建议。参考示例 11。

**Q27：日志特别大（数千行以上）处理慢怎么办？**
A：可使用 `--quick` 快速模式，仅提取关键错误信息不展开完整堆栈，处理速度更快；也可用 `--max-lines N` 控制处理行数（默认 500，超过自动智能截取保留所有错误和堆栈）。建议先用快速模式定位故障类型，再针对特定时间段截取完整日志做深度分析。

**Q28：处理日志时程序好像卡住了怎么办？**
A：程序不会真的卡住，只是长日志处理需要时间。可使用 `-v` 参数查看实时处理进度（读取字符数/行数、提取错误数、匹配根因数、耗时）；默认 30 秒超时保护，超过时报告顶部会输出明确的超时警告，建议使用 `--quick` 快速模式或 `--max-lines` 限制行数；也可用 `--timeout 60` 自定义超时时间。处理不会因超时而中断，只是提示优化输入方式。

**Q29：支持 Spring Cloud 微服务调用链和中文告警文本吗？**
A：支持。内置 6 种 Spring Cloud 微服务专属模式：Feign 远程调用失败、Hystrix 熔断降级失败、Sentinel 限流降级、负载均衡无可用实例、服务注册发现失败、Gateway 路由失败。每种模式都包含针对性的排查要点（如 Feign 超时配置、Hystrix 熔断器状态、Sentinel 规则配置、服务注册中心实例检查等）。中文告警文本方面，内置中文告警模式，支持【告警】【严重】【紧急】等级别标识，以及 CPU/内存/磁盘/服务不可用/接口超时/消息堆积等常见告警类型，中文关键词从 7 个扩充到 30+ 个。参考示例 13。

**Q30：报告输出太长看不懂怎么办？**
A：有多种方式控制输出长度和降低理解门槛：①使用 `--detail brief` 精简模式，仅保留摘要、Top3 根因和修复建议，输出减少约 63%（108行→40行）；②使用 `--sections summary,root_causes,fixes` 只输出关键章节；③使用 `--explain` 术语解释模式，报告开头增加一句话通俗摘要，末尾增加专业术语的生活化比喻解释（内置 57 个术语），适合非技术用户阅读；④复杂场景（超过5个根因）时报告会自动提示使用精简模式。推荐非技术用户使用 `--detail brief --explain` 组合。

**Q31：输入格式不对或文件有问题时会怎样？**
A：所有错误提示都采用通俗语言+解决建议的形式，不会输出难懂的技术堆栈。例如：文件不存在时会提示"找不到这个文件，请检查路径是否正确"；空文件时提示"日志内容是空的，请粘贴包含 ERROR 的日志片段"；全是正常日志时提示"没有找到明显的错误信息，看起来都是正常运行记录"；二进制文件时提示"请提供文本格式的日志文件"。还有全局异常兜底，任何意外错误都有友好提示，不会直接崩溃。如需查看详细错误信息，可加 -v 参数。

## 最佳实践

1. **提供完整堆栈**：最顶层（异常发生点）和最底层（调用入口）最关键。
2. **附带上下文**：服务名、环境、traceId 能大幅提升定位准确性。
3. **保留报错前后日志**：报错前 5-10 行 INFO/WARN 往往包含触发条件。
4. **一次一个故障**：多个不相关报错分开调用。
5. **脱敏后再粘贴**：移除密码、token、密钥、手机号等敏感数据。
6. **按排查清单验证**：根因候选是可能性列表，需用命令实际验证后再下结论。

## 用户常犯错误（反模式）

以下是用户使用时最常见的错误做法，以及为什么不好、应该怎么做：

### 错误 1：只发截图不发文字
- **错误做法**：拍一张日志截图发过来，说"帮我看看这个报错"
- **为什么不好**：截图 OCR 识别率不稳定，容易丢失堆栈行号、异常消息中的特殊字符，导致分析不准确
- **正确做法**：用鼠标选中日志文本，复制粘贴为纯文本。如果只能截图，请同时手打关键报错行

### 错误 2：只贴异常名称不贴堆栈
- **错误做法**：只说"报了空指针异常"或"超时了"，不贴具体日志
- **为什么不好**：没有堆栈无法定位具体代码行和调用链，只能给出泛泛的建议
- **正确做法**：至少粘贴异常类型 + 异常消息 + 最顶层 5 行堆栈

### 错误 3：截断堆栈关键行
- **错误做法**：堆栈太长，只贴了中间几行，省略了最顶层（异常发生点）和最底层（调用入口）
- **为什么不好**：最顶层告诉你异常在哪一行代码抛出，最底层告诉你从哪个入口调用进来的，两者缺一不可
- **正确做法**：保留堆栈首尾各 5-10 行，中间重复的框架行可以省略

### 错误 4：一次粘贴多个不相关故障
- **错误做法**：把今天遇到的 3 个不同报错全贴在一条消息里
- **为什么不好**：不同故障的根因和排查方向不同，混在一起会互相干扰，分析结果不准确
- **正确做法**：一次只分析一个故障，分开多次调用

### 错误 5：日志含敏感信息未脱敏
- **错误做法**：直接粘贴包含数据库密码、API token、用户手机号的完整日志
- **为什么不好**：敏感信息泄露风险，且分析不需要这些数据
- **正确做法**：粘贴前把密码、token、密钥、手机号替换为 `***`，不影响故障分析

### 错误 6：只说"服务挂了"不贴日志
- **错误做法**：只说"服务挂了，帮我看看"，不提供任何日志
- **为什么不好**：没有日志无法分析，只能猜测可能的原因
- **正确做法**：至少提供报错时的日志片段、错误码、或服务状态信息

### 错误 7：日志太长全量粘贴不筛选
- **错误做法**：把 1000 行包含大量 INFO 心跳/健康检查的日志全贴过来
- **为什么不好**：关键错误被噪音淹没，分析效率低，可能超出处理长度
- **正确做法**：优先保留 ERROR 行、完整堆栈、报错前后各 5-10 行上下文

## 硬性约束

1. 禁止编造不存在的日志内容；没有证据必须标注证据不足，并说明需要补充什么。
2. 排查步骤必须落地，给出检查点和命令示例，不空泛理论。
3. 支持多种日志类型：Java/Python/Go 堆栈、HTTP 报错、数仓任务、数据库、中间件报错。
4. 用户日志太长时只摘录关键报错片段，不全部输出；证据摘录应完整保留关键报错信息（异常类型、异常消息、堆栈顶层），仅在超过 500 字时加省略号。
5. 不闲聊，直接输出完整故障报告。
6. 遇到完全无法分析的输入，直接说明原因并给出正确输入方式，不强行输出。
7. 修复建议最多输出 3 个方案（按根因置信度排序），排查步骤不设硬上限但优先高置信项。
8. **低证据保守原则**：无堆栈、仅单条错误、缺少上下文时，根因置信度自动下调一级，并明确标注"基于有限证据，置信度已保守下调"。禁止在证据不足时给出高置信结论。
9. **错误计数准确原则**：错误信息计数需过滤误报（INFO 行中的 errorCode/errorMsg 不算错误、同一异常重复只计一次），报告中的错误数量应与实际关键错误一致。

## 调用方式

```
调用【故障日志诊断专家】，环境：测试环境，服务：order-service，下面是日志：
这里粘贴你的报错堆栈/日志片段
```

也可以直接说「帮我分析这个报错」「这个堆栈什么原因」「线上报错定位」等并粘贴日志。

## 使用场景

- 接口 500/502 报错堆栈分析
- Python/Java/Go 程序异常堆栈
- 数仓 ETL 任务失败日志（Spark/Hive/Flink）
- 数据库（MySQL/PostgreSQL/Oracle）报错日志
- Redis、RocketMQ、Kafka、Elasticsearch 中间件报错
- 线上告警文本快速定位
- 接口自动化测试失败排查
- OOM、死锁、连接超时等常见线上故障定位
