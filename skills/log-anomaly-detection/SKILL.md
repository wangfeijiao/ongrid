---
name: log-anomaly-detection
version: 1.0.0
display_name: 日志异常检测
display_name_en: Log Anomaly Detection
description_zh: "日志异常检测。当用户要求分析日志、检测错误率异常、监控 ERROR/WARNING 趋势、对比历史基线、识别 warning/critical 级别告警，或需要从日志文件或命令输出(如 docker logs、kubectl logs、tail)中自动发现异常时使用。核心能力：自动统计最近时间窗口(默认30分钟)的 ERROR/WARNING 频率，与近24小时历史基线比较，输出 warning/critical 分级判定与样例错误行，减少无效报警、提前捕获夜间问题。"
description_en: "Log anomaly detection. Use when the user needs to analyze logs, detect error-rate anomalies, monitor ERROR/WARNING trends, compare against historical baselines, identify warning/critical alerts, or automatically spot anomalies from log files or command output (e.g. docker logs, kubectl logs, tail). Core capability: automatically count ERROR/WARNING frequency over the recent time window (default 30 minutes), compare against a 24-hour baseline, and output warning/critical verdicts plus sample error lines."
description: "日志异常检测。当用户要求分析日志、检测错误率异常、监控 ERROR/WARNING 趋势、对比历史基线、识别 warning/critical 级别告警，或需要从日志文件或命令输出(如 docker logs、kubectl logs、tail)中自动发现异常时使用。核心能力：自动统计最近时间窗口(默认30分钟)的 ERROR/WARNING 频率，与近24小时历史基线比较，输出 warning/critical 分级判定与样例错误行，减少无效报警、提前捕获夜间问题。"
agent_created: true
---

# 日志异常检测 (Log Anomaly Detection)

## 概述

本技能把「当前错误率 vs 历史基线」的异常判定做成一条可复用流水线。核心是 `scripts/analyze.py`：读日志 → 解析时间戳 → 统计最近窗口(默认 30 分钟)的 ERROR/WARNING 频率 → 与历史基线(默认近 24 小时)按分钟均值归一化对比 → 输出 warning/critical 判定 + 样例错误行。

三种价值：
- **自动对比基线**：不用人盯，把「异常」量化成「速率比」。
- **区分 warning / urgent**：按偏差倍数分级，减少无效报警。
- **提前捕获夜间问题**：在影响用户之前就给出 critical 提示。

## 何时使用

- 用户要求「分析日志」「检测日志异常」「看错误率」「对比历史基线」。
- 用户给了日志文件路径、目录、glob 模式，或某个日志命令(如 `docker logs`、`kubectl logs`、`tail -f`)。
- 用户想给某个服务加「夜间异常自动捕获」能力。

## 输入方式（两种都支持）

1. **本地文件/目录/glob**：`--source` 传文件路径、目录(自动读目录下全部文件)或 glob 模式(如 `/var/log/*.log`)，多个用逗号分隔。
2. **命令输出(stdin)**：`--source -`，从标准输入读取，配合管道使用。

日志行需含可解析的时间戳与级别关键字(ERROR/WARNING 等)。脚本内置多种常见格式(ISO8601、syslog、nginx 等)，解析失败的行不参与时间分桶但会计入总数并在报告中标明，不会静默丢失。

## 使用步骤

1. 确定日志来源与时间参数。默认窗口 30 分钟、基线 24 小时，通常无需改；如有特殊需求再传 `--window` / `--baseline`。
2. 调用脚本：

   ```bash
   python analyze.py --source /var/log/app.log
   # 或读目录/glob
   python analyze.py --source "/var/log/app*.log"
   # 或从命令输出
   docker logs my-service --since 24h | python analyze.py --source -
   ```

3. 读取脚本输出，把判定结论(warning/critical/normal)与样例错误行转述给用户，并给出建议动作。

## 判定逻辑与阈值（脚本已内置，可调）

| 参数 | 默认 | 含义 |
|------|------|------|
| `--window` | 30 | 检测窗口(分钟) |
| `--baseline` | 1440 | 基线窗口(分钟，即 24h) |
| `--warn-ratio` | 2.0 | warning 速率比阈值(窗口/基线) |
| `--crit-ratio` | 5.0 | critical 速率比阈值(窗口/基线) |
| `--warn-count` | 5 | warning 绝对数量门槛：窗口 ERROR 需 ≥ 该数 |
| `--crit-count` | 10 | critical 绝对数量门槛：窗口 ERROR 需 ≥ 该数 |
| `--min-count` | 3 | 窗口内 ERROR 少于该数时不告警，避免零星噪声 |
| `--top` | 5 | 展示的样例错误行数 |
| `--now` | 无 | 覆盖「当前时间」，用于测试/回放历史日志 |

判定规则（**比例 + 绝对双阈值，两者同时满足才升级**）：
- 窗口 ERROR 数 < `--min-count` → **normal**（零星噪声，不打扰）。
- 速率比 ≥ crit-ratio **且** 窗口 ERROR ≥ crit-count → **critical**。
- 速率比 ≥ warn-ratio **且** 窗口 ERROR ≥ warn-count → **warning**。
- 基线错误数为 0（速率比视为 ∞）时，用绝对数量分级：窗口 ERROR ≥ crit-count → critical；≥ warn-count → warning。

「速率比」= (窗口错误数 / 窗口分钟) ÷ (基线错误数 / 基线分钟)，用分钟均值归一化，避免「24 小时基线总数 vs 30 分钟窗口总数」直接相除导致的量纲偏差。

**为什么加绝对数量门槛**：当基线错误率极低（如每小时仅 1 条）时，窗口多几条错误就会让速率比爆表（4 条 ≈ 8x），纯比例阈值会把所有小尖峰误报成 critical。双阈值能过滤这类噪声——只有当错误「既相对异常、又绝对够多」时才告警。

## 输出报告解读

脚本输出包含：检测/基线窗口、总行数与无法解析时间戳的行数、窗口与基线的 ERROR/WARNING 计数、速率比、最终判定，以及按严重程度排序的样例错误行。向用户汇报时，应明确给出：判定级别 + 速率比 + 1~2 条典型错误，并说明建议动作（critical=立即排查，warning=持续观察，normal=无需处理）。

## 资源

### scripts/analyze.py
核心分析脚本。零第三方依赖(仅标准库)，参数化，可直接执行或读入后按需调整时间戳格式列表。

## 注意事项

- 若日志不含时间戳(纯文本流)，脚本无法做时间分桶，会把全部行计入总数但无法做基线对比——此时应提示用户日志缺少时间戳，或改用「近期 N 行」的简化口径。
- 基线窗口内错误数为 0 时，速率比视为无穷大，直接判 critical，这是符合直觉的「从无到有」处理。
- 阈值(warn/crit ratio)是通用默认值，不同服务量级差异大，可按需用命令行参数调整。
