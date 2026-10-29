#!/usr/bin/env python3
"""
日志异常检测 —— 预演版核心分析脚本。

功能：
  1. 从本地日志文件 或 命令输出(stdin) 读取日志行。
  2. 解析时间戳，统计最近窗口(默认30分钟)内 ERROR / WARNING 频率。
  3. 与历史基线窗口(默认近24小时)比较。
  4. 按偏差幅度输出 warning / critical 判定，并给出样例错误行。

设计原则：
  - 零第三方依赖，仅用 Python 标准库。
  - 参数化(命令行传参)，便于复用与冒烟测试。
  - 时间戳解析兜底：解析失败的行不参与时间分桶，但计入总数，避免静默丢失。

用法：
  python analyze.py --source <文件路径|目录|glob|-> [--window 30 --baseline 1440 ...]

  --source 为 "-" 时从 stdin 读取(供 `docker logs ... | python analyze.py --source -` 使用)。
"""

import argparse
import glob
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

# 常见日志时间戳格式 -> strptime 格式
TS_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S,%f",
    "%b %d %H:%M:%S",
    "%b  %d %H:%M:%S",
    "%d/%b/%Y:%H:%M:%S",  # nginx access log
    "%Y-%m-%dT%H:%M:%S.%f%z",
]

# 时间戳提取正则：优先匹配显式日期时间
TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"|(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)"
    r"|(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2})"
    r"|(\w{3} +\d{1,2} \d{2}:\d{2}:\d{2})"
)

# 级别识别：优先级 ERROR > CRITICAL > FATAL > WARNING > WARN > INFO > DEBUG
LEVEL_ORDER = ["CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG"]
LEVEL_RE = re.compile(r"\b(CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE)\b", re.IGNORECASE)

# 行级优先级：用于判断「样例错误」的严重程度排序
SEVERITY_RANK = {"CRITICAL": 5, "FATAL": 5, "ERROR": 4, "WARNING": 3, "WARN": 3, "INFO": 2, "DEBUG": 1, "TRACE": 0}


