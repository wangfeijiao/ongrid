#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志异常聚合分析器 —— 零依赖，仅用 Python 标准库。

核心能力（对标 Splunk / Datadog 的日志聚合思路）：
  1. 自动识别日志格式：JSON / key=value / 纯文本；
  2. 日志模式聚类：用变量掩码把消息归并为「模板」，统计每类出现频次；
  3. 时间线突增检测：按时间桶统计错误，标记异常尖峰；
  4. 堆栈/异常分组：识别多行堆栈，按异常类型与根因帧归组；
  5. 来源排行：高频 IP、HTTP 状态码、接口路径。

用法:
  python3 analyze_log.py <日志文件> [--top N] [--bucket 3600] [--json]
"""
import argparse
import collections
import json
import re
import sys
from datetime import datetime

# ==================== 日志级别 ====================
LEVEL_NORM = {
    'fatal': 'FATAL', 'critical': 'CRITICAL', 'panic': 'PANIC', 'error': 'ERROR',
    'err': 'ERROR', 'severe': 'SEVERE', 'warn': 'WARN', 'warning': 'WARN',
    'notice': 'NOTICE', 'info': 'INFO', 'debug': 'DEBUG', 'trace': 'TRACE',
}
ERROR_LEVELS = {'FATAL', 'CRITICAL', 'PANIC', 'ERROR', 'SEVERE'}
REAL_STATUS_CODES = {'400', '401', '403', '404', '405', '408', '409', '410', '413',
                     '414', '415', '422', '429', '500', '501', '502', '503', '504',
                     '505', '507'}

# ==================== 变量掩码正则 ====================
# 负向后顾排除 User-Agent 里的版本号（如 Chrome/151.0.0.0）
IP_RE = re.compile(r'(?<!/)\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
INT_RE = re.compile(r'\b\d+\b')

# 合并掩码：单次正则扫描完成所有变量归一（性能远优于逐条替换）
MASK_RE = re.compile(
    r'(?P<time>\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?'
    r'|\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)'
    r'|(?P<uuid>\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b)'
    r'|(?P<url>https?://[^\s"\'\]]+)'
    r'|(?P<email>\b[\w.+-]+@[\w-]+\.[\w.]+\b)'
    r'|(?P<ip>\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)'
    r'|(?P<hash>\b[0-9a-fA-F]{32,}\b)'
    r'|(?P<hex>\b0x[0-9a-fA-F]+\b)'
    r'|(?P<dur>\b\d+(?:\.\d+)?\s*(?:ms|µs|us|ns|s)\b)'
    r'|(?P<size>\b\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB)\b)'
    r'|(?P<status>(?<![:.\w])[2345]\d\d\b)'
    r'|(?P<float>\b\d+\.\d+\b)'
    r'|(?P<int>\b\d+\b)',
    re.I)

MASK_MAP = {'time': '<TIME>', 'uuid': '<UUID>', 'url': '<URL>', 'email': '<EMAIL>',
            'ip': '<IP>', 'hash': '<HASH>', 'hex': '<HEX>', 'dur': '<DUR>', 'size': '<SIZE>'}

# 轻量掩码：仅用于 INFO 等正常流量，只掩时间戳/IP/数字，避免大正则开销
LIGHT_MASK_RE = re.compile(
    r'(?P<time>\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?'
    r'|\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)'
    r'|(?P<ip>\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)'
    r'|(?P<num>\d+)',
    re.I)


def mask(msg, full=True):
    """把消息中的变量替换为占位符。full=True 用完整掩码（异常行），否则用轻量掩码"""
    def repl_full(m):
        for g, tag in MASK_MAP.items():
            if m.group(g):
                return tag
        if m.group('status'):
            return f'<{m.group("status")[0]}xx>'
        return '<NUM>'

    def repl_light(m):
        if m.group('time'):
            return '<TIME>'
        if m.group('ip'):
            return '<IP>'
        return '<NUM>'

    if full:
        return MASK_RE.sub(repl_full, msg).strip()
    return LIGHT_MASK_RE.sub(repl_light, msg).strip()


# ==================== 格式识别与解析 ====================
TS_RE = re.compile(r'(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?'
                   r'|\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)')


TS_PARTS_RE = re.compile(r'(\d{4})[-/](\d{2})[-/](\d{2})[T ](\d{2}):(\d{2}):(\d{2})')
APACHE_TS_RE = re.compile(r'(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})')
MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
          'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}


def parse_ts(raw):
    if not raw:
        return None
    m = TS_PARTS_RE.search(raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            return None
    m = APACHE_TS_RE.search(raw)
    if m:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)),
                            int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except (ValueError, KeyError):
            return None
    return None


def parse_json(line):
    """返回 (level, message, timestamp_str) 或 None"""
    if not (line.startswith('{') and line.rstrip().endswith('}')):
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    level = (obj.get('level') or obj.get('severity') or obj.get('loglevel')
             or obj.get('log.level') or obj.get('logLevel') or '')
    msg = (obj.get('message') or obj.get('msg') or obj.get('log')
           or obj.get('@message') or obj.get('content') or '')
    ts = (obj.get('time') or obj.get('timestamp') or obj.get('@timestamp')
          or obj.get('ts') or '')
    if isinstance(msg, (dict, list)):
        msg = json.dumps(msg, ensure_ascii=False)
    return str(level), str(msg), str(ts)


def parse_kv(line):
    """key=value 格式，返回 (level, message, timestamp_str)"""
    lvl = re.search(r'\b(?:level|severity|loglevel)\s*[:=]\s*"?([A-Za-z]+)"?', line, re.I)
    msg = ''
    m = re.search(r'\b(?:msg|message)\s*[:=]\s*"([^"]*)"', line, re.I)
    if m:
        msg = m.group(1)
    else:
        m = re.search(r'\b(?:msg|message)\s*[:=]\s*(\S+)', line, re.I)
        msg = m.group(1) if m else ''
    ts = TS_RE.search(line)
    return (lvl.group(1) if lvl else '', msg, ts.group(1) if ts else '')


LEVEL_TOKEN_RE = re.compile(
    r'(?:\b|\]\s*)(FATAL|CRITICAL|PANIC|ERROR|SEVERE|WARN|WARNING|NOTICE|INFO|DEBUG|TRACE)(?:\b|\s*\[|:)',
    re.I)


def parse_plain(line):
    """纯文本日志，返回 (level, message, timestamp_str)"""
    lvl = ''
    m = LEVEL_TOKEN_RE.search(line)
    if m:
        lvl = m.group(1)
    ts = TS_RE.search(line)
    msg = line
    # 去掉时间戳前缀，让消息更干净
    msg = TS_RE.sub('', msg).strip()
    return lvl, msg, (ts.group(1) if ts else '')


# ==================== 堆栈识别 ====================
EXCEPTION_RE = re.compile(
    r'\b([A-Za-z_][\w.]*(?:Exception|Error|Panic|Fault))\b')
STACK_FRAME_RE = re.compile(r'^\s*(?:at\s+\S+|File\s+".+?",\s+line\s+\d+|#\d+\s+)')
ACCESS_RE = re.compile(r'"(?:GET|POST|PUT|DELETE|PATCH)\s+(\S+)[^"]*"\s+([2345]\d\d)')


def parse_line(line):
    """统一解析入口，返回 (level, message, timestamp_str)"""
    line = line.rstrip('\n')
    if not line.strip():
        return '', '', ''
    r = parse_json(line)
    if r:
        return r
    if '=' in line and re.search(r'\b(level|msg|message|time|ts)\s*=', line, re.I):
        return parse_kv(line)
    return parse_plain(line)


# ==================== 主分析 ====================
def detect_encoding(path):
    """探测文件编码：优先 UTF-8，回退 GB18030（覆盖 GBK/GB2312）"""
    with open(path, 'rb') as f:
        sample = f.read(1048576)
    for enc in ('utf-8', 'gb18030'):
        try:
            sample.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return 'utf-8'


def analyze(path, top_n, bucket_sec):
    patterns = collections.Counter()      # 模板 -> 次数
    pattern_samples = {}                  # 模板 -> 一个样例
    pattern_levels = {}                   # 模板 -> Counter(级别)
    level_count = collections.Counter()   # 级别 -> 次数
    time_buckets = collections.Counter()  # 时间桶 -> 次数
    exceptions = collections.Counter()    # 异常类型 -> 次数
    ips = collections.Counter()
    status_codes = collections.Counter()
    endpoints = collections.Counter()
    total = 0
    first_ts = last_ts = None

    with open(path, 'r', encoding=detect_encoding(path), errors='replace') as f:
        for line in f:
            total += 1
            level, msg, ts_str = parse_line(line)
            # 级别统计
            norm = ''
            if level:
                norm = LEVEL_NORM.get(level.lower(), level.upper())
                level_count[norm] += 1
            is_error = norm in ERROR_LEVELS or norm == 'WARN'
            # 模式聚类（跳过堆栈帧/追踪行，避免噪声）
            if msg and not STACK_FRAME_RE.match(msg) and not msg.lstrip().startswith(('Traceback', 'File ')):
                pat = mask(msg, full=is_error)
                if pat:
                    patterns[pat] += 1
                    if pat not in pattern_samples:
                        pattern_samples[pat] = msg[:200]
                    if norm:
                        pattern_levels.setdefault(pat, collections.Counter())[norm] += 1
            # 时间桶
            dt = parse_ts(ts_str)
            if dt:
                if first_ts is None:
                    first_ts = dt
                last_ts = dt
                bucket = int(dt.timestamp()) // bucket_sec * bucket_sec
                time_buckets[bucket] += 1
            # 异常类型
            for exc in EXCEPTION_RE.findall(msg or line):
                exceptions[exc] += 1
            # IP / 状态码 / 接口
            for ip in IP_RE.findall(line):
                ips[ip] += 1
            for code in re.findall(r'\b(4\d\d|5\d\d)\b', line):
                if code in REAL_STATUS_CODES:
                    status_codes[code] += 1
            m = ACCESS_RE.search(line)
            if m and m.group(2) in REAL_STATUS_CODES and m.group(2)[0] in '45':
                path = m.group(1).split('?')[0]
                path = INT_RE.sub('<NUM>', path)
                endpoints[path] += 1

    return {
        'total': total,
        'level_count': level_count,
        'patterns': patterns,
        'samples': pattern_samples,
        'pattern_levels': pattern_levels,
        'time_buckets': time_buckets,
        'exceptions': exceptions,
        'ips': ips,
        'status_codes': status_codes,
        'endpoints': endpoints,
        'first_ts': first_ts,
        'last_ts': last_ts,
    }


def fmt_ts(dt):
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else 'N/A'


def print_report(r, path, top_n, bucket_sec):
    total = r['total']
    err_count = sum(v for k, v in r['level_count'].items() if k in ERROR_LEVELS)
    span = ''
    if r['first_ts'] and r['last_ts']:
        span = f"{fmt_ts(r['first_ts'])} ~ {fmt_ts(r['last_ts'])}"

    print('===== 日志异常聚合分析报告 =====')
    print(f'文件: {path}')
    print(f'总行数: {total}')
    print(f'时间范围: {span}')
    print(f'错误/异常级别行数: {err_count}（{err_count * 100 // max(total, 1)}%）')
    print()

    # 级别分布
    if r['level_count']:
        print('[日志级别分布]')
        for lv, n in r['level_count'].most_common():
            print(f'  {lv:<10} {n:>6}  {"█" * min(n // max(1, total // 40), 40)}')
        print()

    # 高频模式：异常优先（ERROR/WARN 排前），正常流量单独列
    def dominant_level(pat):
        lv = r.get('pattern_levels', {}).get(pat)
        return lv.most_common(1)[0][0] if lv else ''

    error_pats = [(p, n) for p, n in r['patterns'].most_common()
                  if dominant_level(p) in ERROR_LEVELS or dominant_level(p) == 'WARN']
    normal_pats = [(p, n) for p, n in r['patterns'].most_common()
                   if not (dominant_level(p) in ERROR_LEVELS or dominant_level(p) == 'WARN')]

    print(f'[高频异常模式 TOP {top_n}]（ERROR/WARN，变量已归一）')
    if error_pats:
        for pat, n in error_pats[:top_n]:
            pct = n * 100 // max(total, 1)
            sample = r['samples'].get(pat, '')
            pat_show = pat if len(pat) <= 90 else pat[:90] + '...'
            print(f'  ✦ {n:>6} 次（{pct}%）| {pat_show}')
            if sample and sample != pat[:90]:
                print(f'        样例: {sample[:120]}')
    else:
        print('  未发现 ERROR/WARN 模式')
    if normal_pats:
        print(f'\n[正常流量模式（INFO/DEBUG，前 5 条，非异常）]')
        for pat, n in normal_pats[:5]:
            pct = n * 100 // max(total, 1)
            pat_show = pat if len(pat) <= 90 else pat[:90] + '...'
            print(f'  · {n:>6} 次（{pct}%）| {pat_show}')
    print()

    # 时间突增检测（用中位数作基线，避免被尖峰本身拉高）
    if r['time_buckets'] and len(r['time_buckets']) >= 3:
        buckets = sorted(r['time_buckets'].items())
        counts = sorted(c for _, c in buckets)
        median = counts[len(counts) // 2]
        mx = counts[-1]
        print('[时间线突增检测]')
        print(f'  时间桶大小: {bucket_sec} 秒，共 {len(buckets)} 个桶，基线(中位数) {median} 条/桶，峰值 {mx} 条')
        hot = [b for b, c in buckets if c >= max(median * 2, 5)]
        if hot:
            print('  ⚠ 异常尖峰桶（≥ 2 倍基线）:')
            for b in hot:
                print(f'    {fmt_ts(datetime.fromtimestamp(b))}  {r["time_buckets"][b]} 条')
        else:
            print('  未检测到明显尖峰（错误分布均匀）')
        print()

    # 异常类型
    if r['exceptions']:
        print('[异常类型 TOP]')
        for exc, n in r['exceptions'].most_common(10):
            print(f'  {n:>6} 次  {exc}')
        print()

    # 来源排行
    if r['status_codes']:
        print('[高频 HTTP 错误状态码]')
        for code, n in r['status_codes'].most_common(8):
            print(f'  {code}  {n} 次')
        print()
    if r['endpoints']:
        print('[高频错误接口]')
        for ep, n in r['endpoints'].most_common(8):
            print(f'  {n:>6} 次  {ep}')
        print()
    if r['ips']:
        print('[高频来源 IP]')
        for ip, n in r['ips'].most_common(8):
            print(f'  {n:>6} 次  {ip}')
        print()

    print('===== 分析结束（深度根因分析请结合 error-catalog 由 AI 完成）=====')


def main():
    ap = argparse.ArgumentParser(description='日志异常聚合分析器')
    ap.add_argument('path', help='日志文件路径')
    ap.add_argument('--top', type=int, default=15, help='高频模式显示条数')
    ap.add_argument('--bucket', type=int, default=3600, help='时间桶大小（秒）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()

    try:
        r = analyze(args.path, args.top, args.bucket)
    except FileNotFoundError:
        print(f'[错误] 文件不存在: {args.path}')
        sys.exit(1)
    except IsADirectoryError:
        print(f'[错误] {args.path} 是目录，请指定具体日志文件')
        sys.exit(1)

    if args.json:
        out = {k: v for k, v in r.items() if k != 'samples'}
        out['patterns'] = dict(r['patterns'].most_common(args.top))
        out['time_buckets'] = {fmt_ts(datetime.fromtimestamp(k)): v for k, v in sorted(r['time_buckets'].items())}
        out['first_ts'] = fmt_ts(r['first_ts']) if r['first_ts'] else None
        out['last_ts'] = fmt_ts(r['last_ts']) if r['last_ts'] else None
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_report(r, args.path, args.top, args.bucket)


if __name__ == '__main__':
    main()
