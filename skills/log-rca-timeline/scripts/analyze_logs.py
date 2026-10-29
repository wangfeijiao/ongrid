#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""log-rca-timeline / 日志分析与故障根因定位 —— 日志 -> data.json

用法:
    python analyze_logs.py --input <文件或目录> [--glob "*.log"]
        [--window 5m] [--start "2026-09-28 10:00"] [--end "2026-09-28 12:00"]
        [--changes changes.txt] [--alert "2026-09-28 10:12"]
        [--assume-date 2026-09-28] [--top 5] [--out data.json]

处理链路:
    1. 格式识别: JSONL / CSV / 纯文本（自动）
    2. 时间戳解析: ISO8601 / nginx / syslog / Android / Unix epoch
    3. 时间窗口聚合: 总量、分级计数、错误率、P50/P95/P99、签名与服务分布
    4. 异常检测: 稳健基线（中位数 + MAD）+ 双阈 + EWMA 漂移 + 形态判据
    5. 异常模式识别: 14 类模式库，每条带证据与样本行
    6. 根因假设排序: 候选因库 x 证据分级 x 时序先后 x 集中度，输出 0-100 分
    7. 故障时间线: 潜伏 -> 触发 -> 放大 -> 缓解 -> 恢复 分阶段事件流

仅依赖标准库。输出 data.json 供 build_board.py 渲染。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob as globmod
import json
import math
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 常量

MONTH_ABBR = {}
for _i, _m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]):
    MONTH_ABBR[_m] = _i + 1
MONTH_ABBR["Sept"] = 9

P_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2}):(\d{2})"
                   r"(?:[.,](\d{1,6}))?\s*(Z|[+-]\d{2}:?\d{2})?")
P_NGINX = re.compile(r"(\d{1,2})/([A-Za-z]{3,4})/(\d{4}):(\d{2}):(\d{2}):(\d{2})")
P_ANDROID = re.compile(r"\b(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
P_SYSLOG = re.compile(r"\b([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\b")
P_EPOCH = re.compile(r"\b(1[5-9]\d{8}|1[6-9]\d{11})\b")
P_TIMEONLY = re.compile(r"\b(\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?\b")

LEVEL_MAP = {
    "TRACE": "TRACE", "DEBUG": "DEBUG", "INFO": "INFO", "NOTICE": "INFO",
    "WARN": "WARN", "WARNING": "WARN",
    "ERROR": "ERROR", "ERR": "ERROR", "SEVERE": "ERROR", "CRITICAL": "ERROR",
    "FATAL": "FATAL", "PANIC": "FATAL", "EMERG": "FATAL", "ALERT": "FATAL",
}
P_LEVEL = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|SEVERE|"
                     r"CRITICAL|FATAL|PANIC|EMERG|ALERT)\b")
LEVEL_RANK = {"TRACE": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "ERROR": 4, "FATAL": 5}

TIME_KEYS = ["ts", "timestamp", "time", "@timestamp", "datetime", "date", "logtime",
             "log_time", "occurtime", "occur_time", "eventtime", "event_time",
             "created_at", "asctime", "t"]
LEVEL_KEYS = ["level", "levelname", "severity", "loglevel", "log_level", "lvl", "priority"]
LAT_KEYS = ["rt", "latency", "duration", "duration_ms", "cost", "cost_ms", "elapsed",
            "took_ms", "time_ms", "response_time", "resp_time", "spend", "spend_ms",
            "responsetime", "elapsed_ms"]
SVC_KEYS = ["service", "svc", "app", "application", "module", "component", "logger",
            "source", "pod", "host", "hostname", "container", "appname"]
MSG_KEYS = ["msg", "message", "log", "content", "text", "body", "detail", "event"]

P_LAT_TEXT = re.compile(r"(?:rt|cost|took|spend|elapsed|dur|duration|latency|"
                        r"resp(?:onse)?[_\-]?time|time)[=:：\s]+([0-9]+(?:\.[0-9]+)?)"
                        r"\s*(ms|m?s|s|us|\u00b5s)?", re.I)
P_LAT_MS = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*ms\b", re.I)
P_SVC_BRACKET = re.compile(r"[\[\(]([A-Za-z0-9_.\-]{2,40})[\]\)]")
P_SVC_KV = re.compile(r"\b(?:svc|service|app|module|component)[=:]"
                      r"([A-Za-z0-9_.\-]{2,40})", re.I)
# 方括号内常见的是线程名而非服务名，需跳过
THREAD_TOKENS = set("""main thread pool worker executor scheduler async default
http-nio xnio nio selector dispatcher reactor eventloop task daemon catalina
localhost tid pid""".split())

RE_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
RE_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
RE_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
RE_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
RE_QUOTED = re.compile(r"\"[^\"]{0,80}\"|'[^']{0,80}'")
RE_PATH = re.compile(r"(/[\w.\-]+){2,}")

WINDOW_LADDER = [10, 30, 60, 300, 600, 900, 1800, 3600, 10800, 21600, 86400]

# ------------------------------------------------- 候选根因库（14 类）

