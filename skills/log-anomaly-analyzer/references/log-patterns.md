# 日志格式识别与解析说明

分析脚本 `analyze_log.py` 会自动识别以下格式，本文件说明识别逻辑与各格式的特征，供 AI 在深度分析时参考。

## 支持的三种格式

### 1. JSON 结构化日志（最易解析）
特征：每行一个 JSON 对象，以 `{` 开头 `}` 结尾。

```json
{"time":"2024-01-15 03:10:11","level":"error","msg":"connection refused"}
```

脚本会提取：`level` / `severity` / `loglevel` 字段为级别，`message` / `msg` / `log` 字段为消息，`time` / `timestamp` / `@timestamp` 为时间。

### 2. key=value 格式
特征：行内包含 `key=value` 或 `key: value`，且含 `level` / `msg` / `time` 等键。

```
time=2024-01-15T03:10:11 level=ERROR msg="connection refused"
```

### 3. 纯文本格式（兜底）
特征：时间戳 + 可选级别标记 + 消息。

```
2024-01-15 03:31:00,123 ERROR [payment] refund failed for order 5521
[ERROR] something went wrong
```

脚本用正则提取级别（`ERROR`/`WARN`/`INFO`/`DEBUG` 等，支持 `[ERROR]`、` ERROR `、` ERROR:` 三种写法）和时间戳。

时间戳支持两种格式：`YYYY-MM-DD HH:MM:SS` 和 Apache 风格 `DD/Mon/YYYY:HH:MM:SS`（如 `30/Aug/2026:16:16:27`）。文件编码自动识别 UTF-8 与 GBK/GB2312。

---

## 变量掩码聚类原理

脚本把消息中的「变量」替换为占位符，再把相同的「模板」归为一类。这是 Splunk / Datadog 日志聚合的核心思路。

| 原始消息 | 掩码后模板 |
|----------|-----------|
| `refund failed for order 5521` | `refund failed for order <NUM>` |
| `refund failed for order 5522` | `refund failed for order <NUM>` |
| `connection refused to db host 10.0.0.5` | `connection refused to db host <IP>` |
| `GET /api/orders returned 500 in 1245ms` | `GET /api/orders returned <5xx> in <DUR>` |

占位符类型：`<NUM>`（数字）、`<IP>`（IP 地址）、`<UUID>`、`<URL>`、`<EMAIL>`、`<TIME>`（时间戳）、`<DUR>`（时长 ms/s）、`<SIZE>`（KB/MB/GB）、`<HASH>`（长哈希）、`<HEX>`（0x 十六进制）、`<2xx>`/`<5xx>`（HTTP 状态码，按首位区分成功/失败）。

---

## 堆栈识别

脚本识别以下堆栈/异常特征，提取异常类型用于分组：
- Python：`Traceback (most recent call last):`、`File "x.py", line N`、`XxxError: message`
- Java：`Exception in thread "..."`、`at com.foo.Bar.method(...)`、`Caused by:`
- Go：`panic: ...`、`goroutine N [running]:`
- Node：`at Object.<anonymous> (...)`、`Error: ...`

堆栈帧行（`at ...` / `File ...`）不会进入「高频模式」列表（避免噪声），但异常类型会单独统计。

---

## 脚本输出各部分的含义

| 输出块 | 含义 | 用途 |
|--------|------|------|
| 日志级别分布 | 各级别行数与占比 | 判断整体健康度 |
| 高频异常模式 | ERROR/WARN 聚类模板 + 频次（异常优先） | 定位「最常发生的异常」 |
| 正常流量模式 | INFO/DEBUG 的粗聚类（前 5 条） | 了解主要正常流量 |
| 时间线突增检测 | 按时间桶的错误量（中位数基线） | 判断突发 vs 持续 |
| 异常类型 TOP | 异常类名 + 次数 | 定位根因类型 |
| 高频错误状态码/接口/IP | 来源排行 | 定位影响范围 |

---

## 已知限制（如实说明）

分析脚本基于「逐行解析」，以下场景暂不支持或支持不完整：

1. **多行 JSON**：pretty-printed JSON（一个对象跨多行）无法正确解析，每个字段行会被当成独立日志。建议先把 JSON 压成单行（如 `jq -c` 或 `json.dumps`）。
2. **syslog（RFC 5424）**：`<PRI>` 优先级字段会被当数字掩码，不影响聚类但展示不干净。
3. **非标准时间戳**：仅支持 `YYYY-MM-DD` 和 `DD/Mon/YYYY` 两种，其他格式（如 epoch 时间戳）不参与时间线突增检测。
4. **超大文件**：138MB 日志约需 50 秒（纯 Python 正则逐行处理），几 MB 的日志很快；性能极限受零依赖方案约束。
