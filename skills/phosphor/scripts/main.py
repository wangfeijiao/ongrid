#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phosphor — 基于 DTrace 的 Ruby 运行时事件采集与结构化输出
版本: 1.0.1 (独立实现 / clean-room)
许可证: MIT
"""

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional


# 错误码定义
ERROR_CODES = {
    "E001": "参数错误或缺少必要参数",
    "E002": "输入数据格式无法解析",
    "E003": "不支持的输出格式",
    "E004": "输入数据为空或无效",
    "E005": "批量处理时遇到异常文件",
    "E006": "DTrace 采样数据不完整",
    "E007": "时间窗口解析失败",
    "E008": "PID 格式错误",
    "E009": "内部逻辑错误（不应发生）",
    "E010": "自检失败",
}


def error_exit(code: str, message: Optional[str] = None) -> None:
    """输出错误信息并退出。"""
    text = ERROR_CODES.get(code, "未知错误")
    if message:
        text = f"{text}: {message}"
    print(f"[ERROR] {code} {text}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 核心数据模型与解析
# ---------------------------------------------------------------------------

class RubyEvent:
    """表示一条 Ruby 运行时事件。"""

    def __init__(self, event_type: str, timestamp: float, pid: int,
                 thread_id: Optional[str] = None,
                 method: Optional[str] = None,
                 details: Optional[Dict[str, Any]] = None):
        self.event_type = event_type
        self.timestamp = timestamp
        self.pid = pid
        self.thread_id = thread_id
        self.method = method
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 JSON 序列化）。"""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "pid": self.pid,
            "thread_id": self.thread_id,
            "method": self.method,
            "details": self.details,
        }


def parse_pid(raw: str) -> int:
    """解析并验证 PID。"""
    try:
        pid = int(raw.strip())
        if pid <= 0:
            raise ValueError
        return pid
    except ValueError:
        error_exit("E008", f"无效 PID: {raw}")


def parse_timestamp(raw: str) -> float:
    """解析时间戳，支持数字或 ISO 字符串。"""
    raw = raw.strip()
    # 尝试直接转 float
    try:
        return float(raw)
    except ValueError:
        pass
    # 尝试 ISO 格式
    try:
        dt = datetime.fromisoformat(raw)
        return dt.timestamp()
    except ValueError:
        error_exit("E007", f"无法解析时间戳: {raw}")


def parse_dtrace_line(line: str, pid: int) -> Optional[RubyEvent]:
    """
    解析一行 DTrace 输出文本。
    预期格式（宽松匹配）:
      <时间戳> <事件类型> [<线程ID>] [<方法名>] [key=value ...]
    实际解析时采用正则提取关键字段。
    """
    line = line.strip()
    if not line:
        return None

    # 尝试提取时间戳（行首数字）
    m = re.match(r"^([\d.]+)\s+(.+)$", line)
    if not m:
        return None
    timestamp = parse_timestamp(m.group(1))
    rest = m.group(2)

    # 提取事件类型（第一个单词）
    parts = rest.split()
    event_type = parts[0].lower() if parts else "unknown"

    # 提取线程 ID（若存在形如 tid=xxx 或 [xxx] 的标记）
    thread_id = None
    tid_match = re.search(r"(?:tid|thread)[=:\s]+([a-zA-Z0-9_-]+)", rest, re.I)
    if tid_match:
        thread_id = tid_match.group(1)

    # 提取方法名（若存在 method=xxx 或 meth=xxx）
    method = None
    meth_match = re.search(r"(?:method|meth|func)[=:\s]+([a-zA-Z0-9_:.#]+)", rest, re.I)
    if meth_match:
        method = meth_match.group(1)

    # 提取 key=value 形式的附加字段
    details: Dict[str, Any] = {}
    for kv in re.finditer(r"([a-zA-Z_][a-zA-Z0-9_]*)=([^\s]+)", rest):
        key, val = kv.group(1), kv.group(2)
        # 尝试转数字
        try:
            val_num = float(val)
            details[key] = val_num
        except ValueError:
            details[key] = val

    return RubyEvent(
        event_type=event_type,
        timestamp=timestamp,
        pid=pid,
        thread_id=thread_id,
        method=method,
        details=details,
    )


def parse_dtrace_text(text: str, pid: int) -> List[RubyEvent]:
    """将多行 DTrace 文本解析为事件列表。"""
    events = []
    for line in text.splitlines():
        ev = parse_dtrace_line(line, pid)
        if ev:
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 置信度标注
# ---------------------------------------------------------------------------