def parse_ts(line, year_hint):
    """从日志行提取时间戳，返回 datetime(带时区感知统一为 UTC naive)。"""
    m = TS_RE.search(line)
    if not m:
        return None
    raw = next(g for g in m.groups() if g)
    raw = raw.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    for fmt in TS_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt
        except ValueError:
            continue
    # 无年份格式(如 syslog "Oct 6 21:00:00")：用 year_hint 补年份
    for fmt in ("%b %d %H:%M:%S", "%b  %d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(year=year_hint)
        except ValueError:
            continue
    return None


def detect_level(line):
    """返回该行日志级别(大写)，无级别返回 None。"""
    m = LEVEL_RE.search(line)
    return m.group(1).upper() if m else None


def classify(line):
    """把一行归类为 error / warn / other。"""
    lvl = detect_level(line)
    if lvl in ("CRITICAL", "FATAL", "ERROR"):
        return "error", lvl
    if lvl in ("WARNING", "WARN"):
        return "warn", lvl
    # 兜底：无显式级别但包含错误关键词
    low = line.lower()
    if any(k in low for k in ("exception", "traceback", "fail", "failed", "panic", "fatal")):
        return "error", "ERROR"
    return "other", lvl


def read_lines(source):
    """按 source 读取日志行，返回生成器。source='-' 读 stdin。"""
    if source == "-":
        yield from sys.stdin
        return
    paths = []
    for part in source.split(","):
        part = part.strip()
        if not part:
            continue
        if any(ch in part for ch in "*?["):
            paths.extend(glob.glob(part))
        elif os.path.isdir(part):
            paths.extend(sorted(glob.glob(os.path.join(part, "*"))))
        else:
            paths.append(part)
    for p in paths:
        if not os.path.isfile(p):
            print(f"[warn] 文件不存在，跳过: {p}", file=sys.stderr)
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                yield from f
        except OSError as e:
            print(f"[warn] 读取失败 {p}: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="日志异常检测预演版")
    ap.add_argument("--source", required=True,
                    help="日志来源：文件路径/目录/glob(逗号分隔)，或 '-' 表示从 stdin 读取")
    ap.add_argument("--window", type=int, default=30, help="检测窗口(分钟)，默认 30")
    ap.add_argument("--baseline", type=int, default=1440, help="基线窗口(分钟)，默认 1440(24h)")
    ap.add_argument("--warn-ratio", type=float, default=2.0,
                    help="warning 速率比阈值(窗口/基线)，默认 2.0")
    ap.add_argument("--crit-ratio", type=float, default=5.0,
                    help="critical 速率比阈值(窗口/基线)，默认 5.0")
    ap.add_argument("--warn-count", type=int, default=5,
                    help="warning 绝对数量门槛：窗口 ERROR 需 ≥ 该数，默认 5")
    ap.add_argument("--crit-count", type=int, default=10,
                    help="critical 绝对数量门槛：窗口 ERROR 需 ≥ 该数，默认 10")
    ap.add_argument("--min-count", type=int, default=3,
                    help="窗口内 ERROR 少于该数时不告警，默认 3(避免零星噪声)")
    ap.add_argument("--top", type=int, default=5, help="输出样例错误行数，默认 5")
    ap.add_argument("--now", default=None,
                    help="覆盖当前时间(格式 YYYY-MM-DD HH:MM:SS)，用于测试/回放历史日志")
    args = ap.parse_args()

    if args.now:
        now = datetime.strptime(args.now, "%Y-%m-%d %H:%M:%S")
    else:
        now = datetime.now()
    year_hint = now.year
    win_start = now - timedelta(minutes=args.window)
    base_start = now - timedelta(minutes=args.baseline)

    win_err = 0      # 检测窗口内 error 数
    win_warn = 0     # 检测窗口内 warn 数
    base_err = 0     # 基线窗口内 error 数
    base_warn = 0
    total = 0        # 总行数
    no_ts = 0        # 无时间戳行数
    win_err_samples = []   # (severity, line) 用于样例
    base_err_by_min = defaultdict(int)  # 基线按分钟分桶，算均值和波动

    for line in read_lines(args.source):
        total += 1
        ts = parse_ts(line, year_hint)
        if ts is None:
            no_ts += 1
            continue
        cat, lvl = classify(line)
        if cat != "error":
            if cat == "warn":
                if ts >= win_start:
                    win_warn += 1
                elif ts >= base_start:
                    base_warn += 1
            continue
        if ts >= win_start:
            win_err += 1
            win_err_samples.append((SEVERITY_RANK.get(lvl or "ERROR", 4), line))
        elif ts >= base_start:
            base_err += 1
            base_err_by_min[ts.strftime("%Y-%m-%d %H:%M")] += 1

    # 基线窗口可能远大于检测窗口，用「分钟均值」归一化比较
    baseline_span_min = max(args.baseline - args.window, 1)
    base_err_per_min = base_err / baseline_span_min
    win_err_per_min = win_err / args.window

    # 判定：比例 + 绝对双阈值，两者同时满足才升级
    verdict = "normal"
    ratio = None
    if win_err >= args.min_count and base_err_per_min > 0:
        ratio = win_err_per_min / base_err_per_min
        if ratio >= args.crit_ratio and win_err >= args.crit_count:
            verdict = "critical"
        elif ratio >= args.warn_ratio and win_err >= args.warn_count:
            verdict = "warning"
    elif win_err >= args.min_count and base_err_per_min == 0:
        # 基线几乎无错误，但窗口出现错误：用绝对数量分级
        if win_err >= args.crit_count:
            verdict = "critical"
            ratio = float("inf")
        elif win_err >= args.warn_count:
            verdict = "warning"
            ratio = float("inf")

    # 输出报告
    print("=" * 64)
    print(f"日志异常检测报告  (生成时间 {now:%Y-%m-%d %H:%M:%S})")
    print("=" * 64)
    print(f"检测窗口: 最近 {args.window} 分钟")
    print(f"基线窗口: 最近 {args.baseline} 分钟")
    print(f"总行数: {total}   (无法解析时间戳: {no_ts})")
    print("-" * 64)
    print(f"窗口 ERROR   : {win_err}")
    print(f"窗口 WARNING : {win_warn}")
    print(f"基线 ERROR   : {base_err}  (约 {base_err_per_min:.2f}/分钟)")
    print(f"基线 WARNING : {base_warn}")
    print(f"窗口 ERROR 速率: {win_err_per_min:.2f}/分钟")
    if ratio is not None:
        print(f"速率比(窗口/基线): {ratio:.2f}x")
    print("-" * 64)
    print(f"判定: {verdict.upper()}")
    if verdict == "critical":
        print("  -> 需要立即处理：错误率显著高于历史基线，建议马上排查。")
    elif verdict == "warning":
        print("  -> 需关注：错误率偏高，建议持续观察或提前排查。")
    else:
        print("  -> 正常：错误率处于历史基线范围内。")
    print("=" * 64)

    if win_err_samples:
        print(f"\n样例错误 (共 {len(win_err_samples)} 条，展示前 {args.top} 条):")
        win_err_samples.sort(key=lambda x: -x[0])
        for i, (_, ln) in enumerate(win_err_samples[:args.top], 1):
            print(f"  [{i}] {ln.strip()[:200]}")
    else:
        print("\n样例错误: 无")


if __name__ == "__main__":
    main()