CAUSES = [
    {
        "id": "dep-downstream",
        "name": "下游依赖故障（超时 / 拒绝连接 / 5xx）",
        "cat": "依赖故障",
        "prior": 0.72,
        "explains": ["error_burst", "latency_climb", "slow_p99", "traffic_drop",
                     "cascade", "retry_storm", "fatal_spike", "step_shift"],
        "kw": [r"timeout", r"timed out", r"socket ?timeout", r"read timed out",
               r"connection refused", r"connect(?:ion)? reset", r"no route to host",
               r"\b50[234]\b", r"upstream", r"bad gateway", r"service unavailable",
               r"gateway timeout", r"unavailable", r"circuit ?breaker", r"circuit ?open",
               r"feign", r"dubbo", r"rpc"],
        "anti": [],
        "verify": ["查下游服务同一时段的错误率与 P99 是否同步抬升",
                   "比对调用链（trace）：确认错误是否集中在某个下游接口",
                   "确认下游实例数、健康检查与注册中心状态"],
        "falsify": "下游指标全程平稳、且调用链无任何超时/5xx 记录时可排除",
        "fix": ["为下游调用设置更短的超时与熔断阈值", "补充降级与缓存兜底", "推动下游扩容或限流"],
    },
    {
        "id": "db-slow-lock",
        "name": "数据库慢查询 / 锁等待 / 连接池外的 DB 阻塞",
        "cat": "数据层",
        "prior": 0.68,
        "explains": ["latency_climb", "slow_p99", "error_burst", "step_shift",
                     "concentrate_service", "retry_storm"],
        "kw": [r"slow query", r"slowquery", r"lock wait", r"lockwait", r"deadlock",
               r"dead lock", r"\bdeadlockfound\b", r"lock timeout", r"row lock",
               r"waiting for.*lock", r"too many connections", r"max_connections",
               r"sql", r"jdbc", r"mysql", r"postgres", r"oracle", r"ora-\d",
               r"dbcp", r"hikari", r"druid", r"cannotgetjdbcconnection",
               r"transaction", r"rollback"],
        "anti": [],
        "verify": ["拉取该时段慢查询日志 TOP 20 与执行计划",
                   "查 information_schema / pg_stat_activity 中的锁等待与长事务",
                   "确认是否有未提交事务、DDL 或大批量作业"],
        "falsify": "慢查询条数为 0、且无锁等待记录时可排除",
        "fix": ["补索引 / 改写 SQL / 加缓存", "拆分大事务、缩短持锁时间", "设置语句超时"],
    },
    {
        "id": "pool-exhausted",
        "name": "连接池 / 线程池耗尽",
        "cat": "资源池",
        "prior": 0.62,
        "explains": ["latency_climb", "slow_p99", "error_burst", "traffic_drop",
                     "step_shift", "saturation"],
        "kw": [r"pool", r"pool exhausted", r"pool timeout", r"connection pool",
               r"thread pool", r"threadpool", r"rejectedexecution",
               r"too many connections", r"acquire.*timeout", r"getconnection",
               r"maxactive", r"maxwait", r"queue full", r"queue capacity",
               r"no available", r"exhausted"],
        "anti": [],
        "verify": ["看池的 active / idle / waitCount 指标曲线",
                   "确认获取连接的等待耗时是否先于错误率抬升",
                   "排查是否存在连接未归还（泄漏）"],
        "falsify": "池指标始终有空闲、等待数为 0 时可排除",
        "fix": ["调大池上限并设合理获取超时", "修复连接泄漏", "按依赖拆分独立池"],
    },
    {
        "id": "gc-oom",
        "name": "内存溢出 / GC 停顿 / 堆外内存",
        "cat": "资源",
        "prior": 0.6,
        "explains": ["latency_climb", "slow_p99", "fatal_spike", "traffic_drop",
                     "step_shift", "saturation", "error_burst"],
        "kw": [r"outofmemory", r"out of memory", r"oom", r"heap space", r"metaspace",
               r"gc overhead", r"full ?gc", r"gc pause", r"garbagecollector",
               r"direct buffer", r"cannot allocate memory", r"memory leak",
               r"killed.*(?:oom|memory)", r"cgroup", r"oomkilled"],
        "anti": [],
        "verify": ["看堆内存与 Full GC 次数/耗时的时序曲线",
                   "抓取 heap dump 或分析已有 dump 的大对象持有链",
                   "确认容器内存 limit 与是否被 OOMKilled"],
        "falsify": "GC 次数与耗时平稳、堆水位无上升趋势时可排除",
        "fix": ["定位大对象 / 缓存无界增长", "调整堆与 GC 参数", "修复泄漏后灰度观察"],
    },
    {
        "id": "cpu-saturation",
        "name": "CPU / 负载打满（含线程争用）",
        "cat": "资源",
        "prior": 0.55,
        "explains": ["latency_climb", "slow_p99", "traffic_drop", "step_shift",
                     "saturation", "periodic_glitch"],
        "kw": [r"cpu", r"load average", r"load is high", r"system overload",
               r"thread starvation", r"context switch", r"busy", r"overload",
               r"100%", r"high load", r"blocked thread", r"stuck"],
        "anti": [],
        "verify": ["看 CPU 使用率与 load average，确认是否 user 高还是 sys 高",
                   "抓火焰图 / thread dump 定位热点方法",
                   "确认是否有大批量定时任务与业务高峰重叠"],
        "falsify": "CPU 与 load 均在常态区间内可排除",
        "fix": ["优化热点路径 / 降频轮询", "扩容或错峰调度", "限制单请求资源消耗"],
    },
    {
        "id": "disk-io",
        "name": "磁盘写满 / IO 阻塞",
        "cat": "资源",
        "prior": 0.5,
        "explains": ["error_burst", "fatal_spike", "traffic_drop", "step_shift",
                     "silent_gap"],
        "kw": [r"no space left", r"disk full", r"diskfull", r"read-only file system",
               r"io error", r"i/o error", r"ioexception", r"await", r"util.*100",
               r"write failed", r"quota exceeded", r"inode"],
        "anti": [],
        "verify": ["df -h 与 inode 使用率", "看磁盘 await / util 是否打满",
                   "确认日志轮转与清理策略是否失效"],
        "falsify": "磁盘余量充足且 await 正常可排除",
        "fix": ["清理并修复轮转策略", "分离日志盘与数据盘", "扩容或降写入"],
    },
    {
        "id": "network-dns-cert",
        "name": "网络抖动 / DNS 解析失败 / 证书过期",
        "cat": "基础设施",
        "prior": 0.48,
        "explains": ["error_burst", "latency_climb", "traffic_drop", "cascade",
                     "fatal_spike"],
        "kw": [r"unknownhost", r"dns", r"name resolution", r"resolve",
               r"certificate", r"ssl", r"tls", r"handshake", r"expired",
               r"network is unreachable", r"no route to host", r"packet loss",
               r"connection reset", r"broken pipe", r"eof exception"],
        "anti": [],
        "verify": ["确认故障时段内网/专线质量与丢包率",
                   "查 DNS 解析耗时与失败率、证书有效期",
                   "核对网络设备与安全组变更记录"],
        "falsify": "网络质量与 DNS 解析均无异常记录可排除",
        "fix": ["配置 DNS 缓存与多 resolver", "证书到期前自动续期告警", "设置重试与超时"],
    },
    {
        "id": "recent-change",
        "name": "近期发布 / 配置变更引入缺陷",
        "cat": "变更",
        "prior": 0.66,
        "explains": ["new_error_pattern", "error_burst", "fatal_spike", "step_shift",
                     "latency_climb", "concentrate_service"],
        "kw": [r"nullpointer", r"npe", r"indexoutofbounds", r"classcast",
               r"nosuchmethod", r"nosuchfield", r"illegalargument",
               r"illegalstate", r"unsupportedoperation", r"serialization",
               r"deserialize", r"parse error", r"parseexception",
               r"json", r"numberformat", r"classnotfound", r"beancreation",
               r"configuration", r"property", r"config", r"schema", r"migration"],
        "anti": [],
        "verify": ["对齐发布/配置变更时间与异常起始时间（是否在 30 分钟内）",
                   "比对灰度批次：是否仅新版本实例报错",
                   "回滚或切回上一版本验证"],
        "falsify": "异常起始早于最近一次变更、或老版本实例同样报错时可排除",
        "fix": ["回滚或关闭开关", "补齐灰度与回归用例", "变更前后加基线对比"],
    },
    {
        "id": "traffic-surge",
        "name": "外部流量突增（活动 / 爬虫 / 压测 / 回放）",
        "cat": "外部流量",
        "prior": 0.58,
        "explains": ["traffic_spike", "latency_climb", "error_burst", "slow_p99",
                     "step_shift", "saturation"],
        "kw": [r"rate limit", r"ratelimit", r"throttl", r"429", r"too many requests",
               r"qps", r"spider", r"crawler", r"bot", r"stress", r"benchmark",
               r"batch", r"sync task", r"import", r"export"],
        "anti": [],
        "verify": ["看入口 QPS 与来源 IP/UA 分布是否异常",
                   "确认是否有活动、压测、回放或上游重试放大",
                   "核对限流阈值与实际拒绝量"],
        "falsify": "入口 QPS 平稳、无异常来源可排除",
        "fix": ["按来源限流与配额", "扩容并预热", "错峰执行批量任务"],
    },
    {
        "id": "cache-avalanche",
        "name": "缓存击穿 / 雪崩 / 热点 key",
        "cat": "缓存",
        "prior": 0.52,
        "explains": ["latency_climb", "error_burst", "traffic_spike", "step_shift",
                     "slow_p99", "concentrate_service"],
        "kw": [r"redis", r"cache", r"memcached", r"hot ?key", r"big ?key",
               r"cache miss", r"penetration", r"breakdown", r"mget", r"jedis",
               r"lettuce", r"connection reset by peer", r"moved", r"cluster down",
               r"readonly", r"wrap", r"evict"],
        "anti": [],
        "verify": ["看缓存命中率曲线是否在故障前骤降",
                   "查大 key / 热 key 与 Redis 慢日志",
                   "确认是否存在批量 key 同时过期"],
        "falsify": "命中率平稳、无慢日志与热 key 可排除",
        "fix": ["过期时间加随机抖动", "空值缓存 + 布隆过滤器", "热 key 拆分与本地缓存"],
    },
    {
        "id": "mq-backlog",
        "name": "MQ 堆积 / 消费失败重试风暴",
        "cat": "消息",
        "prior": 0.5,
        "explains": ["retry_storm", "error_burst", "step_shift", "concentrate_service",
                     "latency_climb", "periodic_glitch"],
        "kw": [r"kafka", r"rocketmq", r"rabbitmq", r"pulsar", r"mq", r"consumer",
               r"consume", r"lag", r"backlog", r"accumulat", r"rebalance",
               r"offset", r"retries", r"retry", r"redeliver", r"dlq",
               r"dead ?letter", r"reconsume", r"maxreconsume"],
        "anti": [],
        "verify": ["看消费 lag 曲线与消费者实例数",
                   "确认是否触发 rebalance 或消费者掉线",
                   "查死信队列与重复投递次数"],
        "falsify": "消费 lag 平稳、无重复投递可排除",
        "fix": ["扩容消费者 / 提高并行度", "设置最大重试与死信", "幂等消费"],
    },
    {
        "id": "auth-quota",
        "name": "限流 / 配额 / 鉴权失效（429、401、403）",
        "cat": "治理",
        "prior": 0.46,
        "explains": ["error_burst", "traffic_drop", "concentrate_service",
                     "new_error_pattern"],
        "kw": [r"\b429\b", r"\b401\b", r"\b403\b", r"unauthorized", r"forbidden",
               r"token", r"expired", r"signature", r"ak/sk", r"accesskey",
               r"permission", r"quota", r"rate limit", r"throttl", r"appkey",
               r"invalid credential", r"auth"],
        "anti": [],
        "verify": ["核对调用方凭证与授权是否变更或过期",
                   "看限流命中量与配额使用情况",
                   "确认网关策略是否调整"],
        "falsify": "无 401/403/429 且鉴权链路正常可排除",
        "fix": ["修复凭证轮转机制", "配额告警与提前申请", "区分业务错误与限流错误"],
    },
    {
        "id": "code-defect",
        "name": "代码缺陷（空指针 / 越界 / 并发 / 序列化）",
        "cat": "代码",
        "prior": 0.44,
        "explains": ["new_error_pattern", "fatal_spike", "error_burst",
                     "concentrate_service", "periodic_glitch"],
        "kw": [r"nullpointer", r"npe", r"indexoutofbounds", r"classcastexception",
               r"concurrentmodification", r"illegalstate", r"stackoverflow",
               r"numberformat", r"divide by zero", r"assertion", r"exception in thread",
               r"uncaught", r"unhandled"],
        "anti": [],
        "verify": ["取完整堆栈定位到具体类与行号",
                   "确认是否仅在特定入参/分支复现",
                   "用相同输入在预发环境复现"],
        "falsify": "能找到外部依赖或资源侧的先行异常时可降权",
        "fix": ["修复缺陷并补单测", "加参数校验与兜底", "灰度发布观察"],
    },
    {
        "id": "observability-gap",
        "name": "日志采集侧异常（断流但业务指标正常）",
        "cat": "可观测性",
        "prior": 0.35,
        "explains": ["silent_gap", "traffic_drop"],
        "kw": [r"agent", r"collector", r"fluent", r"filebeat", r"logstash",
               r"lost", r"disconnect", r"buffer overflow", r"drop", r"rotation",
               r"truncat", r"cannot write", r"permission denied"],
        "anti": [],
        "verify": ["对比业务成功率/QPS 指标是否同步下降",
                   "确认采集 agent 存活、磁盘与队列水位",
                   "看其他实例/其他Topic是否同样断流"],
        "falsify": "业务指标同步恶化时排除（那是真故障）",
        "fix": ["恢复采集链路与缓冲", "补齐采集侧自身监控", "多通道冗余上报"],
    },
]