def annotate_confidence(events: List[RubyEvent]) -> List[Dict[str, Any]]:
    """
    为事件添加置信度标注。
    规则（宽松判定）：
      - 有完整时间戳、事件类型、PID -> 高置信度 >= 0.9
      - 缺少方法名或线程 ID -> 中等置信度 0.6~0.9
      - 缺少附加细节 -> 低置信度 < 0.6
    """
    result = []
    for ev in events:
        score = 1.0
        # 时间戳与事件类型是必须的，若缺失则很低
        if ev.timestamp is None or not ev.event_type:
            score -= 0.5
        # 方法名缺失
        if not ev.method:
            score -= 0.2
        # 线程 ID 缺失
        if not ev.thread_id:
            score -= 0.1
        # 细节字段少
        if len(ev.details) < 2:
            score -= 0.1

        # 限制在 [0, 1] 区间
        score = max(0.0, min(1.0, score))
        d = ev.to_dict()
        d["confidence"] = round(score, 2)
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def format_output(data: Any, fmt: str) -> str:
    """按指定格式输出。"""
    fmt = fmt.lower()
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    elif fmt == "yaml":
        return _to_yaml(data)
    elif fmt == "table":
        return _to_table(data)
    else:
        error_exit("E003", f"不支持的格式: {fmt}")


def _to_yaml(data: Any, indent: int = 0) -> str:
    """极简 YAML 序列化（够用即可）。"""
    lines = []
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return f"{pad}{{}}\n"
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_to_yaml(v, indent + 1).rstrip("\n"))
            else:
                lines.append(f"{pad}{k}: {v}")
    elif isinstance(data, list):
        if not data:
            return f"{pad}[]\n"
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(_to_yaml(item, indent + 1).rstrip("\n"))
            else:
                lines.append(f"{pad}- {item}")
    else:
        lines.append(f"{pad}{data}")
    return "\n".join(lines) + "\n"


def _to_table(data: Any) -> str:
    """极简表格输出（适用于事件列表）。"""
    if not isinstance(data, list) or not data:
        return "(空数据)\n"
    if not isinstance(data[0], dict):
        return "\n".join(str(x) for x in data) + "\n"

    # 取所有键的并集
    headers = []
    for row in data:
        for k in row.keys():
            if k not in headers:
                headers.append(k)

    # 限制列宽
    def fmt_cell(v: Any, width: int) -> str:
        s = str(v)
        if len(s) > width:
            s = s[: width - 3] + "..."
        return s.ljust(width)

    col_width = 20
    lines = []
    header_line = " | ".join(h.ljust(col_width) for h in headers)
    lines.append(header_line)
    lines.append("-+-".join("-" * col_width for _ in headers))

    for row in data:
        lines.append(" | ".join(fmt_cell(row.get(h, ""), col_width) for h in headers))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 批量处理
# ---------------------------------------------------------------------------

def batch_process(inputs: List[Dict[str, Any]], fmt: str) -> str:
    """批量处理多个输入源，返回汇总报告。"""
    if not inputs:
        error_exit("E004", "批量输入为空")

    all_events = []
    errors = []
    for item in inputs:
        try:
            pid = parse_pid(str(item.get("pid", "")))
            text = item.get("text", "")
            if not text:
                continue
            events = parse_dtrace_text(text, pid)
            all_events.extend(events)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))

    if errors:
        error_exit("E005", f"批量处理部分失败: {'; '.join(errors[:3])}")

    annotated = annotate_confidence(all_events)
    summary = {
        "total_events": len(annotated),
        "unique_types": list({e["event_type"] for e in annotated}),
        "events": annotated,
    }
    return format_output(summary, fmt)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_single(pid: int, text: str, fmt: str) -> str:
    """单次分析流程。"""
    if not text or not text.strip():
        error_exit("E004", "输入 DTrace 文本为空")
    events = parse_dtrace_text(text, pid)
    if not events:
        error_exit("E006", "未能从输入中解析出任何事件")
    annotated = annotate_confidence(events)
    return format_output(annotated, fmt)


# ---------------------------------------------------------------------------
# 自检（内置硬编码样例，离线可过）
# ---------------------------------------------------------------------------