PATTERN_META = {
    "traffic_spike": ("流量突增", "高"),
    "traffic_drop": ("流量断崖 / 断流", "高"),
    "error_burst": ("错误率飙升", "高"),
    "fatal_spike": ("致命错误突现", "高"),
    "latency_climb": ("延迟抬升", "中"),
    "slow_p99": ("P99 长尾恶化", "中"),
    "new_error_pattern": ("新错误签名涌现", "中"),
    "retry_storm": ("重试风暴", "高"),
    "concentrate_service": ("单服务集中", "中"),
    "periodic_glitch": ("周期性毛刺", "低"),
    "step_shift": ("阶跃 / 持续漂移", "中"),
    "cascade": ("级联传播", "高"),
    "silent_gap": ("日志静默", "中"),
    "saturation": ("饱和度线索", "中"),
}
SEV_RANK = {"高": 3, "中": 2, "低": 1}
PATTERN_UNIT = {
    "traffic_spike": "条/窗", "traffic_drop": "条/窗", "error_burst": "条/窗",
    "fatal_spike": "条/窗", "latency_climb": "ms", "slow_p99": "ms",
    "new_error_pattern": "个新签名", "retry_storm": "次/窗", "concentrate_service": "%",
    "periodic_glitch": "个窗口", "step_shift": "倍", "cascade": "个服务",
    "silent_gap": "条/窗", "saturation": "条线索",
}

METHOD_SOURCES = [
    {"name": "Google SRE · 事故复盘文化（Postmortem Culture）", "kind": "方法论",
     "url": "https://sre.google/sre-book/postmortem-culture/"},
    {"name": "Google SRE · 分布式系统监控四黄金指标", "kind": "方法论",
     "url": "https://sre.google/sre-book/monitoring-distributed-systems/"},
    {"name": "Google SRE Workbook · 事故响应", "kind": "方法论",
     "url": "https://sre.google/workbook/incident-response/"},
    {"name": "Brendan Gregg · USE 方法（使用率/饱和度/错误）", "kind": "方法论",
     "url": "https://www.brendangregg.com/usemethod.html"},
]

# ---------------------------------------------------------------- 工具


def log(msg):
    print(msg)


def parse_window(s):
    m = re.match(r"^\s*(\d+)\s*([smhd]?)\s*$", str(s or "").strip(), re.I)
    if not m:
        return None
    mult = {"": 60, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2).lower()]
    return int(m.group(1)) * mult