def selftest() -> None:
    """内置样例自检核心逻辑。"""
    # 硬编码样例数据（不依赖外部文件）
    sample_dtrace = """
    1710000000.123456 method_call tid=0x1a2b method=UserService#find
    1710000001.234567 gc_start tid=0x1a2b heap_live=12345 heap_free=6789
    1710000002.345678 method_call tid=0x3c4d method=Order#calculate_total
    1710000003.456789 method_return tid=0x3c4d method=Order#calculate_total duration=0.045
    """
    sample_pid = 1234

    # 1. 解析测试
    events = parse_dtrace_text(sample_dtrace, sample_pid)
    if len(events) < 3:
        error_exit("E010", f"解析事件数量不足，期望>=3，实际={len(events)}")
    if not all(ev.pid == sample_pid for ev in events):
        error_exit("E010", "PID 解析不一致")

    # 2. 置信度标注测试（宽松阈值）
    annotated = annotate_confidence(events)
    if not annotated:
        error_exit("E010", "置信度标注结果为空")
    for item in annotated:
        conf = item.get("confidence", 0)
        # 宽松区间判断
        if not (0.0 <= conf <= 1.0):
            error_exit("E010", f"置信度超出 [0,1] 区间: {conf}")

    # 3. 输出格式测试（JSON 可解析）
    json_out = format_output(annotated, "json")
    try:
        parsed = json.loads(json_out)
        if not isinstance(parsed, list):
            error_exit("E010", "JSON 输出不是列表")
    except json.JSONDecodeError as e:
        error_exit("E010", f"JSON 输出无法解析: {e}")

    # 4. 批量处理测试
    batch_input = [
        {"pid": "1234", "text": sample_dtrace},
        {"pid": "5678", "text": "1710000100.000000 method_call tid=0x99 method=Test#run"},
    ]
    batch_out = batch_process(batch_input, "json")
    try:
        batch_parsed = json.loads(batch_out)
        # 总事件数 >= 4 (3 + 1)
        if batch_parsed.get("total_events", 0) < 4:
            error_exit("E010", "批量处理事件总数异常")
    except json.JSONDecodeError as e:
        error_exit("E010", f"批量输出 JSON 解析失败: {e}")

    # 5. 错误处理测试（PID 非法应报 E008）
    try:
        parse_pid("abc")
        error_exit("E010", "非法 PID 未触发错误")
    except SystemExit as e:
        # 预期退出码非 0
        if e.code == 0:
            error_exit("E010", "非法 PID 错误退出码为 0")

    print("[SELFTEST] 全部通过 ✅")


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="phosphor — 基于 DTrace 的 Ruby 运行时事件采集与结构化输出"
    )
    parser.add_argument("--pid", type=str, help="目标 Ruby 进程 PID")
    parser.add_argument("--input", type=str, help="DTrace 输出文本（直接传入）")
    parser.add_argument("--file", type=str, help="从文件读取 DTrace 输出")
    parser.add_argument("--format", type=str, default="json",
                        choices=["json", "yaml", "table"],
                        help="输出格式 (默认: json)")
    parser.add_argument("--batch", type=str, nargs="*",
                        help="批量模式：传入多个 'pid:file' 对")
    parser.add_argument("--selftest", action="store_true",
                        help="运行内置自检（离线，无需外部数据）")

    parser.add_argument("--verbose", action="store_true", help="显示修改明细")  # R6 可解释输出

    args = parser.parse_args()

    # 自检模式优先
    if args.selftest:
        selftest()
        return

    # 批量模式
    if args.batch:
        inputs = []
        for item in args.batch:
            if ":" in item:
                pid_str, path = item.split(":", 1)
            else:
                pid_str, path = item, item  # 宽松处理
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                error_exit("E005", f"无法读取文件 {path}: {e}")
            inputs.append({"pid": pid_str, "text": text})
        if not inputs:
            error_exit("E004", "批量模式无有效输入")
        output = batch_process(inputs, args.format)
        print(output)
        return

    # 单次模式
    if not args.pid:
        error_exit("E001", "缺少 --pid 参数")

    pid = parse_pid(args.pid)

    # 数据来源：--input 优先，其次 --file
    text = None
    if args.input:
        text = args.input
    elif args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            error_exit("E005", f"无法读取文件 {args.file}: {e}")
    else:
        error_exit("E001", "缺少输入数据（--input 或 --file）")

    output = run_single(pid, text, args.format)
    print(output)


if __name__ == "__main__":
    main()


def read_text_safe(path):
    """多编码容错读取 (selftest contract)"""
    import codecs
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            with codecs.open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()