def win_label(sec):
    if sec % 86400 == 0:
        return "%dd" % (sec // 86400)
    if sec % 3600 == 0:
        return "%dh" % (sec // 3600)
    if sec % 60 == 0:
        return "%dm" % (sec // 60)
    return "%ds" % sec


def parse_user_time(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def median(xs):
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    return float(ys[mid]) if n % 2 else (ys[mid - 1] + ys[mid]) / 2.0


def mad_of(xs, med=None):
    if not xs:
        return 0.0
    m = median(xs) if med is None else med
    return median([abs(x - m) for x in xs])


def robust_z(x, med, mad_v):
    """MAD 稳健 z；MAD=0 时退化为相对偏离量"""
    if mad_v > 1e-9:
        return (x - med) / (1.4826 * mad_v)
    if med > 1e-9:
        return 0.0 if abs(x - med) < 1e-9 else math.copysign(6.0, x - med)
    return 6.0 if x > 0 else 0.0


def pct(vals, p):
    if not vals:
        return None
    ys = sorted(vals)
    k = (len(ys) - 1) * p
    f, c = int(math.floor(k)), int(math.ceil(k))
    if f == c:
        return float(ys[f])
    return float(ys[f]) + (ys[c] - ys[f]) * (k - f)


def clamp01(x):
    return max(0.0, min(1.0, x))


def fmt_ts(epoch, with_sec=True):
    d = dt.datetime.fromtimestamp(epoch)
    return d.strftime("%Y-%m-%d %H:%M:%S" if with_sec else "%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 解析


def parse_ts_from_text(text, assume_date=None, ref_year=None):
    """从一行文本抽取时间戳，返回 (epoch_float, 是否含时区) 或 (None, False)"""
    m = P_ISO.search(text)
    if m:
        year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm, ss = int(m.group(4)), int(m.group(5)), int(m.group(6))
        frac = m.group(7) or "0"
        micro = int((frac + "000000")[:6])
        try:
            base = dt.datetime(year, mon, day, hh, mm, ss, micro).timestamp()
        except ValueError:
            return None, False
        return base, bool(m.group(8))

    m = P_NGINX.search(text)
    if m:
        mon = MONTH_ABBR.get(m.group(2).title())
        if mon:
            try:
                return dt.datetime(int(m.group(3)), mon, int(m.group(1)),
                                   int(m.group(4)), int(m.group(5)),
                                   int(m.group(6))).timestamp(), False
            except ValueError:
                return None, False

    m = P_ANDROID.search(text)
    if m and (assume_date or ref_year):
        year = assume_date.year if assume_date else ref_year
        try:
            return dt.datetime(year, int(m.group(1)), int(m.group(2)),
                               int(m.group(3)), int(m.group(4)), int(m.group(5)),
                               int(m.group(6)) * 1000).timestamp(), False
        except ValueError:
            return None, False

    m = P_SYSLOG.search(text)
    if m and (assume_date or ref_year):
        mon = MONTH_ABBR.get(m.group(1).title())
        year = assume_date.year if assume_date else ref_year
        if mon and year:
            try:
                return dt.datetime(year, mon, int(m.group(2)), int(m.group(3)),
                                   int(m.group(4)), int(m.group(5))).timestamp(), False
            except ValueError:
                return None, False

    m = P_EPOCH.search(text)
    if m:
        v = int(m.group(1))
        return (v / 1000.0 if v > 10 ** 11 else float(v)), False

    if assume_date is not None:
        m = P_TIMEONLY.search(text)
        if m:
            try:
                return dt.datetime(assume_date.year, assume_date.month, assume_date.day,
                                   int(m.group(1)), int(m.group(2)),
                                   int(m.group(3))).timestamp(), False
            except ValueError:
                return None, False
    return None, False


def extract_service(line):
    """从文本行抽取服务/模块名：优先 kv 形式，其次跳过级别与线程名的方括号"""
    m = P_SVC_KV.search(line or "")
    if m:
        return m.group(1)
    for m in P_SVC_BRACKET.finditer(line or ""):
        v = m.group(1)
        if v.upper() in LEVEL_MAP or v.lower() in THREAD_TOKENS:
            continue
        if re.match(r"^\d", v):
            continue
        if re.match(r"^(?:http|nio|xnio|pool|thread|task|worker)[-_\d]", v, re.I):
            continue
        return v
    return None


def norm_signature(msg):
    s = msg or ""
    s = RE_UUID.sub("<uuid>", s)
    s = RE_HEX.sub("<hex>", s)
    s = RE_IP.sub("<ip>", s)
    s = RE_QUOTED.sub("<str>", s)
    s = RE_NUM.sub("<n>", s)
    s = RE_PATH.sub("<path>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:180]


def extract_latency_ms(text, obj=None):
    if obj:
        for k in LAT_KEYS:
            v = obj.get(k)
            if isinstance(v, (int, float)):
                return float(v) if v < 600000 else float(v) / 1000.0
            if isinstance(v, str):
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)", v)
                if m:
                    return float(m.group(1))
    m = P_LAT_TEXT.search(text or "")
    if m:
        v = float(m.group(1))
        u = (m.group(2) or "ms").lower()
        if u in ("us", "\u00b5s"):
            return v / 1000.0
        if u == "s":
            return v * 1000.0
        return v
    m = P_LAT_MS.search(text or "")
    if m:
        return float(m.group(1))
    return None


def pick(obj, keys):
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    low = {str(k).lower(): v for k, v in obj.items()}
    for k in keys:
        if k in low and low[k] not in (None, ""):
            return low[k]
    return None


def detect_format(path, sample=120):
    """返回 jsonl / csv / text"""
    n_json, n_lines, first = 0, 0, ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if not first:
                    first = line
                n_lines += 1
                if line.startswith("{"):
                    try:
                        json.loads(line)
                        n_json += 1
                    except Exception:
                        pass
                if n_lines >= sample:
                    break
    except Exception:
        return "text"
    if n_lines == 0:
        return "text"
    if n_json >= max(1, int(n_lines * 0.8)):
        return "jsonl"
    if "," in first and not P_ISO.search(first[:40]) and first.count(",") >= 2:
        head = first.split(",")
        if any(re.search(r"time|date|ts|level|msg|message", h, re.I) for h in head):
            return "csv"
    return "text"


def load_records(paths, assume_date, ref_year, max_lines=2000000):
    """解析全部文件，返回 (records, stats)"""
    records = []
    stats = {"files": 0, "lines": 0, "parsed": 0, "formats": {}, "tz_hint": False}
    for p in paths:
        fmt = detect_format(p)
        stats["formats"][fmt] = stats["formats"].get(fmt, 0) + 1
        stats["files"] += 1
        header = None
        try:
            fh = open(p, "r", encoding="utf-8", errors="replace")
        except Exception as e:
            log("[WARN] 打开失败，跳过: %s (%s)" % (p, e))
            continue
        with fh:
            for raw in fh:
                if len(records) >= max_lines:
                    break
                line = raw.rstrip("\n").rstrip("\r")
                if not line.strip():
                    continue
                stats["lines"] += 1
                ts, level, lat, svc, msg = None, None, None, None, line
                obj = None
                if fmt == "jsonl":
                    try:
                        obj = json.loads(line)
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        tv = pick(obj, TIME_KEYS)
                        if isinstance(tv, (int, float)):
                            ts = float(tv) / 1000.0 if tv > 10 ** 11 else float(tv)
                        else:
                            ts, tz = parse_ts_from_text(str(tv), assume_date, ref_year)
                            stats["tz_hint"] = stats["tz_hint"] or tz
                        lv = pick(obj, LEVEL_KEYS)
                        level = LEVEL_MAP.get(str(lv).upper().strip()) if lv else None
                        lat = extract_latency_ms(line, obj)
                        sv = pick(obj, SVC_KEYS)
                        svc = str(sv)[:40] if sv else None
                        mv = pick(obj, MSG_KEYS)
                        msg = str(mv) if mv else line
                elif fmt == "csv":
                    parts = next(csv_rows([line]))
                    if header is None:
                        header = parts
                        continue
                    row = dict(zip(header, parts)) if len(parts) == len(header) else {}
                    if not row:
                        row = {("c%d" % i): v for i, v in enumerate(parts)}
                    tv = pick(row, TIME_KEYS)
                    ts, tz = parse_ts_from_text(str(tv or ""), assume_date, ref_year)
                    stats["tz_hint"] = stats["tz_hint"] or tz
                    lv = pick(row, LEVEL_KEYS)
                    level = LEVEL_MAP.get(str(lv).upper().strip()) if lv else None
                    lat = extract_latency_ms(line, None)
                    sv = pick(row, SVC_KEYS)
                    svc = str(sv)[:40] if sv else None
                    mv = pick(row, MSG_KEYS)
                    msg = str(mv) if mv else line
                else:
                    ts, tz = parse_ts_from_text(line, assume_date, ref_year)
                    stats["tz_hint"] = stats["tz_hint"] or tz
                    m = P_LEVEL.search(line)
                    level = LEVEL_MAP.get(m.group(1).upper()) if m else None
                    lat = extract_latency_ms(line, None)
                    svc = extract_service(line)
                if ts is None:
                    continue
                if level is None:
                    level = "INFO"
                    low = msg.lower()
                    if re.search(r"\b(error|exception|fail|failed|fatal|panic)\b", low):
                        level = "ERROR"
                    elif re.search(r"\b(warn|warning|timeout|retry|slow)\b", low):
                        level = "WARN"
                records.append({
                    "t": float(ts), "level": level, "lat": lat,
                    "svc": svc or "-", "msg": msg[:600], "sig": norm_signature(msg),
                    "raw": line[:400],
                })
                stats["parsed"] += 1
    return records, stats


def csv_rows(lines):
    import csv as _csv
    return _csv.reader(lines)


# ---------------------------------------------------------------- 聚合


def build_buckets(records, size, t_start=None, t_end=None):
    if not records:
        return []
    ts_all = [r["t"] for r in records]
    lo = t_start if t_start is not None else min(ts_all)
    hi = t_end if t_end is not None else max(ts_all)
    lo = math.floor(lo / size) * size
    hi = math.floor(hi / size) * size
    nb = int((hi - lo) / size) + 1
    buckets = []
    idx = {}
    for i in range(nb):
        b = {"t": lo + i * size, "total": 0, "TRACE": 0, "DEBUG": 0, "INFO": 0,
             "WARN": 0, "ERROR": 0, "FATAL": 0, "lats": [], "sigs": {},
             "svcs": {}, "svc_err": {}, "lines": []}
        buckets.append(b)
        idx[b["t"]] = b
    for r in records:
        key = math.floor(r["t"] / size) * size
        if key < lo or key > hi:
            continue
        b = idx.get(key)
        if b is None:
            continue
        b["total"] += 1
        b[r["level"]] = b.get(r["level"], 0) + 1
        if r["lat"] is not None and len(b["lats"]) < 4000:
            b["lats"].append(r["lat"])
        b["sigs"][r["sig"]] = b["sigs"].get(r["sig"], 0) + 1
        b["svcs"][r["svc"]] = b["svcs"].get(r["svc"], 0) + 1
        if r["level"] in ("ERROR", "FATAL"):
            b["svc_err"][r["svc"]] = b["svc_err"].get(r["svc"], 0) + 1
        if len(b["lines"]) < 12:
            b["lines"].append(r["raw"])
    for b in buckets:
        errs = b["ERROR"] + b["FATAL"]
        b["errors"] = errs
        b["error_rate"] = (errs / b["total"]) if b["total"] else 0.0
        b["p50"] = pct(b["lats"], 0.50)
        b["p95"] = pct(b["lats"], 0.95)
        b["p99"] = pct(b["lats"], 0.99)
        top_sig, top_sig_n = max(b["sigs"].items(), key=lambda kv: kv[1]) if b["sigs"] else (None, 0)
        b["top_sig"] = top_sig
        b["top_sig_share"] = (top_sig_n / b["total"]) if b["total"] else 0.0
        top_svc, top_svc_n = max(b["svcs"].items(), key=lambda kv: kv[1]) if b["svcs"] else (None, 0)
        b["top_svc"] = top_svc
        b["top_svc_share"] = (top_svc_n / b["total"]) if b["total"] else 0.0
        b["lats"] = None
    return buckets


def two_pass_z(buckets, key, min_pts=4):
    vals = [b[key] for b in buckets if b[key] is not None]
    if len(vals) < min_pts:
        return None
    med = median(vals)
    mad_v = mad_of(vals, med)
    keep = [v for v in vals if abs(robust_z(v, med, mad_v)) < 3.0] or vals
    med2 = median(keep)
    mad2 = mad_of(keep, med2)
    if mad2 <= 1e-9:
        mad2 = max(1e-9, med2 * 0.1)
    out = []
    for b in buckets:
        v = b[key]
        out.append(None if v is None else robust_z(v, med2, mad2))
    return {"baseline": med2, "mad": mad2, "z": out}


# ---------------------------------------------------------------- 模式识别


def detect_patterns(buckets, size):
    patterns = []
    n = len(buckets)
    if n == 0:
        return patterns

    totals = [b["total"] for b in buckets]
    errs = [b["errors"] for b in buckets]
    rates = [b["error_rate"] for b in buckets]
    p95s = [b["p95"] for b in buckets if b["p95"] is not None]
    fatals = [b["FATAL"] for b in buckets]

    zt = two_pass_z(buckets, "total")
    ze = two_pass_z(buckets, "errors")
    zr = two_pass_z(buckets, "error_rate")
    zp = two_pass_z(buckets, "p95")

    for i, b in enumerate(buckets):
        b["z_total"] = zt["z"][i] if zt else None
        b["z_errors"] = ze["z"][i] if ze else None
        b["z_rate"] = zr["z"][i] if zr else None
        b["z_p95"] = zp["z"][i] if zp else None

    base_total = zt["baseline"] if zt else median(totals)
    base_err = ze["baseline"] if ze else median(errs)
    base_rate = zr["baseline"] if zr else median(rates)
    base_p95 = zp["baseline"] if zp else (median(p95s) if p95s else None)

    def mk(ptype, i, sev, peak, baseline, ratio, z, why, extra=None):
        lo, hi = i, min(n - 1, i)
        p = {
            "id": "%s-%d" % (ptype, i),
            "type": ptype,
            "name": PATTERN_META[ptype][0],
            "severity": sev,
            "sev": SEV_RANK[sev],
            "window_start": buckets[lo]["t"],
            "window_end": buckets[hi]["t"] + size,
            "t_label": fmt_ts(buckets[i]["t"]),
            "peak_label": fmt_ts(buckets[i]["t"]),
            "peak_value": round(float(peak), 3),
            "baseline": round(float(baseline), 3) if baseline is not None else None,
            "ratio": round(float(ratio), 2) if ratio else None,
            "z": round(float(z), 2) if z is not None else None,
            "unit": PATTERN_UNIT.get(ptype, ""),
            "why": why,
            "samples": buckets[i]["lines"][:3],
        }
        if extra:
            p.update(extra)
        return p

    # 1) 流量突增 / 断崖
    if zt:
        for i, b in enumerate(buckets):
            z = zt["z"][i] or 0
            if z >= 3.5 and b["total"] >= max(5, base_total * 2):
                patterns.append(mk("traffic_spike", i, "高", b["total"], base_total,
                                   (b["total"] / base_total) if base_total else None, z,
                                   "窗口内日志量为基线 %.1f 倍，需先区分真实流量放大与"
                                   "重试/循环打印造成的日志放大。"
                                   % ((b["total"] / base_total) if base_total else 0),
                                   {"service": b["top_svc"]}))
            elif base_total >= 5 and b["total"] <= max(0.25 * base_total, 1) and z <= -3.0:
                patterns.append(mk("traffic_drop", i, "高", b["total"], base_total,
                                   (b["total"] / base_total) if base_total else 0, z,
                                   "日志量跌至基线的 %.0f%%，须同时核对业务 QPS："
                                   "若业务指标同步下降则为真实故障，若不变则疑似采集侧断流。"
                                   % (100 * (b["total"] / base_total) if base_total else 0),
                                   {"service": b["top_svc"]}))

    # 2) 错误率飙升 / 致命错误突现
    if ze:
        for i, b in enumerate(buckets):
            z = ze["z"][i] or 0
            if z >= 3.5 and b["errors"] >= max(5, base_err * 3):
                rate = b["error_rate"]
                patterns.append(mk("error_burst", i, "高", b["errors"], base_err,
                                   (b["errors"] / base_err) if base_err else None, z,
                                   "错误条数为基线 %.1f 倍，窗口错误率 %.1f%%（基线 %.1f%%）。"
                                   % ((b["errors"] / base_err) if base_err else 0,
                                      rate * 100, base_rate * 100),
                                   {"service": b["top_svc"], "top_sig": b["top_sig"],
                                    "rate": round(rate, 4)}))
    if any(fatals):
        med_f = median(fatals)
        for i, b in enumerate(buckets):
            if b["FATAL"] >= 3 and b["FATAL"] > max(med_f * 3, 2):
                patterns.append(mk("fatal_spike", i, "高", b["FATAL"], med_f,
                                   (b["FATAL"] / med_f) if med_f else None,
                                   robust_z(b["FATAL"], med_f, mad_of(fatals, med_f)),
                                   "FATAL 级日志突现，通常直接对应进程退出、OOM 或不可恢复异常。",
                                   {"service": b["top_svc"], "top_sig": b["top_sig"]}))

    # 3) 延迟抬升 / P99 长尾
    if zp:
        for i, b in enumerate(buckets):
            if b["p95"] is None:
                continue
            z = zp["z"][i] or 0
            if z >= 3.5 and base_p95 and b["p95"] >= max(base_p95 * 2, base_p95 + 100):
                patterns.append(mk("latency_climb", i, "中", b["p95"], base_p95,
                                   b["p95"] / base_p95 if base_p95 else None, z,
                                   "P95 由基线 %.0fms 抬升至 %.0fms（%.1f 倍），"
                                   "先确认是全局抬升还是仅个别接口。"
                                   % (base_p95, b["p95"], b["p95"] / base_p95),
                                   {"service": b["top_svc"]}))
            elif b["p99"] and b["p95"] and b["p95"] > 0 and b["p99"] >= b["p95"] * 3:
                patterns.append(mk("slow_p99", i, "中", b["p99"], b["p95"],
                                   b["p99"] / b["p95"], z,
                                   "P99 达 %.0fms，为同窗口 P95 的 %.1f 倍，长尾显著，"
                                   "常见于少量慢请求、GC 停顿或个别下游抖动。" % (b["p99"], b["p99"] / b["p95"]),
                                   {"service": b["top_svc"]}))

    # 4) 新错误签名涌现（相对前序窗口）
    seen = set()
    for i, b in enumerate(buckets):
        err_sigs = {s: c for s, c in b["sigs"].items()
                    if re.search(r"error|exception|fail|fatal|timeout|refused|denied", s, re.I)}
        new = {s: c for s, c in err_sigs.items() if s not in seen}
        if err_sigs and len(new) >= 2 and len(new) / max(1, len(err_sigs)) >= 0.5 and i >= 2:
            top = max(new.items(), key=lambda kv: kv[1])[0] if new else None
            patterns.append(mk("new_error_pattern", i, "中", len(new), 0, None, None,
                               "本窗口出现 %d 个此前未出现的错误签名（占错误签名 %.0f%%），"
                               "高度提示新代码路径或新依赖被触发。" % (len(new), 100 * len(new) / len(err_sigs)),
                               {"service": b["top_svc"], "top_sig": top}))
        seen |= set(err_sigs.keys())

    # 5) 重试风暴：同一签名单窗口高频且环比放大
    for i in range(1, n):
        b, prev = buckets[i], buckets[i - 1]
        if not b["top_sig"]:
            continue
        cur = b["sigs"].get(b["top_sig"], 0)
        was = prev["sigs"].get(b["top_sig"], 0)
        if cur >= 50 and cur >= max(5 * was, 30):
            patterns.append(mk("retry_storm", i, "高", cur, was,
                               (cur / was) if was else None, None,
                               "同一日志签名在本窗口出现 %d 次（上一窗口 %d 次），"
                               "典型为重试循环、补偿任务或消息重复投递。" % (cur, was),
                               {"service": b["top_svc"], "top_sig": b["top_sig"]}))

    # 6) 单服务集中
    for i, b in enumerate(buckets):
        if b["errors"] >= 5 and b["top_svc"] and b["top_svc"] != "-":
            share = b["svc_err"].get(b["top_svc"], 0) / max(1, b["errors"])
            if share >= 0.7:
                patterns.append(mk("concentrate_service", i, "中", round(share * 100, 1), None, None, None,
                                   "%.0f%% 的错误集中在服务「%s」，定位范围可收敛到单一组件。"
                                   % (share * 100, b["top_svc"]),
                                   {"service": b["top_svc"]}))

    # 7) 周期性毛刺
    anom_idx = sorted({i for p in patterns for i in [int(p["id"].rsplit("-", 1)[1])]
                       if p["type"] in ("traffic_spike", "error_burst")})
    if len(anom_idx) >= 3:
        gaps = [anom_idx[k + 1] - anom_idx[k] for k in range(len(anom_idx) - 1)]
        gm = sum(gaps) / len(gaps)
        var = sum((g - gm) ** 2 for g in gaps) / len(gaps)
        cv = math.sqrt(var) / gm if gm else 9
        if cv < 0.35 and gm >= 2:
            patterns.append({
                "id": "periodic_glitch-0", "type": "periodic_glitch",
                "name": PATTERN_META["periodic_glitch"][0], "severity": "低", "sev": 1,
                "unit": PATTERN_UNIT["periodic_glitch"],
                "window_start": buckets[anom_idx[0]]["t"],
                "window_end": buckets[anom_idx[-1]]["t"] + size,
                "t_label": fmt_ts(buckets[anom_idx[0]]["t"]),
                "peak_value": len(anom_idx), "baseline": gm, "ratio": None, "z": None,
                "why": "共 %d 个异常窗口，间隔高度均匀（约 %.1f 个窗口 ≈ %.0f 分钟），"
                       "常见于定时任务、健康检查、批处理或对账作业。"
                       % (len(anom_idx), gm, gm * size / 60.0),
                "samples": [], "service": buckets[anom_idx[0]]["top_svc"],
            })

    # 8) 阶跃 / 持续漂移
    if n >= 8:
        for key, label, sev in (("total", "日志量", "中"), ("error_rate", "错误率", "中")):
            vals = [b[key] for b in buckets]
            half = n // 2
            a, c = median(vals[:half]), median(vals[half:])
            if a > 0 and c > a * 2 and c - a > (1 if key == "total" else 0.02):
                patterns.append({
                    "id": "step_shift-%s" % key, "type": "step_shift",
                    "name": PATTERN_META["step_shift"][0], "severity": sev, "sev": SEV_RANK[sev],
                    "unit": PATTERN_UNIT["step_shift"],
                    "window_start": buckets[half]["t"], "window_end": buckets[-1]["t"] + size,
                    "t_label": fmt_ts(buckets[half]["t"]),
                    "peak_value": round(float(c), 4), "baseline": round(float(a), 4),
                    "ratio": round(c / a, 2) if a else None, "z": None,
                    "why": "%s 由前半段中位数 %.4g 抬升到后半段 %.4g（%.1f 倍）并持续维持，"
                           "形态属阶跃而非瞬时尖峰，多见于配置生效、容量变化或慢性泄漏。"
                           % (label, a, c, c / a),
                    "samples": [], "service": buckets[-1]["top_svc"],
                })

    # 9) 级联传播
    onset = {}
    for i, b in enumerate(buckets):
        for svc, cnt in b["svc_err"].items():
            if cnt >= 3 and svc not in onset:
                rate_i = cnt / max(1, b["total"])
                if rate_i >= max(0.05, 2 * base_rate):
                    onset[svc] = i
    if len(onset) >= 2:
        order = sorted(onset.items(), key=lambda kv: kv[1])
        first_i = order[0][1]
        later = [s for s, i in order if i - first_i >= 2]
        if later:
            patterns.append({
                "id": "cascade-0", "type": "cascade",
                "name": PATTERN_META["cascade"][0], "severity": "高", "sev": 3,
                "unit": PATTERN_UNIT["cascade"],
                "window_start": buckets[first_i]["t"],
                "window_end": buckets[max(i for _, i in order)]["t"] + size,
                "t_label": fmt_ts(buckets[first_i]["t"]),
                "peak_value": len(order), "baseline": 1, "ratio": None, "z": None,
                "why": "错误按时间先后在多个服务间扩散：%s。首发服务通常是根因侧，"
                       "后续为被拖垮方，治理应优先处理首发环节。"
                       % (" → ".join("%s(+%d窗口)" % (s, i - first_i) for s, i in order)),
                "samples": [], "service": order[0][0],
                "chain": [{"service": s, "offset": i - first_i} for s, i in order],
            })

    # 10) 日志静默（中间空窗）
    zeros = [i for i, b in enumerate(buckets) if b["total"] == 0]
    if zeros and len(zeros) < n:
        runs, cur = [], [zeros[0]]
        for i in zeros[1:]:
            if i - cur[-1] == 1:
                cur.append(i)
            else:
                runs.append(cur)
                cur = [i]
        runs.append(cur)
        for run in runs:
            if len(run) >= 2 and run[0] > 0 and run[-1] < n - 1:
                i = run[0]
                patterns.append({
                    "id": "silent_gap-%d" % i, "type": "silent_gap",
                    "name": PATTERN_META["silent_gap"][0], "severity": "中", "sev": 2,
                    "unit": PATTERN_UNIT["silent_gap"],
                    "window_start": buckets[run[0]]["t"],
                    "window_end": buckets[run[-1]]["t"] + size,
                    "t_label": fmt_ts(buckets[run[0]]["t"]),
                    "peak_value": 0, "baseline": round(base_total, 2),
                    "ratio": 0, "z": None,
                    "why": "连续 %d 个窗口（约 %.0f 分钟）零日志。若业务指标未下降，"
                           "优先怀疑采集侧（agent/队列/磁盘）而非业务故障。"
                           % (len(run), len(run) * size / 60.0),
                    "samples": [], "service": "-",
                })

    # 11) 饱和度线索（关键词簇）
    sat_kw = re.compile(r"pool|exhaust|too many connections|queue full|rejected|"
                        r"outofmemory|out of memory|oom|full ?gc|gc overhead|"
                        r"no space left|disk full|thread pool|backlog|lag", re.I)
    sat_hits = {}
    for i, b in enumerate(buckets):
        hits = [ln for ln in b["lines"] if sat_kw.search(ln)]
        if hits:
            sat_hits[i] = hits
    if sat_hits:
        i0 = min(sat_hits)
        total_hits = sum(len(v) for v in sat_hits.values())
        patterns.append({
            "id": "saturation-%d" % i0, "type": "saturation",
            "name": PATTERN_META["saturation"][0], "severity": "中", "sev": 2,
            "unit": PATTERN_UNIT["saturation"],
            "window_start": buckets[i0]["t"],
            "window_end": buckets[max(sat_hits)]["t"] + size,
            "t_label": fmt_ts(buckets[i0]["t"]),
            "peak_value": total_hits, "baseline": 0, "ratio": None, "z": None,
            "why": "命中资源饱和类关键词（连接池/线程池/内存/磁盘/队列）共 %d 条，"
                   "属 USE 方法中的饱和度信号，需与资源指标交叉验证。" % total_hits,
            "samples": sat_hits[i0][:3], "service": buckets[i0]["top_svc"],
        })

    # 去重：同类型相邻窗口合并为一段
    merged = []
    for p in sorted(patterns, key=lambda x: (x["type"], x["window_start"])):
        if merged and merged[-1]["type"] == p["type"] \
                and p["window_start"] - merged[-1]["window_end"] <= size:
            m = merged[-1]
            m["window_end"] = max(m["window_end"], p["window_end"])
            if (p["sev"] or 0) > (m["sev"] or 0):
                m["severity"] = p["severity"]
                m["sev"] = p["sev"]
            if (p.get("peak_value") or 0) > (m.get("peak_value") or 0):
                m["peak_value"] = p["peak_value"]
                m["peak_label"] = p["t_label"]
                m["samples"] = p["samples"]
            m["count"] = m.get("count", 1) + 1
        else:
            p["count"] = 1
            merged.append(p)
    for m in merged:
        m["duration_min"] = round((m["window_end"] - m["window_start"]) / 60.0, 1)
    return sorted(merged, key=lambda x: (-x["sev"], x["window_start"]))


# ---------------------------------------------------------------- 根因排序


def score_hypotheses(buckets, patterns, size, changes, top_n=5):
    if not patterns:
        return []
    high = [p for p in patterns if p["sev"] >= 3]
    target_types = {p["type"] for p in patterns}
    ptypes_high = {p["type"] for p in high} or target_types

    anom_idx = set()
    for p in patterns:
        lo = int((p["window_start"] - buckets[0]["t"]) // size)
        hi = int((p["window_end"] - buckets[0]["t"]) // size)
        for i in range(max(0, lo), min(len(buckets), hi + 1)):
            anom_idx.add(i)
    scan_idx = {i for i in anom_idx}
    for i in list(anom_idx):
        for j in (i - 1, i - 2):
            if j >= 0:
                scan_idx.add(j)

    onset_by_type = {}
    for p in patterns:
        t = p["type"]
        if t not in onset_by_type or p["window_start"] < onset_by_type[t]:
            onset_by_type[t] = p["window_start"]

    # 集中度
    conc = 0.0
    svc_shares = [b["top_svc_share"] for b in buckets if b["total"] > 0]
    if svc_shares:
        conc = clamp01(median(svc_shares))

    # 变更时间
    change_ts = None
    if changes:
        change_ts = min(c["t"] for c in changes)

    results = []
    for c in CAUSES:
        rx = [re.compile(k, re.I) for k in c["kw"]]
        hits = []
        first_hit = None
        for i in sorted(scan_idx):
            b = buckets[i]
            for ln in b["lines"]:
                for r in rx:
                    if r.search(ln):
                        hits.append({"t": b["t"], "t_label": fmt_ts(b["t"]),
                                     "text": ln[:220], "kw": r.pattern})
                        if first_hit is None or b["t"] < first_hit:
                            first_hit = b["t"]
                        break
        explained = sorted(set(c["explains"]) & ptypes_high)
        coverage = len(explained) / max(1, len(ptypes_high))
        if not c["explains"] or not (set(c["explains"]) & target_types):
            coverage *= 0.45
        evidence = clamp01(len(hits) / 8.0)
        if first_hit is None:
            temporal = 0.0
        else:
            starts = [onset_by_type[t] for t in explained if t in onset_by_type]
            if not starts:
                temporal = 0.3
            else:
                earliest = min(starts)
                d = earliest - first_hit
                if d >= 0:
                    temporal = 1.0
                elif abs(d) <= 2 * size:
                    temporal = 0.6
                else:
                    temporal = 0.2
        prior = c["prior"]
        conc_bonus = conc if (set(c["explains"]) & {"concentrate_service", "cascade"}) else conc * 0.4
        penalty = 0.0
        if not hits and coverage < 0.5:
            penalty += 0.12
        if c["id"] == "observability-gap" and "silent_gap" not in target_types:
            penalty += 0.35
        if c["id"] == "recent-change":
            if change_ts is not None:
                starts = [onset_by_type[t] for t in explained if t in onset_by_type]
                if starts and abs(min(starts) - change_ts) <= 1800:
                    temporal = 1.0
                    evidence = max(evidence, 0.8)
            else:
                penalty += 0.08

        raw = (0.40 * coverage + 0.28 * evidence + 0.14 * temporal
               + 0.10 * prior + 0.08 * conc_bonus - penalty)
        score = int(round(100 * clamp01(raw)))
        if score < 12:
            continue
        conf = "A" if score >= 65 else ("B" if score >= 40 else "C")
        ev_items = []
        if explained:
            ev_items.append({"type": "形态", "strength": "强" if len(explained) >= 2 else "中",
                             "text": "解释已检出模式：%s" % "、".join(
                                 PATTERN_META[t][0] for t in explained)})
        if hits:
            ev_items.append({"type": "关键词", "strength": "强" if len(hits) >= 5 else "中",
                             "text": "异常窗口内命中 %d 条线索日志，最早 %s，样例：%s"
                                     % (len(hits), fmt_ts(first_hit), hits[0]["text"][:160])})
        if temporal >= 0.6:
            ev_items.append({"type": "时序", "strength": "强" if temporal >= 1.0 else "中",
                             "text": "线索出现时间不晚于被解释模式的起始时间"})
        elif first_hit is not None:
            ev_items.append({"type": "时序", "strength": "弱",
                             "text": "线索出现时间晚于效应起始，可能为次生现象"})
        if change_ts is not None and c["id"] == "recent-change":
            ev_items.append({"type": "变更", "strength": "强",
                             "text": "变更记录 %s 与异常起始时间吻合"
                                     % fmt_ts(change_ts)})
        if not ev_items:
            ev_items.append({"type": "先验", "strength": "弱",
                             "text": "仅凭故障形态先验匹配，无直接日志证据"})
        results.append({
            "id": c["id"], "name": c["name"], "cat": c["cat"], "score": score,
            "confidence": conf,
            "coverage": round(coverage, 2), "evidence": round(evidence, 2),
            "temporal": round(temporal, 2), "hits": len(hits),
            "explains": [PATTERN_META[t][0] for t in explained],
            "evidence_items": ev_items,
            "verify": c["verify"], "falsify": c["falsify"], "fix": c["fix"],
            "first_hit": fmt_ts(first_hit) if first_hit else None,
        })
    results.sort(key=lambda x: (-x["score"], x["id"]))
    for i, r in enumerate(results[:top_n], 1):
        r["rank"] = i
    return results[:top_n]


# ---------------------------------------------------------------- 时间线


def build_timeline(buckets, patterns, size, changes, alert_ts):
    if not buckets:
        return [], {}
    ev = []
    ev.append({"t": buckets[0]["t"], "t_label": fmt_ts(buckets[0]["t"]),
               "phase": "观察起点", "kind": "start", "severity": "低",
               "title": "分析窗口起点",
               "detail": "共 %d 个时间窗，粒度 %s。" % (len(buckets), win_label(size)),
               "source": "聚合"})
    onset = min(p["window_start"] for p in patterns) if patterns else None
    if onset:
        for p in patterns:
            if p["window_start"] <= onset + size:
                ev.append({"t": p["window_start"], "t_label": fmt_ts(p["window_start"]),
                           "phase": "触发", "kind": "anomaly", "severity": p["severity"],
                           "title": p["name"],
                           "detail": "起于 %s，持续 %.0f 分钟，峰值 %s %s。%s"
                                     % (p["t_label"], p.get("duration_min") or 0,
                                        p.get("peak_value"), p.get("unit") or "", p["why"]),
                           "source": "模式识别"})
        hi = [p for p in patterns if p["sev"] >= 3]
        if hi:
            peak = max(hi, key=lambda p: (p.get("peak_value") or 0))
            ev.append({"t": peak["window_start"], "t_label": fmt_ts(peak["window_start"]),
                       "phase": "放大", "kind": "peak", "severity": "高",
                       "title": "影响峰值：%s" % peak["name"],
                       "detail": "峰值 %.4g（基线 %.4g）。%s"
                                 % (peak.get("peak_value") or 0, peak.get("baseline") or 0, peak["why"]),
                       "source": "聚合"})
    for c in (changes or []):
        ev.append({"t": c["t"], "t_label": fmt_ts(c["t"]), "phase": "变更",
                   "kind": "change", "severity": "中",
                   "title": "变更记录", "detail": c["text"], "source": "用户提供"})
    if alert_ts:
        ev.append({"t": alert_ts, "t_label": fmt_ts(alert_ts), "phase": "发现",
                   "kind": "alert", "severity": "中", "title": "告警触发",
                   "detail": "用户提供的告警时刻。", "source": "用户提供"})

    # 恢复判定：异常结束后连续 3 个窗口回到基线
    if patterns:
        last_end = max(p["window_end"] for p in patterns)
        lo_i = int((last_end - buckets[0]["t"]) // size)
        rec_i = None
        rates = [b["error_rate"] for b in buckets]
        base_rate = median(rates)
        for i in range(min(lo_i, len(buckets) - 3), len(buckets) - 2):
            win = buckets[i:i + 3]
            if all(w["error_rate"] <= max(base_rate * 1.5, 0.02) for w in win) \
                    and all(w["total"] > 0 for w in win):
                rec_i = i
                break
        if rec_i is not None:
            ev.append({"t": buckets[rec_i]["t"], "t_label": fmt_ts(buckets[rec_i]["t"]),
                       "phase": "恢复", "kind": "recover", "severity": "低",
                       "title": "指标回到基线",
                       "detail": "连续 3 个窗口错误率 ≤ 基线 1.5 倍且日志未断流，判定为恢复。",
                       "source": "聚合"})
    ev.append({"t": buckets[-1]["t"] + size, "t_label": fmt_ts(buckets[-1]["t"] + size),
               "phase": "观察终点", "kind": "end", "severity": "低",
               "title": "分析窗口终点", "detail": "窗口结束。", "source": "聚合"})
    ev.sort(key=lambda x: (x["t"], {"start": 0, "change": 1, "anomaly": 2, "peak": 3,
                                    "alert": 4, "recover": 5, "end": 6}.get(x["kind"], 9)))

    impact = {}
    if patterns and onset:
        end_t = max(p["window_end"] for p in patterns)
        impact["onset"] = fmt_ts(onset)
        impact["last"] = fmt_ts(end_t)
        impact["impact_min"] = round((end_t - onset) / 60.0, 1)
        impact["windows"] = sum(1 for b in buckets
                                if b["t"] >= onset and b["t"] < end_t)
        impact["peak_rate"] = round(max(b["error_rate"] for b in buckets
                                        if b["t"] >= onset and b["t"] < end_t) * 100, 2)
        if alert_ts and alert_ts >= onset:
            impact["mttd_min"] = round((alert_ts - onset) / 60.0, 1)
        rec = [e for e in ev if e["kind"] == "recover"]
        if rec:
            impact["mttr_min"] = round((rec[0]["t"] - onset) / 60.0, 1)
    return ev, impact


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser(description="日志分析与故障根因定位")
    ap.add_argument("--input", nargs="+", required=True, help="日志文件或目录")
    ap.add_argument("--glob", default="*.log", help="目录模式，默认 *.log")
    ap.add_argument("--window", default="auto", help="时间窗粒度：auto / 30s / 5m / 1h")
    ap.add_argument("--start", default=None, help="起始时间")
    ap.add_argument("--end", default=None, help="结束时间")
    ap.add_argument("--changes", default=None, help="变更记录文件（每行：时间 + 说明）")
    ap.add_argument("--alert", default=None, help="告警触发时刻")
    ap.add_argument("--assume-date", default=None, help="仅有时分秒的日志指定日期")
    ap.add_argument("--top", type=int, default=5, help="根因假设保留条数")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default="data.json")
    args = ap.parse_args()

    paths = []
    for p in args.input:
        if os.path.isdir(p):
            paths.extend(sorted(globmod.glob(os.path.join(p, args.glob))))
            paths.extend(sorted(globmod.glob(os.path.join(p, "*.txt"))))
        elif os.path.isfile(p):
            paths.append(p)
        else:
            paths.extend(sorted(globmod.glob(p)))
    paths = [p for p in dict.fromkeys(paths) if os.path.isfile(p)]
    if not paths:
        print("[FATAL] 未找到任何输入文件")
        sys.exit(2)

    assume_date = None
    if args.assume_date:
        try:
            assume_date = dt.datetime.strptime(args.assume_date, "%Y-%m-%d").date()
        except ValueError:
            print("[WARN] --assume-date 格式应为 YYYY-MM-DD，已忽略")
    ref_year = dt.datetime.now().year

    records, stats = load_records(paths, assume_date, ref_year)
    if not records:
        print("[FATAL] 未解析出任何带时间戳的日志行，请检查日志格式或用 --assume-date 补全日期")
        sys.exit(2)
    records.sort(key=lambda r: r["t"])

    t_start = parse_user_time(args.start)
    t_end = parse_user_time(args.end)
    if t_start:
        records = [r for r in records if r["t"] >= t_start]
    if t_end:
        records = [r for r in records if r["t"] <= t_end]
    if not records:
        print("[FATAL] 按 --start/--end 过滤后无数据")
        sys.exit(2)

    span = max(r["t"] for r in records) - min(r["t"] for r in records)
    if args.window == "auto":
        target = 80.0
        size = WINDOW_LADDER[-1]
        for s in WINDOW_LADDER:
            if span / s <= target:
                size = s
                break
    else:
        size = parse_window(args.window)
        if not size:
            print("[FATAL] --window 格式错误，示例：30s / 5m / 1h")
            sys.exit(2)

    buckets = build_buckets(records, size, t_start, t_end)
    patterns = detect_patterns(buckets, size)

    changes = []
    if args.changes and os.path.isfile(args.changes):
        for line in open(args.changes, "r", encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            ts, _ = parse_ts_from_text(line, assume_date, ref_year)
            if ts is None:
                continue
            text = re.sub(r"^\s*\S+\s+\S+\s*", "", line).strip() or line
            changes.append({"t": ts, "text": text[:200]})

    hyps = score_hypotheses(buckets, patterns, size, changes, args.top)
    alert_ts = parse_user_time(args.alert)
    timeline, impact = build_timeline(buckets, patterns, size, changes, alert_ts)

    parse_rate = stats["parsed"] / max(1, stats["lines"])
    flags = []
    if parse_rate < 0.9:
        flags.append("时间戳解析率 %.1f%%，未解析行已丢弃；跨时区或非常规时间格式请用 "
                     "--assume-date 补全日期。" % (parse_rate * 100))
    if stats.get("tz_hint"):
        flags.append("检测到含时区偏移的时间戳，已按原样解析；跨时区日志建议先统一为 UTC 再分析。")
    if not any(b["p95"] is not None for b in buckets):
        flags.append("未从日志中抽取到耗时字段，延迟类模式（延迟抬升/P99）无法判定；"
                     "可用 JSON 字段 rt/latency/duration 或文本 rt=123ms 形式补充。")
    if not patterns:
        flags.append("未检出显著异常模式：可能是窗口粒度过大掩盖尖峰，建议改用更小粒度或聚焦故障时段。")
    if len(buckets) < 12:
        flags.append("时间窗数量偏少（%d 个），统计基线可靠性有限，结论请谨慎。"
                     % len(buckets))
    flags.append("根因结论为「假设排序」而非定论：须用指标、调用链与变更记录交叉验证后确认。")

    svc_top = {}
    for b in buckets:
        if b["top_svc"] and b["total"]:
            svc_top[b["top_svc"]] = svc_top.get(b["top_svc"], 0) + b["total"]
    top_services = sorted(svc_top.items(), key=lambda kv: -kv[1])[:5]

    summary = []
    summary.append("解析 %d 行（解析率 %.1f%%），覆盖 %s 至 %s，按 %s 粒度聚合为 %d 个时间窗。"
                   % (stats["parsed"], parse_rate * 100,
                      fmt_ts(buckets[0]["t"]), fmt_ts(buckets[-1]["t"] + size),
                      win_label(size), len(buckets)))
    if impact:
        summary.append("首个异常出现在 %s，影响持续约 %.0f 分钟（%s → %s），峰值错误率 %.2f%%。"
                       % (impact["onset"], impact["impact_min"], impact["onset"],
                          impact["last"], impact["peak_rate"]))
    else:
        summary.append("未检出显著异常窗口，本次分析以基线与分布描述为主。")
    if patterns:
        top3 = patterns[:3]
        summary.append("异常模式 %d 类，最突出的是：%s。"
                       % (len(patterns), "、".join("%s（%s）" % (p["name"], p["t_label"]) for p in top3)))
    if hyps:
        h = hyps[0]
        summary.append("根因假设首位：%s（%d 分，置信度 %s），次位 %s（%d 分）。"
                       % (h["name"], h["score"], h["confidence"],
                          hyps[1]["name"] if len(hyps) > 1 else "无",
                          hyps[1]["score"] if len(hyps) > 1 else 0))
    if top_services:
        summary.append("日志量集中在：%s。" % "、".join("%s(%d)" % (s, c) for s, c in top_services[:3]))

    title = args.title or "日志分析与故障根因定位"
    data = {
        "meta": {
            "title": title,
            "subtitle": "时间窗聚合 · 异常模式识别 · 根因假设排序 · 故障时间线",
            "scope": "%d 个文件 / %d 行" % (stats["files"], stats["lines"]),
            "window": win_label(size),
            "bucket_size": size,
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "files": stats["files"],
            "lines": stats["lines"],
            "parsed": stats["parsed"],
            "parse_rate": round(parse_rate, 4),
            "formats": stats["formats"],
            "buckets": len(buckets),
            "span_label": "%s → %s" % (fmt_ts(buckets[0]["t"]), fmt_ts(buckets[-1]["t"] + size)),
            "top_services": [{"name": s, "count": c} for s, c in top_services],
        },
        "summary": summary,
        "impact": impact,
        "buckets": [{k: v for k, v in b.items() if k not in ("lines",)} for b in buckets],
        "patterns": patterns,
        "hypotheses": hyps,
        "timeline": timeline,
        "changes": [{"t_label": fmt_ts(c["t"]), "text": c["text"]} for c in changes],
        "flags": flags,
        "sources": METHOD_SOURCES,
    }

    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print("[OK] 已生成 %s" % out_path)
    print("     文件 %d | 行数 %d | 解析 %d (%.1f%%) | 时间窗 %d x %s"
          % (stats["files"], stats["lines"], stats["parsed"], parse_rate * 100,
             len(buckets), win_label(size)))
    print("     异常模式 %d 类 | 根因假设 %d 条 | 时间线事件 %d 个"
          % (len(patterns), len(hyps), len(timeline)))
    if hyps:
        print("     Top1: %s (%d 分 / %s)" % (hyps[0]["name"], hyps[0]["score"], hyps[0]["confidence"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
