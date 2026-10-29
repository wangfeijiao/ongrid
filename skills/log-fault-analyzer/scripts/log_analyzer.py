#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
故障日志诊断专家 - 核心分析脚本

功能：
  1. 预处理日志：提取 ERROR/Exception/堆栈/超时/拒绝连接等关键片段
  2. 错误模式匹配：对照常见错误模式库，识别根因候选并标注置信度
  3. 生成结构化报告：按固定 Markdown 格式输出故障分析报告

用法：
  python log_analyzer.py <日志文件路径>
  python log_analyzer.py <日志文件路径> -o <输出文件路径>
  cat app.log | python log_analyzer.py

输出：结构化 Markdown 故障分析报告（输出到 stdout 或指定文件）
"""

import re
import sys
import os
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ErrorPattern:
    """错误模式定义"""
    pattern: str                    # 正则表达式
    name: str                       # 错误名称
    root_cause: str                 # 最可能根因
    confidence: str                 # 置信度：high / medium / low
    category: str                   # 分类：java/python/db/redis/http/warehouse/os/mq
    troubleshoot: List[str]         # 立即排查命令/检查点
    deep_troubleshoot: List[str]    # 深度排查命令/检查点
    fix_immediate: str              # 立即止血
    fix_short: str                  # 短期修复
    fix_long: str                   # 长期优化
    missing_info: List[str]         # 通常缺失的信息


@dataclass
class MatchedPattern:
    """匹配到的错误模式及证据"""
    pattern: ErrorPattern
    evidence: str                   # 匹配到的日志原文片段
    line_number: int                # 所在行号
    adjusted_confidence: str = ""   # 调整后的置信度（低证据场景自动降级）
    confidence_adjusted: bool = False  # 是否被调整过
    specificity: int = 0            # 命中文本长度（越长越特异，用于同证据多模式排序）
    is_alternative: bool = False    # 备选标记：同证据下比主推测更泛化的推测
    primary_name: str = ""          # 同证据主推测的模式名（备选时使用）
    chain_id: int = 0               # 故障链编号（同一时间链上的模式共享编号；0=未分组）
    role: str = ""                  # 链内角色：main(主因)/trigger(诱因)/effect(结果)/independent(独立故障)


@dataclass
class AnalysisResult:
    """分析结果"""
    summary: str = ""
    evidence: List[str] = field(default_factory=list)
    root_causes: List[MatchedPattern] = field(default_factory=list)
    immediate_checks: List[str] = field(default_factory=list)
    deep_checks: List[str] = field(default_factory=list)
    fixes: List[Tuple[str, str, str]] = field(default_factory=list)  # (immediate, short, long)
    missing_info: List[str] = field(default_factory=list)
    raw_errors: List[str] = field(default_factory=list)
    has_errors: bool = False
    has_stack_trace: bool = False  # 是否检测到堆栈信息
    any_confidence_adjusted: bool = False  # 是否有根因置信度被下调
    log_processing_note: str = ""  # 日志处理说明（如超长截取、格式转换等）
    original_line_count: int = 0  # 原始日志行数
    processed_line_count: int = 0  # 处理后日志行数


# ============================================================
# 错误模式库
# ============================================================

def load_custom_patterns(filepath: str) -> List[ErrorPattern]:
    """
    从 JSON 文件加载自定义错误模式，与内置模式合并使用。

    JSON 格式示例：
    [
      {
        "pattern": "MyCustomException",
        "name": "自定义异常",
        "root_cause": "自定义异常的根因描述",
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
    """
    import json
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print(f"[WARN] 自定义模式文件格式错误，应为 JSON 数组: {filepath}", file=sys.stderr)
            return []
        patterns = []
        for item in data:
            try:
                patterns.append(ErrorPattern(
                    pattern=item.get("pattern", ""),
                    name=item.get("name", "自定义模式"),
                    root_cause=item.get("root_cause", ""),
                    confidence=item.get("confidence", "medium"),
                    category=item.get("category", "custom"),
                    troubleshoot=item.get("troubleshoot", []),
                    deep_troubleshoot=item.get("deep_troubleshoot", []),
                    fix_immediate=item.get("fix_immediate", ""),
                    fix_short=item.get("fix_short", ""),
                    fix_long=item.get("fix_long", ""),
                    missing_info=item.get("missing_info", []),
                ))
            except Exception as e:
                print(f"[WARN] 跳过无效的自定义模式: {e}", file=sys.stderr)
        print(f"[INFO] 加载 {len(patterns)} 个自定义模式", file=sys.stderr)
        return patterns
    except FileNotFoundError:
        print(f"[WARN] 自定义模式文件不存在: {filepath}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"[WARN] 自定义模式文件 JSON 解析失败: {e}", file=sys.stderr)
        return []


ERROR_PATTERNS: List[ErrorPattern] = [
    # ---------- Java / JVM ----------
    ErrorPattern(
        pattern=r"NullPointerException.*?because \"?(\w+)\"? is null",
        name="Java NullPointerException (对象为null)",
        root_cause="代码中对象为 null 时调用了其方法/属性，未做 null 校验",
        confidence="high",
        category="java",
        troubleshoot=[
            "查看堆栈最顶层行号，定位 null 对象的来源（DB查询/RPC调用/缓存获取）",
            "用 traceId 搜索完整调用链日志，确认对象获取环节",
            "用相同输入复现请求，观察是否稳定复现",
        ],
        deep_troubleshoot=[
            "检查上游服务是否有发布/降级导致返回 null",
            "检查缓存是否过期且未回源 DB",
            "检查 DB 中该记录是否存在（可能被物理删除）",
        ],
        fix_immediate="在调用处增加 null 校验，为 null 时抛出明确业务异常",
        fix_short="排查对象为 null 的上游原因，在源头增加兜底逻辑",
        fix_long="全局异常处理 + 防御性编程规范，对所有外部依赖返回值做 null 检查",
        missing_info=["null 对象的获取代码（堆栈行号之前的逻辑）", "上游服务日志", "具体业务 ID/参数"],
    ),
    ErrorPattern(
        pattern=r"panic: runtime error: nil pointer dereference|panic: runtime error: invalid memory address|panic: runtime error: index out of range",
        name="Go panic (nil pointer/越界)",
        root_cause="Go 程序运行时 panic，通常是 nil 指针解引用、数组/切片越界或类型断言失败，导致程序崩溃（如未 recover 则进程退出）",
        confidence="high",
        category="go",
        troubleshoot=[
            "查看 panic 堆栈最顶层业务代码行号，定位 nil 对象或越界索引",
            "检查该变量/切片的赋值来源（函数返回值/DB查询/外部调用）",
            "确认是否有 recover() 机制捕获 panic，如无则进程会退出",
        ],
        deep_troubleshoot=[
            "检查并发场景下是否有 data race（go run -race）",
            "检查是否有未初始化的 struct 字段或 nil channel 操作",
            "检查类型断言是否使用了 comma-ok 模式（value, ok := x.(T)）",
            "检查是否有 defer 中 panic 导致原始错误被覆盖",
        ],
        fix_immediate="在 panic 点增加 nil 检查/越界检查，或增加 recover() 防止进程崩溃",
        fix_short="排查 nil/越界根因，在源头增加防御性检查和错误返回",
        fix_long="代码审查规范（所有外部返回值必须检查 nil/err），panic 监控告警，集成测试覆盖边界场景",
        missing_info=["panic 堆栈中业务代码行号对应的源码", "触发 panic 的具体输入参数", "是否有 recover() 机制"],
    ),
    ErrorPattern(
        pattern=r"OutOfMemoryError.*?Java heap space",
        name="JVM 堆内存溢出 (OOM)",
        root_cause="JVM 堆内存不足，可能存在内存泄漏或大对象占用",
        confidence="high",
        category="java",
        troubleshoot=[
            "jmap -heap <pid> 查看堆内存使用情况",
            "jstat -gcutil <pid> 1s 观察 GC 频率和堆使用率",
            "jmap -dump:format=b,file=/tmp/heap.hprof <pid> 导出堆转储（如服务还在运行）",
        ],
        deep_troubleshoot=[
            "用 MAT/JProfiler 分析 heap dump，定位大对象和内存泄漏点",
            "检查近期是否有大查询/全表扫描导致对象激增",
            "检查缓存是否无上限增长",
        ],
        fix_immediate="重启服务释放内存，临时调大 -Xmx",
        fix_short="分析 heap dump 定位内存泄漏，修复代码",
        fix_long="配置内存使用率告警，增加大对象限制，缓存设置上限和 TTL",
        missing_info=["堆转储文件 (heap dump)", "GC 日志", "近期代码变更记录"],
    ),
    ErrorPattern(
        pattern=r"StackOverflowError",
        name="栈溢出 (递归过深)",
        root_cause="方法递归调用过深或存在无限递归，导致栈空间耗尽",
        confidence="high",
        category="java",
        troubleshoot=[
            "查看堆栈中重复出现的方法，定位递归调用点",
            "检查递归终止条件是否正确",
        ],
        deep_troubleshoot=[
            "检查是否有循环依赖导致的递归调用",
            "检查数据结构是否有环形引用",
        ],
        fix_immediate="增加递归深度限制或改为迭代实现",
        fix_short="修复递归终止条件，消除无限递归",
        fix_long="代码审查中增加递归调用的深度检查规范",
        missing_info=["完整堆栈（重复方法名）", "触发递归的输入数据"],
    ),

    # ---------- Python ----------
    ErrorPattern(
        pattern=r"requests\.exceptions\.ConnectTimeout",
        name="Python requests 连接超时",
        root_cause="TCP 连接阶段超时，目标服务不可达或网络链路异常",
        confidence="high",
        category="python",
        troubleshoot=[
            "curl -v --connect-timeout 5 <目标URL> 测试连通性",
            "nc -zv <host> <port> 测试端口连通性",
            "nslookup <host> 检查 DNS 解析",
        ],
        deep_troubleshoot=[
            "tcpdump -i any host <host> and port <port> 抓包确认 TCP 握手",
            "检查出口 IP 是否在目标服务白名单内",
            "检查防火墙/安全组是否限制出站连接",
        ],
        fix_immediate="调大 connect timeout（如 3s→5-10s），增加指数退避重试",
        fix_short="若目标服务不可达，联系对方恢复；若为网络问题，协调运维排查",
        fix_long="回调失败进入死信队列，设置最大重试次数和告警阈值",
        missing_info=["目标服务状态", "网络诊断数据 (ping/traceroute)", "出口 IP 信息"],
    ),
    ErrorPattern(
        pattern=r"requests\.exceptions\.ReadTimeout",
        name="Python requests 响应超时",
        root_cause="TCP 连接成功但下游服务响应时间超过 read timeout 阈值",
        confidence="high",
        category="python",
        troubleshoot=[
            "检查下游服务响应时间和负载",
            "查看下游是否有慢查询/慢操作",
            "确认 read timeout 配置值是否合理",
        ],
        deep_troubleshoot=[
            "下游服务 APM 追踪，定位慢操作环节",
            "检查下游数据库慢查询日志",
            "检查是否有下游依赖级联超时",
        ],
        fix_immediate="临时调大 read timeout，增加重试",
        fix_short="优化下游慢查询/慢操作，降低响应时间",
        fix_long="全链路超时配置规范，下游增加缓存和降级",
        missing_info=["下游服务响应时间监控", "下游慢查询日志", "read timeout 配置值"],
    ),
    ErrorPattern(
        pattern=r"requests\.exceptions\.ConnectionError.*?Max retries exceeded",
        name="Python requests 连接失败 (Max retries exceeded)",
        root_cause="requests 请求重试达到上限，通常是 DNS 解析失败、目标服务不可达、TLS 握手失败或连接超时（区别于单次连接被拒绝）",
        confidence="high",
        category="python",
        troubleshoot=[
            "确认目标主机名 DNS 解析：nslookup/getent hosts <host>",
            "测试目标地址连通性：nc -zv <host> <port> 或 curl -v <url>",
            "检查 TLS/证书环节：curl -k -v <url> 观察握手阶段报错",
        ],
        deep_troubleshoot=[
            "检查重试次数与超时配置（urllib3 Retry 策略）",
            "确认目标服务是否在发布/重启导致间歇性不可达",
            "检查是否因代理/防火墙对长连接进行重置",
        ],
        fix_immediate="临时增加重试次数或调大连接超时",
        fix_short="排查 DNS/网络/证书根因，恢复目标服务可达性",
        fix_long="对关键依赖配置健康检查与降级，设置合理的重试与超时规范",
        missing_info=["目标服务状态与部署记录", "DNS 解析结果", "TLS 握手阶段错误信息"],
    ),
    ErrorPattern(
        pattern=r"KeyError:\s*['\"]?(\w+)['\"]?",
        name="Python KeyError (字典键不存在)",
        root_cause="字典中不存在指定的 key，可能是数据缺失或 key 名拼写错误",
        confidence="high",
        category="python",
        troubleshoot=[
            "检查字典实际包含的 keys：print(dict.keys())",
            "确认 key 名拼写是否正确（大小写、下划线）",
            "检查数据来源是否可能缺少该字段",
        ],
        deep_troubleshoot=[
            "检查上游数据结构是否有变更",
            "检查是否存在条件分支导致某些路径不设置该 key",
        ],
        fix_immediate="使用 dict.get(key, default) 替代直接访问，提供默认值",
        fix_short="排查 key 缺失的上游原因，确保数据完整性",
        fix_long="数据校验层增加必填字段检查，提前抛出明确异常",
        missing_info=["字典的实际 keys", "数据来源/上游接口返回结构"],
    ),
    ErrorPattern(
        pattern=r"AttributeError:.*?'NoneType' object has no attribute",
        name="Python NoneType 属性错误",
        root_cause="对象为 None 时调用其属性/方法，未做 None 校验",
        confidence="high",
        category="python",
        troubleshoot=[
            "查看堆栈定位 None 对象的来源（函数返回值/查询结果）",
            "检查该函数/查询在什么情况下返回 None",
        ],
        deep_troubleshoot=[
            "检查上游接口/数据库是否可能返回空结果",
            "检查是否有异常被吞掉导致返回 None",
        ],
        fix_immediate="增加 None 校验，为 None 时抛出明确异常或返回默认值",
        fix_short="排查对象为 None 的上游原因，在源头处理",
        fix_long="类型提示 + 静态检查（mypy），提前发现 None 解引用风险",
        missing_info=["None 对象的获取代码", "上游返回数据样本"],
    ),

    # ---------- 数据库 ----------
    ErrorPattern(
        pattern=r"deadlock detected|Deadlock found when trying to get lock",
        name="数据库死锁",
        root_cause="两个或多个事务相互等待对方释放锁，形成死锁",
        confidence="high",
        category="db",
        troubleshoot=[
            "查看数据库死锁日志，获取涉及的事务和 SQL",
            "分析事务加锁顺序，找出循环等待点",
            "检查是否有长事务持锁不释放",
        ],
        deep_troubleshoot=[
            "检查事务隔离级别是否过高",
            "检查是否有大事务包含过多操作",
            "检查索引是否合理（全表扫描会升级锁范围）",
        ],
        fix_immediate="KILL 死锁中的事务，恢复业务",
        fix_short="统一事务加锁顺序，减小事务粒度，增加重试机制",
        fix_long="死锁监控告警，事务规范审查，索引优化减少锁范围",
        missing_info=["完整死锁日志（涉及的事务和 SQL）", "事务隔离级别配置", "相关表索引信息"],
    ),
    ErrorPattern(
        pattern=r"statement timeout|query timeout|query timed out|canceling statement due to statement timeout|Statement cancelled due to timeout|MySQLTimeoutException",
        name="SQL 执行超时",
        root_cause="SQL 执行时间超过 statement_timeout 阈值被强制取消，可能是慢查询或数据库负载高",
        confidence="high",
        category="db",
        troubleshoot=[
            "EXPLAIN ANALYZE <SQL> 查看执行计划和实际耗时",
            "检查相关表索引是否存在：\\d <table>",
            "查看数据库当前活动：SELECT * FROM pg_stat_activity WHERE state != 'idle';",
        ],
        deep_troubleshoot=[
            "检查表膨胀（dead tuples）：SELECT relname, n_dead_tup FROM pg_stat_user_tables;",
            "检查是否有长事务持锁：SELECT pid, xact_start FROM pg_stat_activity WHERE state = 'idle in transaction';",
            "检查数据库服务器资源（CPU/IO/内存）",
        ],
        fix_immediate="临时调大 statement_timeout，或为查询增加索引",
        fix_short="优化慢 SQL（加索引/改写查询/分页），VACUUM ANALYZE 更新统计信息",
        fix_long="慢查询监控告警，定期审查慢查询日志，SQL 规范审查",
        missing_info=["完整 SQL 语句", "EXPLAIN ANALYZE 结果", "表结构和索引信息", "statement_timeout 配置值"],
    ),
    ErrorPattern(
        pattern=r"Too many connections|too many clients already",
        name="数据库连接数耗尽",
        root_cause="数据库连接数达到 max_connections 上限，可能是连接池配置过大或连接泄漏",
        confidence="high",
        category="db",
        troubleshoot=[
            "SHOW PROCESSLIST; (MySQL) 或 SELECT count(*), state FROM pg_stat_activity GROUP BY state; (PG)",
            "检查应用连接池配置（最大连接数）",
            "检查是否有连接泄漏（长事务/未关闭连接）",
        ],
        deep_troubleshoot=[
            "计算各应用连接池总和是否超过 max_connections",
            "检查是否有异常流量导致连接激增",
            "检查数据库端 idle_in_transaction 连接数量",
        ],
        fix_immediate="KILL 空闲连接，临时调大 max_connections",
        fix_short="调小应用连接池最大连接数，修复连接泄漏",
        fix_long="连接池规范（总量 ≤ max_connections 的 80%），连接泄漏监控",
        missing_info=["各应用连接池配置", "当前连接列表和状态", "max_connections 配置值"],
    ),
    ErrorPattern(
        pattern=r"Table .*? doesn't exist|relation .*? does not exist|Table or view not found",
        name="数据库表不存在",
        root_cause="SQL 引用的表不存在（未创建/被删除/库名错误/连错环境）",
        confidence="high",
        category="db",
        troubleshoot=[
            "SHOW TABLES; (MySQL) 或 \\dt (PostgreSQL) 确认表是否存在",
            "确认数据库名/ schema 名是否正确",
            "检查近期是否有 DROP TABLE 操作",
        ],
        deep_troubleshoot=[
            "检查是否连接了错误的数据库环境（测试 vs 生产）",
            "检查数据库迁移脚本是否执行成功",
            "HDFS 层面确认数据目录是否存在（数仓场景）",
        ],
        fix_immediate="重新创建表（如有建表语句），或修正库名/表名",
        fix_short="从备份恢复表结构和数据，或重新执行上游任务生成数据",
        fix_long="DDL 变更审批流程，表存在性前置检查，环境隔离配置规范",
        missing_info=["建表语句", "近期 DDL 操作审计日志", "数据库环境配置"],
    ),
    ErrorPattern(
        pattern=r"Duplicate entry.*?for key|unique constraint.*?violated",
        name="唯一约束冲突 (重复键)",
        root_cause="写入数据违反唯一约束，可能是重复提交或业务逻辑未做幂等",
        confidence="high",
        category="db",
        troubleshoot=[
            "查看冲突的唯一键名和重复值",
            "检查业务逻辑是否有重复写入路径",
            "检查是否有重试机制导致重复提交",
        ],
        deep_troubleshoot=[
            "检查是否有并发写入同一唯一键",
            "检查上游是否有重复消息/重复请求",
        ],
        fix_immediate="删除重复数据或修改冲突数据",
        fix_short="增加幂等性检查，使用 INSERT ... ON DUPLICATE KEY UPDATE 或 UPSERT",
        fix_long="唯一键设计规范，幂等性设计审查",
        missing_info=["冲突的唯一键名和值", "业务写入逻辑代码", "上游请求/消息样本"],
    ),
    ErrorPattern(
        pattern=r"Connection is not available|SQLTransientConnectionException|CannotGetJdbcConnectionException|pool.*?exhausted|HikariPool.*?Connection is not available|request timed out after.*?ms",
        name="数据库连接池耗尽",
        root_cause="连接池所有连接被占用且等待超时，可能是慢查询持锁不释放、连接泄漏或连接池配置过小",
        confidence="high",
        category="db",
        troubleshoot=[
            "查看连接池监控指标（active/idle/waiting），确认是否 active=max 且 waiting>0",
            "检查数据库慢查询日志，定位长时间运行的 SQL",
            "检查是否有长事务持连接不释放（idle in transaction）",
        ],
        deep_troubleshoot=[
            "用 jstack 抓取线程栈，查看持有连接的线程在执行什么操作",
            "检查连接池配置（maximumPoolSize、connectionTimeout）是否合理",
            "检查是否有连接泄漏（代码中未关闭连接/未释放连接到池）",
            "检查数据库端是否有锁等待导致连接长时间不释放",
        ],
        fix_immediate="重启应用释放连接，临时调大 maximumPoolSize 和 connectionTimeout",
        fix_short="优化慢查询/长事务，修复连接泄漏，合理配置连接池大小",
        fix_long="连接池监控告警（active使用率>80%、waiting>0持续告警），慢查询治理，连接泄漏检测",
        missing_info=["连接池配置（maximumPoolSize/connectionTimeout）", "连接池监控指标（active/idle/waiting趋势）", "数据库慢查询日志", "线程栈快照"],
    ),

    # ---------- Redis ----------
    ErrorPattern(
        pattern=r"OOM command not allowed when used memory > 'maxmemory'",
        name="Redis 内存溢出 (OOM)",
        root_cause="Redis 内存使用量达到 maxmemory 上限，且淘汰策略为 noeviction，写入命令被拒绝",
        confidence="high",
        category="redis",
        troubleshoot=[
            "redis-cli INFO memory 查看内存使用量和 maxmemory",
            "redis-cli CONFIG GET maxmemory-policy 确认淘汰策略",
            "redis-cli --bigkeys 查找大 key",
        ],
        deep_troubleshoot=[
            "检查是否有未设置 TTL 的 key 持续增长",
            "检查内存使用趋势（是否突发增长）",
            "检查是否有热 key 或异常写入",
            "检查 RDB/AOF 持久化是否导致额外内存占用",
        ],
        fix_immediate="手动 UNLINK 大 key 释放内存，或临时调大 maxmemory",
        fix_short="将 maxmemory-policy 改为 allkeys-lru，修复未设 TTL 的缓存",
        fix_long="内存使用率告警（>80%），大 key 监控，缓存规范（必须设 TTL）",
        missing_info=["INFO memory 输出", "maxmemory 和 maxmemory-policy 配置值", "--bigkeys 分析结果", "内存使用趋势监控"],
    ),
    ErrorPattern(
        pattern=r"Connection refused.*?6379|redis.*?Connection refused",
        name="Redis 连接被拒绝",
        root_cause="Redis 服务未运行或端口未监听，导致连接被拒绝",
        confidence="high",
        category="redis",
        troubleshoot=[
            "systemctl status redis 检查 Redis 服务状态",
            "ss -tlnp | grep 6379 确认端口是否监听",
            "redis-cli ping 测试连接",
        ],
        deep_troubleshoot=[
            "查看 Redis 日志确认是否崩溃：tail -100 /var/log/redis/redis-server.log",
            "检查是否有 OOM Killer 杀掉 Redis：dmesg | grep -i redis",
            "检查磁盘空间（RDB 持久化失败可能导致 Redis 拒绝写入）",
        ],
        fix_immediate="启动 Redis 服务：systemctl start redis",
        fix_short="排查 Redis 崩溃原因（OOM/配置错误/磁盘满），修复后确保自启",
        fix_long="Redis 高可用（主从/哨兵/集群），服务存活监控告警",
        missing_info=["Redis 错误日志", "服务器内存/磁盘资源数据", "Redis 配置文件"],
    ),
    ErrorPattern(
        pattern=r"MOVED \d+ [\d.]+:\d+",
        name="Redis Cluster 重定向",
        root_cause="Cluster 模式下 key 不在当前节点，客户端未正确处理重定向",
        confidence="medium",
        category="redis",
        troubleshoot=[
            "确认客户端是否支持 Cluster 模式（应使用 RedisCluster 客户端）",
            "CLUSTER KEYSLOT <key> 查看 key 所属 slot",
            "检查集群节点状态：CLUSTER NODES",
        ],
        deep_troubleshoot=[
            "检查是否有节点迁移导致 slot 变化",
            "检查客户端连接配置是否正确（应连集群节点而非单节点）",
        ],
        fix_immediate="更换为支持 Cluster 模式的客户端库",
        fix_short="修正客户端连接配置，使用集群入口节点",
        fix_long="集群客户端使用规范，集群状态监控",
        missing_info=["客户端代码和配置", "CLUSTER NODES 输出", "具体 key 值"],
    ),

    # ---------- HTTP / 网关 ----------
    ErrorPattern(
        pattern=r"connect\(\) failed \(111: Connection refused\).*?upstream",
        name="Nginx 上游连接被拒绝 (502)",
        root_cause="Nginx 向上游服务转发请求时连接被拒绝，上游端口无进程监听（服务崩溃/未启动/重启中）",
        confidence="high",
        category="http",
        troubleshoot=[
            "登录上游服务器，ss -tlnp | grep <port> 确认端口是否监听",
            "检查上游服务进程状态：systemctl status <service> 或 docker ps",
            "在 Nginx 服务器测试上游连通性：curl -v http://<upstream>:<port>/health",
        ],
        deep_troubleshoot=[
            "查看上游服务最近日志，确认是否 OOM/崩溃/重启",
            "检查是否有发布/滚动更新在进行",
            "检查 Nginx upstream 配置是否只有单节点（无冗余）",
            "K8s 场景：kubectl get pods -o wide、kubectl describe pod 查看事件",
        ],
        fix_immediate="重启上游服务，确认端口恢复监听",
        fix_short="排查服务崩溃原因（OOM/代码 bug/资源耗尽），修复后重新部署",
        fix_long="Nginx upstream 至少 2 节点 + 健康检查，滚动发布确保端口持续可用",
        missing_info=["上游服务应用日志", "上游服务器资源监控 (CPU/内存)", "Nginx upstream 配置", "发布/重启操作记录"],
    ),
    ErrorPattern(
        pattern=r"upstream prematurely closed connection",
        name="Nginx 上游提前关闭连接 (502)",
        root_cause="上游在响应完成前关闭连接，可能是进程崩溃、被 kill、或主动断开",
        confidence="high",
        category="http",
        troubleshoot=[
            "查看上游服务是否有崩溃/OOM 日志",
            "dmesg | grep -i 'killed process' 检查 OOM Killer",
            "检查上游是否有主动关闭连接的逻辑（如超时断开）",
        ],
        deep_troubleshoot=[
            "检查上游 worker 进程数和请求处理能力",
            "检查是否有大请求导致 worker 崩溃",
            "检查上游 keepalive 配置",
        ],
        fix_immediate="重启上游服务，增加 worker 进程数",
        fix_short="排查崩溃原因，修复代码或增加资源",
        fix_long="上游服务健康检查 + 自动重启，请求大小限制",
        missing_info=["上游服务崩溃日志", "OOM/资源监控数据", "上游 worker 配置"],
    ),
    ErrorPattern(
        pattern=r"504 Gateway Time-out|upstream timed out",
        name="网关超时 (504)",
        root_cause="Nginx 等待上游响应超过 proxy_read_timeout 阈值，上游处理过慢",
        confidence="high",
        category="http",
        troubleshoot=[
            "检查上游服务响应时间和负载",
            "查看上游慢查询/慢操作日志",
            "确认 proxy_read_timeout 配置值",
        ],
        deep_troubleshoot=[
            "上游 APM 全链路追踪，定位慢环节",
            "检查是否有下游依赖级联超时",
            "检查数据库慢查询",
        ],
        fix_immediate="临时调大 proxy_read_timeout，增加上游实例",
        fix_short="优化上游慢操作，降低响应时间",
        fix_long="全链路超时规范，下游降级和缓存",
        missing_info=["上游响应时间监控", "上游慢操作日志", "proxy_read_timeout 配置值"],
    ),

    # ---------- 数仓 / 大数据 ----------
    ErrorPattern(
        pattern=r"FetchFailedException|shuffle.*?failed",
        name="Spark Shuffle 拉取失败",
        root_cause="Executor 间 Shuffle 数据拉取失败，可能是 Executor 崩溃、网络问题或磁盘满",
        confidence="high",
        category="warehouse",
        troubleshoot=[
            "查看失败 Executor 的日志，确认是否 OOM 或崩溃",
            "检查 Shuffle 服务状态和网络连通性",
            "检查 Executor 节点磁盘空间：df -h",
        ],
        deep_troubleshoot=[
            "检查是否有数据倾斜导致单 Executor 压力过大",
            "增加 spark.shuffle.io.maxRetries 重试次数",
            "检查 Shuffle 服务（ESS）配置",
        ],
        fix_immediate="增加 Shuffle 重试次数，重新提交任务",
        fix_short="排查 Executor 崩溃原因（OOM/磁盘满），修复后重跑",
        fix_long="数据倾斜优化，Shuffle 服务高可用，Executor 资源监控",
        missing_info=["Executor 错误日志", "YARN 容器资源配置", "Shuffle 数据量统计"],
    ),
    ErrorPattern(
        pattern=r"Container killed by YARN for exceeding memory limits",
        name="YARN 容器内存超限被杀",
        root_cause="Spark Executor/AM 容器内存使用超过 YARN 限制（堆内存+堆外内存），被 YARN 杀掉",
        confidence="high",
        category="warehouse",
        troubleshoot=[
            "检查 spark.executor.memory 和 spark.executor.memoryOverhead 配置",
            "查看 YARN 容器日志确认内存使用峰值",
            "检查是否有数据倾斜导致单 Executor 内存激增",
        ],
        deep_troubleshoot=[
            "分析 SQL 执行计划，确认是否有大 Join/聚合",
            "检查是否有广播 join 配置不当（广播大表）",
            "检查堆外内存使用（Python UDF/原生库）",
        ],
        fix_immediate="增加 spark.executor.memoryOverhead（如 1g→2g），重新提交",
        fix_short="优化数据倾斜，增加 Executor 内存，调整并行度",
        fix_long="内存使用监控，SQL 优化规范，数据倾斜治理",
        missing_info=["Spark 提交配置（内存/并行度）", "YARN 容器日志", "SQL 执行计划"],
    ),
    ErrorPattern(
        pattern=r"Partition not found|partition.*?does not exist",
        name="分区不存在",
        root_cause="查询的表分区不存在（分区未创建/被删除/分区值错误）",
        confidence="high",
        category="warehouse",
        troubleshoot=[
            "SHOW PARTITIONS <table>; 确认现有分区",
            "检查查询中的分区值是否正确（日期格式/字段名）",
            "检查上游任务是否成功生成该分区",
        ],
        deep_troubleshoot=[
            "检查分区是否被误删（生命周期管理/手动删除）",
            "检查上游任务调度是否延迟或失败",
            "HDFS 层面确认分区目录是否存在：hdfs dfs -ls <table_path>/",
        ],
        fix_immediate="修正查询分区值，或重新执行上游任务生成分区",
        fix_short="排查分区缺失原因（上游失败/被误删），修复调度或恢复数据",
        fix_long="分区存在性前置检查，上游任务依赖配置，分区生命周期管理规范",
        missing_info=["SHOW PARTITIONS 输出", "上游任务执行状态", "查询 SQL 中的分区条件"],
    ),

    # ---------- 操作系统 / 资源 ----------
    ErrorPattern(
        pattern=r"Out of memory: Kill process.*?score",
        name="Linux OOM Killer 杀进程",
        root_cause="系统内存不足，OOM Killer 根据 score 杀掉进程（通常是内存占用最大的进程）",
        confidence="high",
        category="os",
        troubleshoot=[
            "dmesg | grep -i 'killed process' 查看被 kill 的进程和内存使用",
            "free -h 查看当前内存使用",
            "检查 /var/log/messages 或 journalctl 中的 OOM 记录",
        ],
        deep_troubleshoot=[
            "分析进程内存使用趋势（是否有内存泄漏）",
            "检查是否有其他进程异常占用内存",
            "检查系统 overcommit 配置：sysctl vm.overcommit_memory",
        ],
        fix_immediate="重启被 kill 的服务，临时增加系统内存或 swap",
        fix_short="排查进程内存泄漏，修复代码或限制内存使用",
        fix_long="内存使用率告警，进程内存限制（cgroups），OOM 配置优化",
        missing_info=["dmesg OOM 完整日志", "进程内存使用趋势监控", "系统内存配置"],
    ),
    ErrorPattern(
        pattern=r"No space left on device",
        name="磁盘空间不足",
        root_cause="文件系统磁盘空间已满，无法写入数据",
        confidence="high",
        category="os",
        troubleshoot=[
            "df -h 查看各分区使用情况",
            "du -sh /* 2>/dev/null | sort -rh | head -10 定位大目录",
            "检查是否有大日志文件未轮转：ls -lhS /var/log/ | head",
        ],
        deep_troubleshoot=[
            "检查是否有进程持有已删除文件（空间未释放）：lsof | grep deleted",
            "检查 inode 是否耗尽：df -i",
            "检查是否有异常写入（日志爆量/临时文件堆积）",
        ],
        fix_immediate="清理大文件/旧日志释放空间，重启持有已删除文件的进程",
        fix_short="配置日志轮转（logrotate），清理临时文件，排查异常写入",
        fix_long="磁盘使用率告警（>85%），日志轮转规范，临时文件自动清理",
        missing_info=["df -h 输出", "大文件/大目录列表", "lsof | grep deleted 输出"],
    ),
    ErrorPattern(
        pattern=r"Too many open files|EMFILE",
        name="文件描述符耗尽",
        root_cause="进程打开的文件描述符数量达到 ulimit -n 上限，可能是连接泄漏或文件未关闭",
        confidence="high",
        category="os",
        troubleshoot=[
            "ulimit -n 查看当前限制",
            "lsof -p <pid> | wc -l 查看进程打开的文件数",
            "lsof -p <pid> | awk '{print $NF}' | sort | uniq -c | sort -rn | head 查看文件类型分布",
        ],
        deep_troubleshoot=[
            "检查是否有连接泄漏（数据库连接/网络连接未关闭）",
            "检查是否有文件句柄未关闭（日志文件/临时文件）",
            "检查进程线程数（每个线程可能占用 fd）",
        ],
        fix_immediate="临时调大 ulimit -n，重启进程释放 fd",
        fix_short="排查连接/文件泄漏，修复代码确保关闭",
        fix_long="fd 使用率监控，连接池规范，文件操作 try-with-resources 规范",
        missing_info=["ulimit -n 配置值", "lsof -p <pid> 输出", "进程连接池配置"],
    ),

    # ---------- 消息队列 ----------
    ErrorPattern(
        pattern=r"kafka[^\n]{0,60}?(?:out of available brokers|no brokers? available|broker[^\n]{0,30}?unavailable|Unable to connect to broker)",
        name="Kafka 无可用 broker",
        root_cause="Kafka 客户端找不到可用 broker，通常是 broker 宕机、网络不通、bootstrap.servers 配置错误或 ACL 限制",
        confidence="high",
        category="mq",
        troubleshoot=[
            "检查 bootstrap.servers 配置与 broker 地址可访问性",
            "查看 broker 进程状态与日志（是否宕机/重启/磁盘满）",
            "检查网络连通性：nc -zv <broker_host> <port>",
        ],
        deep_troubleshoot=[
            "查看 Kafka 集群元数据（kafka-topics --describe --bootstrap-server）确认 broker 状态",
            "检查 broker 与客户端之间的防火墙/安全组规则",
            "检查是否存在认证/ACL 错误导致 broker 拒绝连接",
        ],
        fix_immediate="确认 broker 存活并恢复网络连通，或修正 bootstrap.servers 指向可用 broker",
        fix_short="修复 broker 异常（宕机重启/磁盘清理/配置纠正），重试客户端连接",
        fix_long="为 Kafka 集群配置高可用与监控告警，客户端配置多 broker 与重试策略",
        missing_info=["bootstrap.servers 配置内容", "broker 进程状态与日志", "客户端与 broker 网络连通性"],
    ),
    ErrorPattern(
        pattern=r"RebalanceInProgressException|rebalance.*?in progress",
        name="Kafka 消费者重平衡中",
        root_cause="消费者组正在进行 Rebalance（消费者加入/退出/超时），期间消费暂停",
        confidence="medium",
        category="mq",
        troubleshoot=[
            "检查消费者是否频繁重启/崩溃",
            "检查 session.timeout.ms 和 heartbeat.interval.ms 配置",
            "检查消费者处理时间是否超过 max.poll.interval.ms",
        ],
        deep_troubleshoot=[
            "检查是否有消费者 OOM 或崩溃导致频繁退出",
            "检查消费逻辑是否有慢操作导致 poll 间隔过长",
            "检查消费者组是否有成员频繁变化",
        ],
        fix_immediate="等待 Rebalance 完成，增加 session.timeout.ms",
        fix_short="优化消费逻辑降低处理时间，修复消费者崩溃问题",
        fix_long="消费者存活监控，Rebalance 频率告警，消费超时配置规范",
        missing_info=["消费者配置（timeout 相关）", "消费者应用日志", "消费者组状态"],
    ),
    ErrorPattern(
        pattern=r"Service not available now|broker.*?not available",
        name="RocketMQ Broker 不可用",
        root_cause="RocketMQ Broker 服务不可用（正在关闭/磁盘满/异常）",
        confidence="medium",
        category="mq",
        troubleshoot=[
            "检查 Broker 进程状态：jps | grep BrokerStartup",
            "查看 Broker 日志：tail -100 ~/logs/rocketmqlogs/broker.log",
            "检查 Broker 服务器磁盘空间：df -h",
        ],
        deep_troubleshoot=[
            "检查 NameServer 中 Broker 注册状态",
            "检查是否有 Broker 磁盘满导致拒绝写入",
            "检查 Broker 配置（storePathRootDir 权限/空间）",
        ],
        fix_immediate="重启 Broker 服务，清理磁盘空间",
        fix_short="排查 Broker 不可用原因（磁盘满/配置错误/崩溃）",
        fix_long="Broker 主从部署，磁盘使用率告警，Broker 存活监控",
        missing_info=["Broker 错误日志", "磁盘空间使用情况", "NameServer 状态"],
    ),

    # ---------- 网络 / 安全 ----------
    ErrorPattern(
        pattern=r"Connection refused|connect: connection refused|ECONNREFUSED|dial tcp.*?connect: connection refused",
        name="服务连接被拒绝",
        root_cause="目标服务未运行、端口未监听或被防火墙拦截，导致 TCP 连接被拒绝。需确认目标服务状态和网络连通性",
        confidence="high",
        category="network",
        troubleshoot=[
            "确认目标服务进程是否运行：ps aux | grep <service>",
            "确认端口是否监听：ss -tlnp | grep <port> 或 netstat -tlnp | grep <port>",
            "测试网络连通性：telnet <host> <port> 或 nc -zv <host> <port>",
        ],
        deep_troubleshoot=[
            "检查目标服务是否崩溃（查看服务日志、dmesg OOM 记录）",
            "检查防火墙/安全组是否拦截该端口（iptables/云安全组）",
            "检查服务是否绑定了 127.0.0.1 而非 0.0.0.0（只能本地访问）",
            "检查容器/集群内服务发现是否正常（DNS/Service/Endpoints）",
        ],
        fix_immediate="启动目标服务，或临时放行防火墙端口",
        fix_short="排查服务崩溃原因并修复，确保持续运行和自启",
        fix_long="服务存活监控告警，端口监听检测，防火墙策略规范审查",
        missing_info=["目标服务名称和端口", "目标服务日志", "网络拓扑/防火墙配置"],
    ),
    ErrorPattern(
        pattern=r"SSLHandshakeException|certificate.*?expired|certificate.*?invalid|unable to find valid certification|PKIX path building failed|SSL.*?certificate",
        name="SSL/TLS 证书错误",
        root_cause="SSL/TLS 握手失败，可能是证书过期、证书不受信任、证书链不完整或域名不匹配",
        confidence="high",
        category="network",
        troubleshoot=[
            "检查证书有效期：openssl x509 -in cert.pem -noout -dates",
            "检查证书链是否完整：openssl verify -CAfile ca.pem cert.pem",
            "检查访问域名是否与证书 CN/SAN 匹配",
        ],
        deep_troubleshoot=[
            "检查是否使用了自签名证书且客户端未配置信任库",
            "检查证书是否已被吊销（CRL/OCSP）",
            "检查 TLS 版本和加密套件是否兼容",
            "检查是否有中间设备（防火墙/代理）替换了证书",
        ],
        fix_immediate="更换有效证书，或临时配置客户端信任自签名证书（仅限测试环境）",
        fix_short="更新过期证书，配置完整证书链，确保证书域名匹配",
        fix_long="证书过期监控告警（提前 30 天），证书自动化续期（Let's Encrypt/cert-manager），TLS 配置规范",
        missing_info=["证书文件", "证书有效期", "客户端信任库配置", "访问的域名和端口"],
    ),
    ErrorPattern(
        pattern=r"UnknownHostException|DNS.*?fail|name or service not known|could not resolve host|nodename nor servname",
        name="DNS 解析失败",
        root_cause="域名无法解析为 IP 地址，可能是 DNS 服务器故障、域名不存在、网络不通或 /etc/hosts 配置错误",
        confidence="high",
        category="network",
        troubleshoot=[
            "nslookup <domain> 或 dig <domain> 测试 DNS 解析",
            "检查 /etc/resolv.conf 中的 DNS 服务器配置",
            "ping <domain> 测试网络连通性",
        ],
        deep_troubleshoot=[
            "检查是否有 DNS 缓存污染（清除缓存后重试）",
            "检查 /etc/hosts 是否有错误配置",
            "检查容器/集群内的 DNS 服务（CoreDNS/kube-dns）状态",
            "检查是否有防火墙拦截 DNS 请求（UDP 53 端口）",
        ],
        fix_immediate="切换 DNS 服务器（如 8.8.8.8/114.114.114.114），或在 /etc/hosts 临时添加映射",
        fix_short="排查 DNS 服务器故障，修复域名配置，检查网络连通性",
        fix_long="DNS 高可用部署（主备 DNS），DNS 解析监控告警，容器集群 DNS 服务健康检查",
        missing_info=["无法解析的域名", "DNS 服务器配置", "nslookup/dig 输出", "网络拓扑"],
    ),
    ErrorPattern(
        pattern=r"401 Unauthorized|403 Forbidden|AccessDeniedException|UnauthorizedException|authentication failed|authorization failed|invalid token|token expired",
        name="认证/授权失败",
        root_cause="请求未通过认证或授权检查，可能是 Token 过期/无效、权限不足、API Key 错误或 IP 白名单限制",
        confidence="medium",
        category="security",
        troubleshoot=[
            "检查请求头中的 Token/API Key 是否正确且未过期",
            "检查当前用户/服务是否有目标资源的访问权限",
            "检查服务端认证服务（OAuth2/JWT/IAM）是否正常",
        ],
        deep_troubleshoot=[
            "检查 Token 签名密钥是否一致（多环境密钥混用）",
            "检查是否有 IP 白名单/地域限制拦截",
            "检查权限配置是否最近变更（RBAC 策略更新）",
            "检查时钟偏移导致 Token 过期判断错误",
        ],
        fix_immediate="刷新 Token 或重新生成 API Key，临时放宽权限（仅限紧急情况）",
        fix_short="排查 Token 过期原因，修复权限配置，确认认证服务正常",
        fix_long="Token 自动续期机制，权限变更审计，认证服务高可用，401/403 监控告警",
        missing_info=["请求的 Token/API Key（脱敏）", "目标资源和所需权限", "认证服务日志", "权限配置"],
    ),
    ErrorPattern(
        pattern=r"429 Too Many Requests|rate limit|RateLimitExceeded|too many requests|circuit breaker|CircuitBreakerOpenException|HystrixRuntimeException",
        name="限流/熔断触发",
        root_cause="请求被限流或熔断器打开，可能是 QPS 超过限流阈值、下游服务异常触发熔断、或突发流量超过系统承载能力",
        confidence="medium",
        category="network",
        troubleshoot=[
            "检查当前 QPS 和限流阈值配置",
            "检查下游服务是否有异常（错误率/响应时间）",
            "检查是否有突发流量或恶意请求",
        ],
        deep_troubleshoot=[
            "检查限流算法（令牌桶/漏窗/滑动窗口）和阈值是否合理",
            "检查熔断器配置（失败率阈值、恢复时间窗口）",
            "检查是否有热点接口/热点参数导致局部限流",
            "检查客户端是否有重试风暴（重试加剧限流）",
        ],
        fix_immediate="临时调大限流阈值，对非核心接口降级，客户端增加退避重试",
        fix_short="优化限流阈值配置，排查下游异常，增加缓存/削峰",
        fix_long="全局限流+局部限流分级配置，熔断降级规范，突发流量弹性扩容，限流/熔断监控告警",
        missing_info=["限流阈值配置", "当前 QPS 趋势", "下游服务状态", "熔断器配置"],
    ),

    # ---------- 容器 / 运维 ----------
    ErrorPattern(
        pattern=r"OOMKilled|oom_reaper|Killed process.*?out of memory|container.*?OOM|memory cgroup out of memory",
        name="容器 OOMKilled",
        root_cause="容器内存使用超过 cgroup limit 被 OOM Killer 终止，可能是内存泄漏、配置 limit 过小或突发大内存占用",
        confidence="high",
        category="os",
        troubleshoot=[
            "kubectl describe pod <pod> 查看 OOMKilled 事件和内存 limit",
            "查看容器内存使用历史：kubectl top pod <pod> --containers",
            "检查应用是否有内存泄漏（堆内存/堆外内存/Metaspace）",
        ],
        deep_troubleshoot=[
            "抓取 OOM 前的堆 dump（-XX:+HeapDumpOnOutOfMemoryError）",
            "检查是否有大文件/大对象加载到内存",
            "检查 JVM 内存配置（Xmx/Xms/MaxDirectMemorySize）是否超过容器 limit",
            "检查是否有 sidecar 容器共享内存 cgroup 导致总内存超限",
        ],
        fix_immediate="临时调大容器 memory limit，重启 Pod 恢复服务",
        fix_short="排查内存泄漏并修复，合理配置 JVM 内存参数（Xmx ≤ limit 的 70%）",
        fix_long="内存使用率监控告警（>80%），定期堆内存分析，容器资源规范（request/limit 合理配比），HPA 弹性扩容",
        missing_info=["容器 memory limit 配置", "OOM 前内存使用趋势", "JVM 内存参数", "堆 dump 文件"],
    ),

    # ---------- Kubernetes / 云原生 ----------
    ErrorPattern(
        pattern=r"CrashLoopBackOff|back-off.*?restarting failed container|container.*?restarted.*?times",
        name="K8s Pod CrashLoopBackOff",
        root_cause="Pod 内容器反复崩溃重启，达到 CrashLoopBackOff 状态，可能是应用启动失败、配置错误、依赖不可用或健康检查失败",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl describe pod <pod-name> -n <namespace> 查看 Events 和 Last State（退出码、退出原因）",
            "kubectl logs <pod-name> -n <namespace> --previous 查看上一次崩溃的容器日志",
            "kubectl logs <pod-name> -n <namespace> 查看当前容器启动日志",
            "检查退出码：137=OOMKilled，1=应用错误，126=命令不可执行，127=命令未找到",
        ],
        deep_troubleshoot=[
            "检查 ConfigMap/Secret 挂载是否正确：kubectl get configmap <name> -o yaml",
            "检查环境变量配置是否完整：kubectl exec <pod> -- env | grep KEY",
            "检查依赖服务（DB/Redis/MQ）是否可从 Pod 内访问：kubectl exec <pod> -- nc -zv <host> <port>",
            "检查启动命令和参数是否正确：kubectl get pod <pod> -o jsonpath='{.spec.containers[0].command}'",
            "检查是否有 init container 失败导致主容器无法启动",
        ],
        fix_immediate="根据退出码定位原因，修复配置或代码后 kubectl delete pod <pod> 触发重建",
        fix_short="修复应用启动逻辑，增加启动失败时的明确错误日志，检查配置和依赖的可用性",
        fix_long="配置启动探针（startupProbe）避免慢启动被 liveness 杀掉，增加 Pod 就绪检查，配置 PDB 保证滚动更新可用性",
        missing_info=["kubectl describe pod 输出（Events/Last State）", "--previous 崩溃日志", "Pod 的 yaml 配置（command/env/volume）", "依赖服务状态"],
    ),
    ErrorPattern(
        pattern=r"Liveness probe failed|Readiness probe failed|startup probe failed|probe.*?failed.*?timeout",
        name="K8s 健康检查探针失败",
        root_cause="Pod 的 liveness/readiness/startup 探针检测失败，可能是应用响应慢、端口错误、路径错误或资源不足",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl describe pod <pod> 查看探针失败的具体事件（超时/连接拒绝/HTTP 状态码）",
            "从 Pod 内测试探针端点：kubectl exec <pod> -- curl -v http://localhost:<port><path>",
            "检查探针配置：kubectl get pod <pod> -o jsonpath='{.spec.containers[0].livenessProbe}'",
            "确认应用监听端口与探针端口一致",
        ],
        deep_troubleshoot=[
            "检查应用是否有慢启动（需要增加 startupProbe 或 initialDelaySeconds）",
            "检查探针超时时间（timeoutSeconds）是否合理，默认 1 秒可能太短",
            "检查应用是否在处理请求时阻塞导致探针无法响应（线程池满/GC 停顿）",
            "检查探针路径是否需要认证（健康检查端点应免认证）",
            "检查是否有网络策略（NetworkPolicy）拦截探针流量",
        ],
        fix_immediate="临时调大 initialDelaySeconds/timeoutSeconds/periodSeconds，或暂时移除探针",
        fix_short="修复探针端点（确保端口/路径正确、响应时间 < 1s、免认证），合理配置探针参数",
        fix_long="配置 startupProbe（慢启动应用）+ livenessProbe（存活检测）+ readinessProbe（流量接入）三层探针体系，探针参数规范审查",
        missing_info=["探针配置详情（端口/路径/超时/间隔）", "应用启动时间", "探针端点响应时间", "Pod Events"],
    ),
    ErrorPattern(
        pattern=r"ImagePullBackOff|ErrImagePull|Failed to pull image|image.*?not found|manifest.*?not found",
        name="K8s 镜像拉取失败",
        root_cause="Kubernetes 无法拉取容器镜像，可能是镜像名称/标签错误、镜像仓库不可达、认证失败或镜像不存在",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl describe pod <pod> 查看 Events 中的镜像拉取错误详情",
            "确认镜像名称和标签正确：kubectl get pod <pod> -o jsonpath='{.spec.containers[0].image}'",
            "测试镜像仓库连通性：kubectl run test --rm -it --image=<image> --restart=Never -- /bin/sh",
            "检查 imagePullSecret 是否存在且正确：kubectl get secret <secret-name>",
        ],
        deep_troubleshoot=[
            "检查镜像标签是否被覆盖（latest 标签不稳定，建议使用固定版本或 digest）",
            "检查私有仓库认证：docker login <registry> 后手动 docker pull 测试",
            "检查集群节点是否能访问镜像仓库（网络策略/防火墙/DNS）",
            "检查镜像是否存在于仓库：docker manifest inspect <image>",
            "检查是否有镜像大小限制或仓库配额限制",
        ],
        fix_immediate="修正镜像名称/标签，或重新推送镜像到仓库",
        fix_short="配置正确的 imagePullSecret，使用固定镜像标签（避免 latest），确保镜像仓库高可用",
        fix_long="镜像仓库高可用部署，镜像标签规范（语义化版本+git commit），CI/CD 流水线镜像推送验证，镜像拉取失败监控告警",
        missing_info=["完整镜像名称和标签", "镜像仓库地址和认证配置", "kubectl describe pod Events", "镜像仓库可用性"],
    ),
    ErrorPattern(
        pattern=r"FailedMount|MountVolume.*?failed|could not mount.*?volume|PersistentVolumeClaim.*?not found|PVC.*?pending",
        name="K8s 存储卷挂载失败",
        root_cause="Pod 无法挂载 PersistentVolume，可能是 PVC 不存在、StorageClass 错误、PV 已被占用或节点无法访问存储",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl describe pod <pod> 查看 MountVolume 失败事件",
            "kubectl get pvc -n <namespace> 查看 PVC 状态（Pending/Bound/Lost）",
            "kubectl describe pvc <pvc-name> 查看 PVC 事件和 StorageClass",
            "kubectl get pv 查看 PV 状态和绑定关系",
        ],
        deep_troubleshoot=[
            "检查 StorageClass 是否存在且可用：kubectl get storageclass",
            "检查 PV 访问模式（ReadWriteOnce/ReadWriteMany）是否与 Pod 调度兼容",
            "检查节点是否能访问存储后端（NFS/iSCSI/Ceph/云盘）",
            "检查 PVC 是否被其他 Pod 独占（RWO 模式不能多 Pod 挂载）",
            "检查存储配额（ResourceQuota）是否超限",
        ],
        fix_immediate="创建缺失的 PVC，或修正 StorageClass/访问模式配置",
        fix_short="排查存储后端连通性，修复 PV/PVC 配置，确保存储资源充足",
        fix_long="存储容量监控告警，PV/PVC 配置规范，StorageClass 高可用，存储备份策略",
        missing_info=["PVC 名称和状态", "StorageClass 配置", "PV 状态和绑定关系", "存储后端类型和状态"],
    ),
    ErrorPattern(
        pattern=r"503 Service Unavailable|upstream connect error|connection reset|no healthy upstream|UH|no_healthy_upstream",
        name="Service Mesh / 网关 503 无健康后端",
        root_cause="服务网格（Istio/Envoy）或网关（Nginx/APISIX）返回 503，没有健康的上游后端实例，可能是 Pod 未就绪、全部实例崩溃或流量策略错误",
        confidence="medium",
        category="k8s",
        troubleshoot=[
            "kubectl get pods -n <namespace> 查看 Pod 状态（Running/Ready 列）",
            "kubectl get endpoints <service-name> -n <namespace> 查看 Service 关联的后端 IP 列表",
            "kubectl describe service <service-name> 查看 selector 是否匹配 Pod 标签",
            "检查 Pod readinessProbe 是否通过（未就绪的 Pod 不会加入 endpoints）",
        ],
        deep_troubleshoot=[
            "检查 Istio VirtualService/DestinationRule 路由配置是否正确",
            "检查是否有流量策略（如 100% 切到 v2 但 v2 未部署）",
            "检查 mTLS 配置是否导致服务间通信失败",
            "检查 Envoy sidecar 是否正常注入和运行：kubectl get pod <pod> -o jsonpath='{.spec.containers[*].name}'",
            "检查是否有 CircuitBreaker 配置触发了熔断（outlierDetection）",
        ],
        fix_immediate="检查并恢复 Pod 就绪状态，修正 Service selector 或流量策略",
        fix_short="排查 Pod 未就绪原因，修复 VirtualService/DestinationRule 配置，确保 mTLS 兼容",
        fix_long="服务网格配置规范审查，流量灰度发布策略，503 监控告警，多可用区部署保证高可用",
        missing_info=["Pod 状态和 Ready 列", "Service endpoints 列表", "VirtualService/DestinationRule 配置", "Envoy access log"],
    ),
    ErrorPattern(
        pattern=r"Throttling|Rate exceeded|Quota exceeded|TooManyRequests|429.*?cloud|RequestLimitExceeded",
        name="云服务 API 限流/配额超限",
        root_cause="调用云服务 API（AWS/Azure/GCP/阿里云等）触发限流或配额超限，可能是调用频率过高、配额不足或突发流量",
        confidence="medium",
        category="cloud",
        troubleshoot=[
            "查看错误信息中的服务名、API 名称和限制值（如 Rate=5/s, Burst=10）",
            "检查云服务控制台的配额使用情况和限流配置",
            "检查应用是否有重试风暴（失败重试加剧限流）",
            "统计该 API 的调用频率和并发量",
        ],
        deep_troubleshoot=[
            "检查是否有定时任务/批处理在短时间内大量调用 API",
            "检查是否有缓存失效导致回源流量激增",
            "检查云服务是否有突发限制（如 AWS API 请求速率限制）",
            "检查是否有多个应用共享同一配额导致超限",
            "检查是否有死循环或异常逻辑导致重复调用",
        ],
        fix_immediate="增加调用间隔/退避重试，临时降低调用频率，或申请临时提升配额",
        fix_short="增加本地缓存减少 API 调用，实现指数退避重试，合理配置并发数",
        fix_long="云服务配额规划和监控，API 调用缓存策略，限流降级机制，配额使用率告警（>80%）",
        missing_info=["云服务名称和 API 名称", "限流/配额的具体限制值", "API 调用频率统计", "云服务控制台配额配置"],
    ),
    ErrorPattern(
        pattern=r"exec.*?format error|exec user process caused|no such file or directory.*?exec|standard_init_linux.*?exec",
        name="容器启动命令执行失败",
        root_cause="容器启动时执行入口命令失败，可能是命令不存在、脚本无执行权限、换行符问题（CRLF）或架构不兼容（arm64/amd64）",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl logs <pod> 查看启动错误（通常包含 exec format error 或 no such file）",
            "检查容器入口命令：kubectl get pod <pod> -o jsonpath='{.spec.containers[0].command}{.spec.containers[0].args}'",
            "检查镜像默认 CMD/ENTRYPOINT：docker inspect <image> | grep -A2 Cmd",
            "本地测试镜像启动：docker run --rm <image> 查看是否能正常启动",
        ],
        deep_troubleshoot=[
            "检查脚本文件是否有执行权限：kubectl exec <pod> -- ls -l /path/to/script.sh",
            "检查脚本换行符是否为 CRLF（Windows 构建导致）：file script.sh 应显示 'with CRLF line terminators' 则有问题",
            "检查镜像架构与节点架构是否匹配：docker inspect <image> | grep Architecture，kubectl get node -o jsonpath='{.items[*].status.nodeInfo.architecture}'",
            "检查 shebang 行是否正确（#!/bin/bash 而非 #!/bin/bash^M）",
            "检查命令路径是否在容器内存在：kubectl debug <pod> -- ls /usr/local/bin/",
        ],
        fix_immediate="修正入口命令路径或权限，重新构建镜像",
        fix_short="修复脚本换行符（LF）、执行权限（chmod +x）、shebang 行，确保镜像架构与节点匹配",
        fix_long="Dockerfile 规范审查（ENTRYPOINT/CMD、WORKDIR、USER），多架构镜像构建（buildx），CI 流水线镜像启动测试",
        missing_info=["容器入口命令和参数", "镜像 Dockerfile", "节点架构", "完整启动错误日志"],
    ),
    ErrorPattern(
        pattern=r"FailedScheduling|0/\\d+ nodes are available|Insufficient (cpu|memory|ephemeral-storage)|node(s) didn't match|node(s) had taint",
        name="K8s Pod 调度失败",
        root_cause="Pod 无法被调度到任何节点，可能是节点资源不足（CPU/内存/存储）、节点选择器不匹配、Taint/Toleration 不兼容、节点不可用或 Pod 反亲和性冲突",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl describe pod <pod> -n <namespace> 查看 FailedScheduling 事件中的具体原因",
            "kubectl get nodes 查看节点状态（Ready/NotReady）和资源使用情况",
            "kubectl describe node <node> 查看节点资源容量、已分配资源、Taint 列表",
            "检查 Pod 的 nodeSelector/nodeAffinity 是否与节点标签匹配：kubectl get pod <pod> -o jsonpath='{.spec.nodeSelector}'",
        ],
        deep_troubleshoot=[
            "检查节点资源是否真的不足：kubectl describe node <node> | grep -A5 'Allocated resources'",
            "检查 Taint/Toleration：kubectl describe node <node> | grep Taints，对比 Pod 的 tolerations",
            "检查 Pod 反亲和性是否导致无法调度：kubectl get pod <pod> -o jsonpath='{.spec.affinity}'",
            "检查是否有节点被 cordon/drain：kubectl get nodes -o jsonpath='{.items[*].spec.unschedulable}'",
            "检查资源配额（ResourceQuota）是否限制了命名空间的资源使用：kubectl get resourcequota -n <namespace>",
        ],
        fix_immediate="临时调大节点资源或减少 Pod 资源请求，或添加 Toleration 匹配节点 Taint",
        fix_short="合理配置 Pod 的 resources.requests/limits，调整 nodeSelector/affinity，确保节点资源充足",
        fix_long="集群容量规划和自动扩缩容（Cluster Autoscaler），节点标签和 Taint 规范，资源请求配置审查，调度失败监控告警",
        missing_info=["kubectl describe pod 的 FailedScheduling 事件", "节点资源使用情况", "Pod 的 nodeSelector/affinity/tolerations 配置", "节点 Taint 列表"],
    ),
    ErrorPattern(
        pattern=r"failed to sync configmap|configmap.*?not found|configmap.*?update failed|secret.*?not found|failed to sync secret|volume.*?configmap.*?not found",
        name="K8s ConfigMap/Secret 挂载或热更新失败",
        root_cause="Pod 挂载的 ConfigMap 或 Secret 不存在、权限不足或热更新失败，可能导致应用配置缺失或配置不生效",
        confidence="medium",
        category="k8s",
        troubleshoot=[
            "kubectl get configmap <name> -n <namespace> 确认 ConfigMap 是否存在",
            "kubectl get secret <name> -n <namespace> 确认 Secret 是否存在",
            "kubectl describe pod <pod> 查看 volume 挂载错误事件",
            "检查 Pod 的 volumeMounts 配置是否正确：kubectl get pod <pod> -o jsonpath='{.spec.volumes}'",
        ],
        deep_troubleshoot=[
            "检查 ConfigMap/Secret 是否在正确的命名空间（跨命名空间不能挂载）",
            "检查 RBAC 权限是否允许 Pod 读取 ConfigMap/Secret",
            "检查 ConfigMap 热更新是否生效：kubectl exec <pod> -- cat /etc/config/<file> 对比最新配置",
            "检查是否有 subPath 挂载导致热更新不生效（subPath 挂载的 ConfigMap 不会自动更新）",
            "检查 ConfigMap 数据量是否超过 1MB 限制（etcd 限制）",
        ],
        fix_immediate="创建缺失的 ConfigMap/Secret，或修正 volumeMounts 配置",
        fix_short="确保 ConfigMap/Secret 在正确命名空间，配置 RBAC 权限，避免 subPath 挂载需要热更新的配置",
        fix_long="配置管理规范（ConfigMap/Secret 命名、命名空间、权限），配置变更验证流程，ConfigMap 热更新监控，敏感信息使用 Secret 而非 ConfigMap",
        missing_info=["ConfigMap/Secret 名称和命名空间", "Pod 的 volume/volumeMounts 配置", "RBAC 权限配置", "完整挂载错误事件"],
    ),
    ErrorPattern(
        pattern=r"failed to get cpu utilization|failed to get memory utilization|HPA.*?unable to get metrics|horizontalpodautoscaler.*?failed|unable to read metrics",
        name="K8s HPA 自动扩缩容异常",
        root_cause="Horizontal Pod Autoscaler 无法获取指标或扩缩容异常，可能是 metrics-server 未安装、指标不可用、目标资源未设置 request 或 HPA 配置错误",
        confidence="medium",
        category="k8s",
        troubleshoot=[
            "kubectl get hpa -n <namespace> 查看 HPA 状态（TARGETS 列是否显示 unknown）",
            "kubectl describe hpa <name> -n <namespace> 查看 HPA 事件和警告",
            "kubectl top pods -n <namespace> 验证 metrics-server 是否正常工作",
            "检查目标 Deployment 的 resources.requests 是否配置（HPA 需要 request 才能计算利用率）",
        ],
        deep_troubleshoot=[
            "检查 metrics-server 是否运行：kubectl get pods -n kube-system | grep metrics-server",
            "检查 metrics-server 日志：kubectl logs -n kube-system <metrics-server-pod>",
            "检查 HPA 的 metrics 配置是否正确（Resource/External/Pods/Object 类型）",
            "检查是否有 API 聚合层问题：kubectl get apiservices | grep metrics",
            "检查 HPA 的 minReplicas/maxReplicas 和 targetCPUUtilizationPercentage 配置",
        ],
        fix_immediate="安装或修复 metrics-server，或为目标资源配置 resources.requests",
        fix_short="修复 HPA 配置，确保 metrics-server 正常运行，目标资源配置 request",
        fix_long="HPA 配置规范（指标类型、阈值、扩缩容冷却时间），metrics-server 高可用部署，扩缩容效果监控，自定义指标（Prometheus Adapter）支持",
        missing_info=["HPA 名称和配置", "metrics-server 状态", "目标资源的 resources.requests 配置", "HPA 事件和警告"],
    ),
    ErrorPattern(
        pattern=r"NotReady|node.*?not ready|node.*?ready status unknown|kubelet.*?stopped|container runtime is down",
        name="K8s 节点 NotReady",
        root_cause="Kubernetes 节点状态变为 NotReady，可能是 kubelet 停止、容器运行时故障、节点网络问题、资源耗尽或节点重启",
        confidence="high",
        category="k8s",
        troubleshoot=[
            "kubectl get nodes 查看哪些节点处于 NotReady 状态",
            "kubectl describe node <node> 查看节点 Conditions 和事件",
            "登录节点检查 kubelet 状态：systemctl status kubelet",
            "登录节点检查容器运行时状态：systemctl status containerd 或 systemctl status docker",
        ],
        deep_troubleshoot=[
            "检查 kubelet 日志：journalctl -u kubelet -n 100 --no-pager",
            "检查容器运行时日志：journalctl -u containerd -n 100",
            "检查节点资源是否耗尽：free -h、df -h、uptime",
            "检查节点网络连通性：ping <api-server-ip>、curl -k https://<api-server>:6443/healthz",
            "检查节点是否有内核 panic 或 OOM：dmesg | tail -50",
            "检查节点时间是否同步（时间偏移会导致 kubelet 无法认证）：timedatectl",
        ],
        fix_immediate="重启 kubelet 或容器运行时，恢复节点 Ready 状态",
        fix_short="排查 kubelet/容器运行时故障原因，修复节点资源或网络问题",
        fix_long="节点健康监控告警（NotReady 检测），kubelet/容器运行时高可用配置，节点资源容量规划，节点自动修复（Node Problem Detector + 自动重启）",
        missing_info=["节点名称和状态", "kubectl describe node 输出", "kubelet/容器运行时日志", "节点资源和网络状态"],
    ),
    ErrorPattern(
        pattern=r"context deadline exceeded|i/o timeout|TLS handshake timeout|connection refused.*?api|x509.*?certificate.*?expired|certificate has expired",
        name="K8s API Server 连接异常",
        root_cause="Kubernetes API Server 连接超时、TLS 握手失败或证书过期，可能是 API Server 过载、网络问题、证书过期或 etcd 故障",
        confidence="medium",
        category="k8s",
        troubleshoot=[
            "kubectl get nodes --v=6 查看 API Server 连接详情和错误",
            "检查 API Server 状态：kubectl get pods -n kube-system | grep kube-apiserver",
            "检查 API Server 证书有效期：openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -dates",
            "测试 API Server 连通性：curl -k https://<api-server>:6443/healthz",
        ],
        deep_troubleshoot=[
            "检查 API Server 日志：kubectl logs -n kube-system <apiserver-pod> --tail=100",
            "检查 etcd 状态和性能：kubectl get pods -n kube-system | grep etcd，etcdctl endpoint health",
            "检查 API Server 资源使用是否过载（CPU/内存/请求数）",
            "检查所有证书是否过期：kubeadm certs check-expiration",
            "检查网络策略或防火墙是否拦截 API Server 流量（6443 端口）",
            "检查 kube-proxy 是否正常工作：kubectl get pods -n kube-system | grep kube-proxy",
        ],
        fix_immediate="重启 API Server 或更新过期证书，恢复 API Server 可用性",
        fix_short="排查 API Server 过载原因，修复网络或 etcd 问题，更新即将过期的证书",
        fix_long="API Server 高可用部署（多副本+负载均衡），证书自动续期（cert-manager），API Server 性能监控和限流，etcd 高可用和备份",
        missing_info=["API Server 状态和日志", "证书有效期", "etcd 状态", "网络连通性测试结果"],
    ),

    # ---------- Spring Cloud / 微服务 ----------
    ErrorPattern(
        pattern=r"FeignException|feign.*?RetryableException|feign.*?timeout|Read timed out.*?feign|connect timed out.*?feign",
        name="Feign 远程调用失败",
        root_cause="Spring Cloud Feign 客户端远程调用失败，可能是目标服务不可用、网络超时、连接被拒绝或服务降级",
        confidence="high",
        category="microservice",
        troubleshoot=[
            "查看 Feign 异常中的目标服务名和接口路径（通常在异常消息中）",
            "检查目标服务是否健康：访问目标服务的 /actuator/health 端点",
            "检查服务注册中心（Eureka/Nacos）中目标服务实例是否在线",
            "检查 Feign 超时配置：ribbon.ReadTimeout / ribbon.ConnectTimeout（默认 1s/5s 可能太短）",
        ],
        deep_troubleshoot=[
            "使用链路追踪（Sleuth/Zipkin/SkyWalking）查看调用链耗时分布",
            "检查目标服务是否有慢查询或资源瓶颈导致响应超时",
            "检查是否有网络分区或防火墙拦截服务间通信",
            "检查 Feign 客户端是否配置了正确的 URL 或服务名（拼写错误）",
            "检查是否有 Hystrix/Sentinel 熔断导致调用快速失败",
            "检查负载均衡器（Ribbon/LoadBalancer）是否选择了不健康的实例",
        ],
        fix_immediate="临时调大 Feign 超时时间，或重启目标服务恢复可用性",
        fix_short="排查目标服务慢响应原因，合理配置 Feign 超时（根据接口 P99 响应时间设置），配置重试策略",
        fix_long="服务间调用超时规范（连接超时/读取超时/重试次数），链路追踪全覆盖，服务健康检查和自动摘除，熔断降级机制完善",
        missing_info=["Feign 目标服务名和接口路径", "完整 Feign 异常堆栈", "服务注册中心实例状态", "Feign 超时配置"],
    ),
    ErrorPattern(
        pattern=r"HystrixRuntimeException|Hystrix.*?timeout|circuit.*?open|fallback.*?failed|fallback.*?execution.*?exception",
        name="Hystrix 熔断降级失败",
        root_cause="Hystrix 熔断器打开或 fallback 执行失败，可能是下游服务持续超时/失败触发熔断，或 fallback 方法本身异常",
        confidence="high",
        category="microservice",
        troubleshoot=[
            "查看 Hystrix 异常中的 command 名称（对应下游服务接口）",
            "检查熔断器状态：访问 /actuator/hystrix.stream 或 Hystrix Dashboard",
            "检查下游服务是否持续超时或失败（熔断触发原因）",
            "检查 fallback 方法是否存在且签名正确（@HystrixCommand fallbackMethod）",
        ],
        deep_troubleshoot=[
            "检查 Hystrix 配置：execution.isolation.thread.timeoutInMilliseconds（默认 1s）、circuitBreaker.requestVolumeThreshold、circuitBreaker.errorThresholdPercentage",
            "检查是否有线程池隔离导致线程池满（HystrixThreadPoolSize 默认 10）",
            "检查 fallback 方法是否调用了其他远程服务导致级联失败",
            "检查下游服务的错误率和响应时间趋势（熔断触发前是否有异常）",
            "检查是否有信号量隔离（semaphore）导致并发数限制",
        ],
        fix_immediate="临时关闭熔断器（hystrix.command.default.circuitBreaker.forceClosed=true）或调大超时，恢复服务调用",
        fix_short="排查下游服务持续失败原因，修复 fallback 方法异常，合理配置 Hystrix 超时和熔断阈值",
        fix_long="熔断降级规范（超时/阈值/线程池大小），fallback 方法最佳实践（不调用远程服务、返回默认值/缓存），熔断监控告警，服务隔离策略",
        missing_info=["Hystrix command 名称", "熔断器状态和 metrics", "下游服务健康状态", "Hystrix 配置和 fallback 方法代码"],
    ),
    ErrorPattern(
        pattern=r"BlockException|Sentinel.*?block|Sentinel.*?degrade|Sentinel.*?flow|flow.*?limit|param.*?flow",
        name="Sentinel 限流降级",
        root_cause="Sentinel 触发限流或降级，可能是 QPS 超过流控阈值、响应时间超过慢调用比例阈值、或异常比例超过熔断阈值",
        confidence="high",
        category="microservice",
        troubleshoot=[
            "查看 BlockException 类型（FlowException/DegradeException/ParamFlowException/SystemBlockException）",
            "检查 Sentinel 控制台中的规则配置（流控规则/降级规则/热点规则）",
            "检查当前 QPS 和响应时间是否超过阈值",
            "检查是否有突发流量或下游服务变慢导致触发",
        ],
        deep_troubleshoot=[
            "检查流控阈值设置是否合理（根据服务容量和 P99 响应时间）",
            "检查降级策略：慢调用比例（RT 阈值/比例/时间窗口）、异常比例、异常数",
            "检查是否有热点参数限流导致特定用户/商品被限",
            "检查系统规则（load/CPU/线程数/入口 QPS）是否触发系统保护",
            "检查 Sentinel 数据源配置（Nacos/Apollo）是否有规则推送异常",
        ],
        fix_immediate="临时调大流控阈值或关闭规则，恢复服务可用性",
        fix_short="排查流量突增或服务变慢原因，根据服务容量合理配置流控和降级阈值",
        fix_long="限流降级规范（阈值评估方法/灰度发布/规则版本管理），Sentinel 监控大盘，流量控制策略（匀速排队/预热），降级 fallback 最佳实践",
        missing_info=["BlockException 具体类型", "Sentinel 规则配置", "当前 QPS/响应时间/异常率", "Sentinel 控制台 metrics"],
    ),
    ErrorPattern(
        pattern=r"No instances available|LoadBalancerException|Ribbon.*?NoServerAvailable|zone.*?affinity.*?failed|no available server",
        name="负载均衡无可用实例",
        root_cause="Ribbon/Spring Cloud LoadBalancer 找不到可用的服务实例，可能是服务未注册、所有实例不健康、或区域亲和性过滤后无实例",
        confidence="high",
        category="microservice",
        troubleshoot=[
            "查看异常中的目标服务名",
            "检查服务注册中心（Eureka/Nacos/Consul）中该服务的实例列表",
            "检查实例健康状态（UP/DOWN/OUT_OF_SERVICE）",
            "检查是否有区域/可用区亲和性配置导致过滤后无实例",
        ],
        deep_troubleshoot=[
            "检查服务提供者是否正常启动并注册（查看启动日志中的注册信息）",
            "检查服务注册中心是否有网络分区或数据不一致",
            "检查 IP 配置是否正确（eureka.instance.ip-address / spring.cloud.nacos.discovery.ip）",
            "检查是否有元数据过滤（metadata-based routing）导致实例被过滤",
            "检查负载均衡器配置（NIWSServerListFilter / ZonePreferenceServerListFilter）",
            "检查服务注册中心的保护模式（Eureka self-preservation）是否导致过期实例未摘除",
        ],
        fix_immediate="重启服务提供者重新注册，或临时移除区域亲和性配置",
        fix_short="排查服务注册失败原因，确保所有实例健康注册，合理配置区域亲和性",
        fix_long="服务注册发现规范（健康检查/心跳/摘除策略），多区域部署高可用，负载均衡配置审查，服务实例监控告警",
        missing_info=["目标服务名", "服务注册中心实例列表和状态", "负载均衡器配置", "区域/元数据过滤配置"],
    ),
    ErrorPattern(
        pattern=r"DiscoveryClient.*?not available|Eureka.*?unavailable|Nacos.*?connect.*?failed|service.*?registry.*?failed|registration.*?failed",
        name="服务注册发现失败",
        root_cause="服务无法注册到注册中心或无法从注册中心获取服务列表，可能是注册中心不可达、认证失败、网络问题或配置错误",
        confidence="medium",
        category="microservice",
        troubleshoot=[
            "查看异常中的注册中心类型和地址（Eureka/Nacos/Consul）",
            "测试注册中心连通性：curl http://<eureka-server>/eureka/apps 或 nacos 控制台",
            "检查注册中心地址配置（eureka.client.serviceUrl.defaultZone / spring.cloud.nacos.discovery.server-addr）",
            "检查注册中心是否需要认证（用户名/密码/命名空间）",
        ],
        deep_troubleshoot=[
            "检查注册中心集群状态（Eureka 副本同步 / Nacos 集群节点状态）",
            "检查网络策略或防火墙是否拦截注册中心端口（Eureka 8761 / Nacos 8848）",
            "检查服务注册的元数据是否正确（应用名/IP/端口/健康检查URL）",
            "检查是否有 DNS 解析问题导致注册中心域名无法解析",
            "检查注册中心是否有资源瓶颈（CPU/内存/连接数）导致响应慢",
            "检查 Spring Cloud 版本兼容性（Finchley/Greenwich/Hoxton 与 Eureka/Nacos 版本匹配）",
        ],
        fix_immediate="临时使用直连方式（@FeignClient url）或修复注册中心连通性",
        fix_short="排查注册中心不可达原因，修复网络/认证/配置问题，确保服务正常注册",
        fix_long="注册中心高可用部署（多节点集群），服务注册发现监控（注册成功率/心跳异常），注册中心备份和灾备，版本兼容性管理",
        missing_info=["注册中心类型和地址", "完整异常堆栈", "注册中心集群状态", "服务注册配置"],
    ),
    ErrorPattern(
        pattern=r"503.*?gateway|Gateway.*?not found|NoRouteException|NotFound.*?route|service.*?not found.*?gateway|Unable to find instance",
        name="Spring Cloud Gateway 路由失败",
        root_cause="Spring Cloud Gateway 无法路由请求，可能是路由规则不匹配、目标服务不存在、负载均衡无可用实例或过滤器异常",
        confidence="medium",
        category="microservice",
        troubleshoot=[
            "查看异常中的请求路径和目标服务名",
            "检查 Gateway 路由配置（spring.cloud.gateway.routes）是否匹配该请求路径",
            "检查目标服务是否在注册中心有健康实例",
            "访问 Gateway 的 /actuator/gateway/routes 查看已加载的路由规则",
        ],
        deep_troubleshoot=[
            "检查路由谓词（Predicate）配置是否正确（Path/Host/Header/Method 等）",
            "检查路由过滤器（Filter）是否有异常（StripPrefix/AddRequestHeader/RewritePath 等）",
            "检查负载均衡器配置（lb:// 协议是否正确，服务名是否拼写正确）",
            "检查是否有跨域配置（CORS）导致请求被拦截",
            "检查 Gateway 的限流过滤器（RequestRateLimiter）是否触发限流",
            "检查是否有自定义全局过滤器（GlobalFilter）抛出异常",
            "检查 Netty 服务器配置（连接超时/响应超时）是否合理",
        ],
        fix_immediate="临时添加直连路由（http:// 而非 lb://）或修复路由配置",
        fix_short="排查路由规则不匹配或目标服务不可用原因，修复 Gateway 配置",
        fix_long="Gateway 路由配置规范（命名/谓词/过滤器/优先级），路由变更灰度发布，Gateway 监控大盘（路由命中率/延迟/错误率），限流降级策略",
        missing_info=["请求路径和目标服务名", "Gateway 路由配置", "已加载路由列表（/actuator/gateway/routes）", "完整异常堆栈"],
    ),

    # ---------- 中文告警文本 ----------
    ErrorPattern(
        pattern=r"CPU使用率.*?(超过|告警|阈值)|内存使用率.*?(超过|告警|阈值)|磁盘.*?(不足|满了|告警)|"
                r"服务.*?不可用|接口.*?响应超时|数据库连接.*?(满|耗尽)|消息.*?堆积|"
                r"网络.*?(延迟|丢包|不通)|线程池.*?(满|耗尽)|连接数.*?(超过|满)|"
                r"告警.*?(触发|恢复)|【告警】|【警告】|【严重】|【紧急】",
        name="中文告警文本",
        root_cause="中文运维告警文本，可能是资源使用率超阈值、服务不可用、接口超时、中间件异常或系统级告警",
        confidence="medium",
        category="alert",
        troubleshoot=[
            "查看告警文本中的具体指标和阈值（如 CPU 使用率 95% 超过阈值 80%）",
            "查看告警中的服务名/主机名/IP，定位到具体资源",
            "查看告警级别（紧急/严重/警告/提示）和触发时间",
            "登录对应主机或服务控制台查看实时指标",
        ],
        deep_troubleshoot=[
            "资源类告警：查看 top/free/df/iostat 等命令确认资源使用情况，排查高资源进程",
            "服务类告警：查看服务日志和健康检查端点，确认服务是否真的不可用",
            "接口类告警：查看接口响应时间分布和错误率，排查下游依赖和慢查询",
            "中间件告警：查看数据库连接池/Redis 内存/MQ 堆积量等具体指标",
            "网络类告警：查看 ping/traceroute/mtr 确认网络质量，排查带宽和丢包",
            "检查是否有告警风暴（同一时间大量告警可能是根因故障引发的连锁反应）",
            "查看告警历史，确认是首次触发还是反复触发（反复触发可能需要长期优化）",
        ],
        fix_immediate="根据告警类型采取对应应急措施：资源不足扩容/重启，服务不可用切换流量，接口超时降级",
        fix_short="排查告警根因并修复，调整告警阈值避免误报，优化资源配置或服务性能",
        fix_long="建立告警分级和响应流程，告警阈值动态调优，资源容量规划和自动扩缩容，告警收敛和降噪",
        missing_info=["告警中的具体指标值和阈值", "告警对应的服务/主机标识", "告警级别和触发时间", "相关服务的实时监控数据"],
    ),
    ErrorPattern(
        pattern=r"(?:【告警】.*?)?(?:接口.*?(?:超时|响应慢|失败|返回\d{3})|服务.*?(?:不可用|异常)|缓存.*?不可用|响应.*?超时|调用.*?失败)",
        name="中文日志/告警：接口或服务异常",
        root_cause="日志或告警明确指向接口超时/报错或服务不可用，优先检查目标服务状态、下游依赖性能与网络链路（区别于资源类告警的泛化推测）",
        confidence="high",
        category="alert",
        troubleshoot=[
            "查看告警中的服务名和接口路径，定位故障服务",
            "检查该服务负载/慢查询/GC，确认响应变慢原因",
            "检查下游依赖与网络链路：ping/curl 目标服务",
        ],
        deep_troubleshoot=[
            "APM 链路追踪定位超时环节（调用链上哪一跳耗时最长）",
            "查看服务日志中同期错误，确认是否由上游故障引发",
            "检查网关/负载均衡是否有超时配置或实例摘除",
        ],
        fix_immediate="对告警接口执行降级/重试/切换实例，先恢复可用性",
        fix_short="定位并修复超时根因（慢查询/下游慢/资源不足），优化接口性能",
        fix_long="配置接口超时分级告警与自动降级策略，建立容量与性能基线",
        missing_info=["告警涉及的服务名与接口路径", "接口响应时间分布", "同期服务日志与链路追踪"],
    ),

    # ---------- 日常开发高频报错（方向A扩展） ----------
    ErrorPattern(
        pattern=r"ModuleNotFoundError: No module named\s+[\'\"\\]*([a-zA-Z_][a-zA-Z0-9_]*)",
        name="Python 模块未安装（ModuleNotFoundError）",
        root_cause="Python 缺少第三方库：运行环境中没有安装你 import 的模块。人话翻译：你代码里用了某个库，但电脑上还没装。",
        confidence="high",
        category="python",
        troubleshoot=[
            "先看报错里的模块名（No module named 后面那个）",
            "用 pip install 安装对应模块：pip install 模块名",
            "如果装了还报错，检查是不是装到了别的 Python 环境（conda/venv/多版本 Python）",
            "确认 pip 对应的是当前运行的 Python：python -m pip install 模块名",
        ],
        deep_troubleshoot=[
            "which python 确认当前 Python 路径",
            "pip list 看当前环境已装的包",
            "如果是虚拟环境没激活，source venv/bin/activate 或 venv/Scripts/activate",
            "conda 环境的话用 conda install 或 pip install 都可以，但注意环境一致性",
        ],
        fix_immediate="pip install 报错中提到的模块名（或 python -m pip install 模块名）",
        fix_short="把依赖写入 requirements.txt 或 pyproject.toml，团队统一环境",
        fix_long="使用虚拟环境（venv/conda）隔离项目依赖，避免全局环境污染",
        missing_info=["报错中提到的模块名", "使用的 Python 版本", "是否在虚拟环境中运行"],
    ),
    ErrorPattern(
        pattern=r"(?i)npm (ERR|error|warn)",
        name="npm 包安装/运行报错",
        root_cause="npm 在安装依赖或运行脚本时出错。常见原因：网络问题、权限不足、Node 版本不兼容、package.json 配置错误。",
        confidence="medium",
        category="network",
        troubleshoot=[
            "看 npm ERR! 下面的完整错误信息（404/网络超时/EACCES权限）",
            "如果是网络超时：切换淘宝镜像 npm config set registry https://registry.npmmirror.com",
            "如果是 EACCES 权限：不要加 sudo，改用 nvm 管理 Node 版本",
            "如果是 Node 版本不兼容：用 nvm 切换到项目要求的 Node 版本",
            "删除 node_modules 和 package-lock.json 后重装：rm -rf node_modules && npm install",
        ],
        deep_troubleshoot=[
            "npm config list 查看当前镜像源",
            "node -v 和 npm -v 确认版本",
            "检查 package.json 中的 engines 字段要求的 Node 版本",
            "如果是原生模块编译失败，检查 python 和 build tools 是否安装",
        ],
        fix_immediate="先看完整错误信息定位类型，常见是网络问题，换镜像源重试",
        fix_short="团队统一 Node 版本和 npm 版本（.nvmrc 文件）",
        fix_long="使用 pnpm 或 yarn 替代 npm，减少依赖冲突和安装时间",
        missing_info=["完整的 npm ERR! 错误信息", "Node 版本（node -v）", "操作系统"],
    ),
    ErrorPattern(
        pattern=r"(?i)(EACCES|Permission denied|permission denied|权限不够|PermissionError)",
        name="权限不足（Permission denied / EACCES）",
        root_cause="当前用户没有权限读写目标文件/目录/端口。人话翻译：你想操作的东西不在你的权限范围内。",
        confidence="high",
        category="os",
        troubleshoot=[
            "看报错里具体是哪个路径/端口没权限",
            "文件权限：ls -l 看权限位，chmod 修改（Linux/Mac）",
            "端口被占用：lsof -i:端口号 或 netstat -ano | findstr 端口号（Windows）",
            "npm 全局安装报 EACCES：不要 sudo，用 nvm 或配置 prefix 到用户目录",
            "Docker 权限：Linux 下把用户加入 docker 组，或 sudo",
        ],
        deep_troubleshoot=[
            "Linux: ls -la 文件路径 查看所有者和权限",
            "Linux: chown -R $USER:$USER 文件路径 修改所有者",
            "Windows: 用管理员身份运行终端（右键→以管理员身份运行）",
            "端口被占用：杀掉占用进程或换端口",
        ],
        fix_immediate="Linux/Mac 加 sudo（临时），Windows 用管理员身份运行终端",
        fix_short="修改文件/目录所有者，避免每次加 sudo",
        fix_long="遵循最小权限原则，配置用户目录下的开发环境，不用 root 跑开发",
        missing_info=["报错的完整路径或端口号", "操作系统（Linux/Mac/Windows）", "执行的具体命令"],
    ),
    ErrorPattern(
        pattern=r"(?i)(merge conflict|CONFLICT|冲突|Automatic merge failed)",
        name="Git 合并冲突",
        root_cause="Git 合并分支时，两个分支修改了同一文件的同一位置，Git 不知道该保留哪个版本。",
        confidence="high",
        category="network",
        troubleshoot=[
            "git status 看哪些文件冲突了（both modified）",
            "打开冲突文件，搜索 <<<<<<< 和 >>>>>>> 找到冲突位置",
            "手动编辑：保留需要的代码，删除 <<<<<<< ======= >>>>>>> 标记",
            "编辑完后 git add 冲突文件，然后 git commit 完成合并",
            "如果搞不清怎么合并，git merge --abort 回到合并前状态",
        ],
        deep_troubleshoot=[
            "git diff 当前分支名 看具体改了什么",
            "用 VS Code 的合并工具（源代码管理→合并更改）可视化解决",
            "多人协作时尽量小步提交、频繁推送，减少冲突范围",
        ],
        fix_immediate="打开冲突文件，手动选择保留的代码，删除冲突标记，git add && git commit",
        fix_short="团队约定代码 owner，减少同一文件多人同时修改",
        fix_long="使用代码评审流程，合并前先拉取最新主分支解决冲突",
        missing_info=["冲突的文件名", "两个分支各自改了什么（可选）"],
    ),
    ErrorPattern(
        pattern=r"SyntaxError: (invalid syntax|missing|unexpected|EOL)",
        name="Python 语法错误（SyntaxError）",
        root_cause="Python 代码写法不合法。人话翻译：你的代码有语法问题，Python 解释器读不懂。",
        confidence="high",
        category="python",
        troubleshoot=[
            "看报错里的行号（File 引号内文件名, line N），跳到那一行",
            "常见原因：少了冒号:、括号不匹配、缩进不对、引号不闭合",
            "检查那一行及上一行的语法",
            "如果是缩进错误（IndentationError），统一用 4 空格缩进",
        ],
        deep_troubleshoot=[
            "用编辑器（VS Code/PyCharm）的语法高亮，红色波浪线标出的位置",
            "python -m py_compile 文件名.py 快速检查语法",
            "复杂代码用 black 或 autopep8 自动格式化",
        ],
        fix_immediate="跳到报错行号，检查冒号、括号、引号、缩进",
        fix_short="使用 IDE 的自动格式化和语法检查插件",
        fix_long="配置 pre-commit hooks，提交前自动检查语法",
        missing_info=["报错的完整行号和错误信息", "出问题的代码片段"],
    ),
    ErrorPattern(
        pattern=r"(?i)(connection refused|ECONNREFUSED|connect: connection refused)",
        name="连接被拒绝（Connection refused）",
        root_cause="目标服务器/服务没在运行，或者端口不对，或者防火墙拦了。人话翻译：你想连的地方没人接电话。",
        confidence="high",
        category="network",
        troubleshoot=[
            "确认目标服务是否启动了（数据库/Redis/API 服务）",
            "确认端口号是否正确（不是默认端口）",
            "本地测试：telnet 主机 端口 或 curl 主机:端口",
            "如果是数据库：确认数据库服务正在运行，监听地址是 0.0.0.0 还是 127.0.0.1",
            "防火墙/安全组：检查是否开放了对应端口",
        ],
        deep_troubleshoot=[
            "Linux: systemctl status 服务名 确认服务状态",
            "Linux: ss -tlnp 看端口是否在监听",
            "Docker: docker ps 看容器是否在运行，端口映射是否正确",
            "K8s: kubectl get pods 看 Pod 是否 Running",
        ],
        fix_immediate="先启动目标服务（数据库/Redis/API），确认端口正确",
        fix_short="健康检查：服务启动后再发起请求",
        fix_long="配置服务依赖管理和启动顺序，熔断降级避免连锁报错",
        missing_info=["连接的目标地址和端口", "目标服务是什么（数据库/Redis/API）", "服务是否已启动"],
    ),
]

# 模式库版本信息
PATTERN_LIBRARY_VERSION = "2.6.0"
PATTERN_LIBRARY_UPDATED = "2026-08-30"
PATTERN_LIBRARY_COUNT = len(ERROR_PATTERNS)


# ============================================================
# 专业术语通俗解释（用于 --explain 模式）
# ============================================================
GLOSSARY = {
    "NPE": "空指针异常：程序试图使用一个不存在的对象（值为 null），就像打电话给一个空号。",
    "NullPointerException": "空指针异常：程序试图使用一个不存在的对象（值为 null），就像打电话给一个空号。",
    "OOM": "内存溢出：程序需要的内存超过了系统能提供的上限，就像杯子装不下更多的水。",
    "OutOfMemoryError": "内存溢出错误：Java 虚拟机的内存被用完了，无法继续运行。",
    "OOMKilled": "容器因内存超限被系统强制终止：就像租客用电超过了电表限额被自动拉闸。",
    "堆栈": "程序运行时的调用记录，像一条追溯链，可以看到错误是从哪一步传到哪一步的。",
    "堆栈跟踪": "程序运行时的调用记录，像一条追溯链，可以看到错误是从哪一步传到哪一步的。",
    "Stack Trace": "堆栈跟踪：程序运行时的调用记录，像一条追溯链，可以看到错误是从哪一步传到哪一步的。",
    "超时": "等待对方响应超过了设定的时间，就像点菜等了 30 分钟还没上，决定不等了。",
    "Timeout": "超时：等待对方响应超过了设定的时间。",
    "连接被拒绝": "目标服务没有在监听这个端口，就像打电话给一个已经停机的号码。",
    "Connection Refused": "连接被拒绝：目标服务没有在监听这个端口。",
    "连接池": "预先创建好的一批数据库连接，像餐厅提前备好的餐具，用的时候直接拿，不用每次现做。",
    "连接池耗尽": "所有预创建的连接都被占用了，新请求只能排队等待，就像餐厅所有座位都满了。",
    "死锁": "两个进程互相等待对方释放资源，谁也不让谁，就像两个人堵在门口都要对方先走。",
    "Deadlock": "死锁：两个进程互相等待对方释放资源，谁也不让谁。",
    "熔断": "当下游服务持续失败时，主动停止调用它，避免连锁故障，就像电路过载时自动跳闸保护。",
    "Circuit Breaker": "熔断器：当下游服务持续失败时，主动停止调用它，避免连锁故障。",
    "Hystrix": "Netflix 开源的熔断器组件，用于保护微服务之间的调用稳定性。",
    "Sentinel": "阿里开源的流量防护组件，支持限流、熔断、降级等功能。",
    "Feign": "Spring Cloud 中的声明式 HTTP 客户端，让远程调用像调用本地方法一样简单。",
    "降级": "当系统压力大或依赖不可用时，返回一个简化的默认结果，保证核心功能可用，就像飞机遇到故障放下氧气面罩保证基本安全。",
    "Fallback": "降级方案：当主逻辑失败时执行的备用逻辑，返回默认值或简化结果。",
    "限流": "限制单位时间内的请求数量，保护系统不被压垮，就像地铁站早高峰限流进站。",
    "Rate Limiting": "限流：限制单位时间内的请求数量，保护系统不被压垮。",
    "CrashLoopBackOff": "Kubernetes 中的状态：容器反复崩溃重启，系统在等待一段时间后再试，就像一个人反复摔倒又爬起来。",
    "Pod": "Kubernetes 中最小的部署单位，可以理解为一个或一组容器的包装盒。",
    "探针": "Kubernetes 定期检查容器健康状态的机制，像体检：liveness 查是否活着，readiness 查是否能接客。",
    "Liveness Probe": "存活探针：Kubernetes 检查容器是否还活着，失败了就重启容器。",
    "Readiness Probe": "就绪探针：Kubernetes 检查容器是否能接收流量，失败了就把它从负载均衡中摘除。",
    "镜像": "容器的模板文件，包含运行程序所需的一切，像安装包+运行环境的打包。",
    "ImagePullBackOff": "Kubernetes 拉取容器镜像失败后在等待重试，就像快递取件失败后改天再送。",
    "PVC": "PersistentVolumeClaim，Kubernetes 中申请持久化存储的方式，像向云盘厂商申请一块存储空间。",
    "ConfigMap": "Kubernetes 中存储配置信息的资源对象，像应用的外部配置文件。",
    "Secret": "Kubernetes 中存储敏感信息（密码、密钥）的资源对象，比 ConfigMap 更安全。",
    "HPA": "Horizontal Pod Autoscaler，Kubernetes 自动扩缩容机制，根据 CPU/内存等指标自动增减 Pod 数量。",
    "metrics-server": "Kubernetes 中收集资源使用数据（CPU/内存）的组件，HPA 依赖它获取指标。",
    "etcd": "Kubernetes 的数据库，存储集群所有状态数据，相当于集群的大脑记忆。",
    "kubelet": "运行在每个节点上的代理，负责管理本节点上的容器，像每个车间的班组长。",
    "容器运行时": "负责运行容器的软件（如 containerd、Docker），像真正执行任务的工人。",
    "TraceId": "分布式链路追踪 ID，一次请求在多个服务间传递时携带的唯一标识，像快递单号可以全程追踪。",
    "链路追踪": "追踪一次请求经过了哪些服务、每个服务花了多长时间，像查快递物流轨迹。",
    "P99 响应时间": "99% 的请求都能在这个时间内完成，用来衡量系统的最慢情况，比平均值更有参考价值。",
    "慢查询": "执行时间超过设定阈值的 SQL 语句，是数据库性能问题的常见原因。",
    "索引": "数据库中加速查询的数据结构，像书的目录，不用逐页翻就能快速找到内容。",
    "事务": "数据库中一组要么全部成功、要么全部失败的操作，像转账：扣钱和加钱必须同时成功或同时失败。",
    "Redis": "开源的内存数据库，读写速度极快，常用于缓存和临时数据存储。",
    "Kafka": "开源的分布式消息队列，用于服务间异步通信和解耦，像一个高效的快递中转站。",
    "消息堆积": "消息队列中生产速度大于消费速度，导致消息积压，就像快递站爆仓。",
    "线程池": "预先创建好的一批线程，避免频繁创建销毁的开销，像公司的正式员工团队，有任务直接分配。",
    "GC": "垃圾回收：Java 虚拟机自动回收不再使用的内存，像清洁工定期清理垃圾。",
    "Full GC": "全面垃圾回收：清理整个堆内存，耗时较长，期间程序暂停，像大扫除时店里暂停营业。",
    "CPU 使用率": "CPU 正在工作的时间占比，100% 表示 CPU 满负荷运转。",
    "内存使用率": "已使用内存占总内存的比例，超过 80% 通常需要关注。",
    "灰度发布": "逐步将流量切换到新版本，先让一小部分用户使用，确认没问题再全量发布，像新药先做临床试验。",
    "回滚": "将系统恢复到上一个稳定版本，像游戏读档回到之前的存档。",
    "告警风暴": "短时间内触发大量告警，通常是一个根因故障引发的连锁反应，需要先找根因而不是逐个处理。",
}

# 错误关键词正则（词边界，避免匹配 errorCode/errorMsg 等变量名）
# 注意：\b[A-Z]\w*Exception\b 用于匹配 NullPointerException、SQLException 等复合异常名
# （\bException\b 无法匹配复合异常名，会导致无 ERROR 级别前缀的裸异常堆栈漏报）
ERROR_KEYWORDS = re.compile(
    r"(\bERROR\b|\bFATAL\b|\bCRITICAL\b|\b[A-Z]\w*Exception\b|\b[A-Z]\w*Error\b|"
    r"\bTraceback\b|\bpanic\b|\btimeout\b|\btimed out\b|\brefused\b|"
    r"\bfailed\b|\bfailure\b|\bOOM\b|\bdeadlock\b|\bkilled\b|"
    r"\boverflow\b|\bout of memory\b|\bno space\b|\btoo many\b|"
    r"\balert\b|\balarm\b|\bwarning\b|\bcritical\b|\bunavailable\b|"
    r"\bthrottled\b|\bcircuit.*?breaker\b|\bfallback\b|\bblocked\b|"
    r"不存在|失败|超时|拒绝|溢出|耗尽|崩溃|异常|告警|警告|"
    r"不可用|宕机|熔断|降级|限流|阻塞|死锁|泄漏|满了|不足|"
    r"超过阈值|达到上限|连接失败|调用失败|请求失败|服务异常|"
    r"系统异常|业务异常|数据异常|配置错误|参数错误|权限不足|"
    r"ERR!|EACCES|denied|npm|pip|merge conflict|CONFLICT|"
    r"ModuleNotFoundError|ImportError|SyntaxError|NameError|TypeError|"
    r"command not found|not recognized|Permission denied)",
    re.IGNORECASE
)

# 日志级别正则（用于过滤 INFO/DEBUG 行的误报）
# 支持标准格式 "2026-08-30 14:23:11.234 ERROR" 和 Go 格式 "2026/08/30 20:30:05 [INFO]"
LOG_LEVEL = re.compile(
    r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}[\d.,\s]*\[?\s*"
    r"(INFO|DEBUG|TRACE|WARN|WARNING|ERROR|FATAL|CRITICAL)\s*\]?\b",
    re.IGNORECASE
)

# 误报模式：变量名/参数名中包含 error 但不是真正的错误
FALSE_POSITIVE_PATTERNS = re.compile(
    r"(errorCode|errorMsg|errorMessage|errorCount|errorNum|error_type|"
    r"errorField|errorList|errorMap|errorLog|errorHandler|errorPage|"
    r"onError|hasError|isError|showError|errorUrl|errorPath|errorId)",
    re.IGNORECASE
)


def is_real_error_line(line: str) -> bool:
    """
    判断一行是否为真正的错误行，过滤误报。
    - INFO/DEBUG/TRACE 级别行如果不含异常/堆栈/明确错误码，跳过
    - 仅包含 errorCode/errorMsg 等变量名的行，跳过
    """
    # 检查是否匹配错误关键词
    if not ERROR_KEYWORDS.search(line):
        return False

    # 提取日志级别
    level_match = LOG_LEVEL.search(line)
    if level_match:
        level = level_match.group(1).upper()
        # INFO/DEBUG/TRACE 级别：只有包含明确异常/堆栈/错误码时才算
        if level in ("INFO", "DEBUG", "TRACE"):
            has_real_error = bool(re.search(
                r"(Exception|Traceback \(most recent|panic:|fatal error:|runtime error:|"
                r"\bat\s+\w+\.\w+\(|Caused by:|"
                r"Connection refused|timed out|OOM|deadlock|OutOfMemory|"
                r"StackOverflow|NoClassDefFound|ClassNotFound|SQLException|"
                r"不存在|失败|超时|拒绝|溢出|耗尽|崩溃)",
                line, re.IGNORECASE
            ))
            if not has_real_error:
                return False

    # 检查是否仅为误报模式（变量名中含 error）
    # 如果行中匹配到错误关键词的位置全部在误报模式中，则跳过
    error_matches = list(ERROR_KEYWORDS.finditer(line))
    if error_matches:
        all_false_positive = True
        for m in error_matches:
            matched_text = m.group(0)
            # 检查这个匹配是否是误报模式的一部分
            start = max(0, m.start() - 10)
            end = min(len(line), m.end() + 10)
            context = line[start:end]
            if not FALSE_POSITIVE_PATTERNS.search(context):
                all_false_positive = False
                break
            # 中文关键词不算误报
            if re.search(r"[不存在失败超时拒绝溢出耗尽崩溃]", matched_text):
                all_false_positive = False
                break
        if all_false_positive:
            return False

    return True

# Java 堆栈起始行
JAVA_EXCEPTION_START = re.compile(
    r"^\s*(at |Caused by:|\.\.\. \d+ more|java\.|javax\.|org\.|com\.)",
)

# Python 堆栈起始
PYTHON_TRACEBACK_START = re.compile(r"Traceback \(most recent call last\):")

# Python 堆栈帧行（File "xxx", line N, in xxx）
PYTHON_FRAME = re.compile(r'^\s*File ".*", line \d+, in ')

# Go panic 起始
GO_PANIC_START = re.compile(r"^(panic:|fatal error:|runtime error:)")

# Go 堆栈帧行（main.xxx() 或 path/to/file.go:42）
GO_FRAME = re.compile(r"^\s*(\w[\w./]*\.\w+\(.*\)|[\w./-]+\.go:\d+)")

# Go goroutine 行
GO_GOROUTINE = re.compile(r"^goroutine \d+ \[")

# JSON 日志行检测
JSON_LOG_LINE = re.compile(r"^\s*\{.*\}\s*$")


def normalize_json_log_line(line: str) -> str:
    """
    将 JSON 结构化日志行转换为纯文本格式，便于后续分析。
    提取 level/time/msg/error/message 等常见字段。
    如果不是 JSON 或解析失败，返回原行。
    """
    line = line.strip()
    if not JSON_LOG_LINE.match(line):
        return line

    try:
        import json
        obj = json.loads(line)
        if not isinstance(obj, dict):
            return line

        # 提取常见字段
        level = obj.get("level", obj.get("severity", obj.get("lvl", ""))).upper()
        timestamp = obj.get("time", obj.get("timestamp", obj.get("ts", obj.get("@timestamp", ""))))
        msg = obj.get("msg", obj.get("message", obj.get("error", obj.get("err", ""))))
        logger = obj.get("logger", obj.get("caller", obj.get("module", "")))
        trace_id = obj.get("trace_id", obj.get("traceId", obj.get("x-trace-id", "")))

        # 构建纯文本行
        parts = []
        if timestamp:
            parts.append(str(timestamp))
        if level:
            parts.append(level)
        if trace_id:
            parts.append(f"[{trace_id}]")
        if logger:
            parts.append(str(logger))
        if msg:
            parts.append(f": {msg}")

        # 如果有 error 字段且与 msg 不同，追加
        error = obj.get("error", obj.get("err", ""))
        if error and error != msg:
            parts.append(f" | error: {error}")

        # 如果有 stack 字段，追加（JSON 日志可能包含堆栈字符串）
        stack = obj.get("stack", obj.get("stack_trace", obj.get("stacktrace", "")))
        result = " ".join(parts) if parts else line
        if stack:
            result = result + "\n" + str(stack)
        return result

    except (json.JSONDecodeError, ValueError):
        return line


def preprocess_log(lines: List[str]) -> Tuple[List[str], List[str]]:
    """
    预处理日志，提取关键错误片段和完整堆栈。
    支持：纯文本日志、JSON 结构化日志、Java 堆栈、Python traceback、Go panic。

    返回:
        (关键错误行列表, 完整错误片段列表（含堆栈上下文）)
    """
    # 第一步：规范化 JSON 日志行
    normalized_lines = []
    for line in lines:
        normalized = normalize_json_log_line(line)
        # JSON 日志可能包含多行堆栈（stack 字段含换行），拆分成多行
        normalized_lines.extend(normalized.split("\n"))

    error_lines = []
    error_segments = []
    i = 0

    while i < len(normalized_lines):
        line = normalized_lines[i].strip()
        if not line:
            i += 1
            continue

        # 检测到错误关键词（过滤误报）
        if is_real_error_line(line):
            error_lines.append(line)

            # 尝试提取完整错误片段（当前行 + 后续堆栈行）
            segment = [line]
            j = i + 1
            while j < len(normalized_lines):
                next_line = normalized_lines[j].strip()
                if not next_line:
                    j += 1
                    continue
                # 判断是否为堆栈延续行（Java/Python/Go）
                if (JAVA_EXCEPTION_START.match(next_line)
                        or PYTHON_TRACEBACK_START.search(next_line)
                        or PYTHON_FRAME.match(next_line)
                        or GO_PANIC_START.match(next_line)
                        or GO_FRAME.match(next_line)
                        or GO_GOROUTINE.match(next_line)
                        or next_line.startswith("File ")
                        or next_line.startswith("^")
                        or next_line.startswith("|")
                        or re.match(r"^\s+at\s", next_line)
                        or re.match(r"^\s+\^", next_line)
                        or re.match(r"^\s+created by ", next_line)):
                    segment.append(next_line)
                    j += 1
                else:
                    break

            # 去重：避免同一异常被重复提取
            segment_text = "\n".join(segment)
            if not any(segment_text[:100] in existing for existing in error_segments):
                error_segments.append(segment_text)

            i = j
        else:
            i += 1

    return error_lines, error_segments


# ============================================================
# 错误模式匹配
# ============================================================

# 泛化兜底模式名单：这些模式是通用/兜底型推测（命中任意类似关键词即触发），
# 当与更具体的模式命中同一证据段时，自动降级为备选（如"服务连接被拒绝"
# 是 Nginx 502 / K8s 探针失败 / 数据库连接拒绝等的泛化分支）。
GENERIC_PATTERN_NAMES = {
    "服务连接被拒绝",
    "限流/熔断触发",
    "中文告警文本",
}

# 表象类模式：这些模式描述的是故障的"症状/结果"（如重启、被杀、熔断、告警），
# 在故障链中倾向于作为"结果"或中间环节，不作为"主因"。
EFFECT_LIKE_PATTERN_NAMES = {
    "中文告警文本",
    "K8s Pod CrashLoopBackOff",
    "容器 OOMKilled",
    "K8s 健康检查探针失败",
    "限流/熔断触发",
    "Hystrix 熔断降级失败",
    "Sentinel 限流降级",
    "网关超时 (504)",
    "Service Mesh / 网关 503 无健康后端",
    "Spring Cloud Gateway 路由失败",
    "云服务 API 限流/配额超限",
    "K8s HPA 自动扩缩容异常",
}

# 故障链时间窗口（秒）：同一类别且间隔不超过该窗口的故障视为同一时间链
CHAIN_TIME_WINDOW = 600

# 日志元信息提取
META_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
META_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
# 实例级标识：K8s Pod 名（Deployment-ReplicaSetHash-PodHash，如 order-service-7d8f9c6b5d-x2k4p）
META_INSTANCE_RE = re.compile(r"([a-z][\w.-]*-[0-9a-f]{5,10}-[a-z0-9]{3,8})\b")


def extract_segment_meta(evidence: str):
    """从错误证据段首部提取 (时间戳, traceId, 实例标识)。

    - 时间戳：YYYY-MM-DD[ T]HH:MM:SS（ISO/普通格式均可）
    - traceId：段首第一个 [方括号] 内逗号分隔的最后一段（如 [order-service,abc123] → abc123）
    - 实例标识：K8s Pod 实例名（如 order-service-7d8f9c6b5d-x2k4p），用于同实例故障链合并
    """
    head = evidence[:300]
    ts = None
    m = META_TS_RE.search(head)
    if m:
        ts = m.group(1) + " " + m.group(2)
    trace = None
    m = META_BRACKET_RE.search(head)
    if m:
        inner = m.group(1)
        parts = [x.strip() for x in inner.split(",") if x.strip()]
        trace = parts[-1] if parts else inner
    instance = None
    m = META_INSTANCE_RE.search(head)
    if m:
        instance = m.group(1)
    return ts, trace, instance


def assign_fault_chains(root_causes) -> None:
    """对主推测进行故障链分组并分配链内角色（主因/诱因/结果）。

    同链判定（满足任一）：
      1. traceId 相同（强信号，跨服务调用链）
      2. 实例标识相同（强信号，同一 K8s Pod）
      3. 均有时间戳、间隔 ≤ CHAIN_TIME_WINDOW 且类别相同（弱信号）
      4. 当前无时间戳，但 traceId 或类别与当前链内某条相同（保守并入）

    角色分配（链长 ≥ 2）：
      - 主因 main   ：有明确时间戳的非表象模式中最早者；全为表象时取有时间戳最早者
      - 结果 effect ：有明确时间戳的表象模式中最晚者；无表象时取有时间戳最晚者
      - 诱因 trigger：其余中间环节
    链长为 1 时标记为 independent（独立故障，无同链关联）。
    备选模式（is_alternative）不参与分组，chain_id 保持 0。
    """
    prims = [rc for rc in root_causes if not rc.is_alternative]
    if not prims:
        return
    for rc in prims:
        ts, trace, instance = extract_segment_meta(rc.evidence)
        rc._ts = ts
        rc._trace = trace
        rc._instance = instance

    chains = []

    def _find_trace_chain(trace):
        for ch in chains:
            if any(x._trace == trace for x in ch):
                return ch
        return None

    def _find_instance_chain(instance):
        for ch in chains:
            if any(x._instance == instance for x in ch):
                return ch
        return None

    def _find_category_chain(category):
        hit = None
        for ch in chains:
            if any(x.pattern.category == category for x in ch):
                hit = ch  # 取最后一条匹配链
        return hit

    # 阶段 1：有元数据（traceId / 实例 / 时间戳）的段建立故障链
    for rc in prims:
        if not (rc._ts or rc._trace or rc._instance):
            continue
        hit = None
        if rc._trace:
            hit = _find_trace_chain(rc._trace)
        if hit is None and rc._instance:
            hit = _find_instance_chain(rc._instance)
        if hit is not None:
            hit.append(rc)
            continue
        cur = chains[-1] if chains else None
        # 弱信号：时间窗内 + 类别相同
        if cur is not None and rc._ts and cur[-1]._ts:
            try:
                from datetime import datetime as _dt
                d0 = _dt.strptime(rc._ts, "%Y-%m-%d %H:%M:%S")
                d1 = _dt.strptime(cur[-1]._ts, "%Y-%m-%d %H:%M:%S")
                delta = abs((d0 - d1).total_seconds())
            except Exception:
                delta = None
            if delta is not None and delta <= CHAIN_TIME_WINDOW and rc.pattern.category == cur[-1].pattern.category:
                cur.append(rc)
                continue
        chains.append([rc])

    # 阶段 2：无 traceId、无实例、无时间戳的段（如纯堆栈段），
    # 按类别并入已有故障链（同一类故障视为同链），无匹配则独立成链
    for rc in prims:
        if rc._ts or rc._trace or rc._instance:
            continue
        hit = _find_category_chain(rc.pattern.category)
        if hit is not None:
            hit.append(rc)
        else:
            chains.append([rc])

    # 链内按时间升序（无时间戳排后）
    for ch in chains:
        ch.sort(key=lambda x: (x._ts is None, x._ts or ""))

    # 赋 chain_id 与角色
    for ci, ch in enumerate(chains, 1):
        for rc in ch:
            rc.chain_id = ci
        if len(ch) == 1:
            ch[0].role = "independent"
            continue
        timed = [rc for rc in ch if rc._ts]
        mains = [rc for rc in timed if rc.pattern.name not in EFFECT_LIKE_PATTERN_NAMES]
        if not mains:
            mains = timed
        main = mains[0] if mains else ch[0]
        main.role = "main"
        effects = [rc for rc in timed if rc.pattern.name in EFFECT_LIKE_PATTERN_NAMES and rc is not main]
        if effects:
            effects[-1].role = "effect"
        else:
            rest_timed = [rc for rc in timed if rc is not main]
            if rest_timed:
                rest_timed[-1].role = "effect"
        for rc in ch:
            if rc.role == "":
                rc.role = "trigger"


def match_patterns(error_segments: List[str], lines: List[str]) -> List[MatchedPattern]:
    """
    对错误片段进行模式匹配，返回匹配到的模式列表（按置信度排序）。
    支持多异常嵌套（Caused by 链）：同一片段可匹配多个模式。
    支持多独立故障：不同片段分别匹配各自的模式。
    """
    matched = []
    seen_patterns = set()

    for seg_idx, segment in enumerate(error_segments):
        for pattern in ERROR_PATTERNS:
            if pattern.name in seen_patterns:
                continue
            try:
                m = re.search(pattern.pattern, segment, re.IGNORECASE)
                if m:
                    # 找到匹配的行号
                    line_no = 0
                    for idx, line in enumerate(lines):
                        if segment.split("\n")[0][:50] in line:
                            line_no = idx + 1
                            break

                    matched.append((MatchedPattern(
                        pattern=pattern,
                        evidence=segment[:500],  # 证据不超过 500 字符
                        line_number=line_no,
                        specificity=len(m.group(0)),  # 命中文本长度 = 特异性
                    ), seg_idx))
                    seen_patterns.add(pattern.name)
                    # 不 break：同一片段（如 Caused by 链式堆栈）可能匹配多个模式
            except re.error:
                continue

    # 同证据多模式命中：同一错误段命中多个模式时，只保留最特异的一个为主推测，
    # 其余更泛化的模式降级为备选，避免对同一条证据平铺多条推测。
    # 排序依据（三重键）：
    #   1) 泛化兜底模式（GENERIC_PATTERN_NAMES）优先降级——如"服务连接被拒绝"
    #      命中任意 Connection refused 文本，比 Nginx 502/探针失败等更泛化；
    #   2) 模式自身置信度（high > medium > low）；
    #   3) 命中文本长度（越长越特异）。
    # 这样避免仅靠命中长度排序时，泛化模式因匹配到较长文本而反超具体模式。
    by_seg = {}
    for mp, seg_idx in matched:
        by_seg.setdefault(seg_idx, []).append(mp)
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    result_list = []
    for seg_idx, mps in by_seg.items():
        if len(mps) > 1:
            mps.sort(key=lambda x: (
                x.pattern.name in GENERIC_PATTERN_NAMES,
                confidence_order.get(x.pattern.confidence, 3),
                -x.specificity,
            ))
            primary = mps[0]
            for other in mps[1:]:
                other.is_alternative = True
                other.primary_name = primary.pattern.name
        result_list.extend(mps)

    # 排序：主推测在前（按置信度 → 特异性降序），备选整体排在后
    result_list.sort(key=lambda m: (
        m.is_alternative,
        confidence_order.get(m.pattern.confidence, 3),
        -m.specificity,
    ))

    return result_list


# ============================================================
# 报告生成
# ============================================================

CONFIDENCE_EMOJI = {"high": "🔴高置信", "medium": "🟡中置信", "low": "⚪低置信"}


@dataclass
class ReportConfig:
    """报告输出配置，控制输出详细程度、格式和章节"""
    detail: str = "standard"       # brief / standard / full
    output_format: str = "markdown"  # markdown / json
    sections: List[str] = None     # 指定输出章节，None 表示全部
    deep_analysis: bool = False    # 深度分析模式：更多排查步骤、风险评估、回滚方案

    # 各详细程度对应的章节列表
    BRIEF_SECTIONS = ["summary", "root_causes", "fixes"]
    STANDARD_SECTIONS = ["summary", "evidence", "root_causes", "correlation", "troubleshoot", "fixes", "missing_info"]
    FULL_SECTIONS = ["summary", "evidence", "root_causes", "correlation", "troubleshoot", "fixes", "missing_info", "extended"]

    def get_enabled_sections(self) -> List[str]:
        """获取当前配置下启用的章节列表"""
        if self.sections:
            return self.sections
        if self.detail == "brief":
            return self.BRIEF_SECTIONS
        elif self.detail == "full":
            return self.FULL_SECTIONS
        return self.STANDARD_SECTIONS

    def has_section(self, name: str) -> bool:
        """检查指定章节是否启用"""
        return name in self.get_enabled_sections()

    def get_troubleshoot_limit(self) -> int:
        """获取排查步骤数量限制"""
        if self.detail == "brief":
            return 3
        elif self.detail == "full" or self.deep_analysis:
            return 8
        return 5

    def get_evidence_limit(self) -> int:
        """获取证据摘录数量限制"""
        if self.detail == "brief":
            return 2
        elif self.detail == "full":
            return 8
        return 5

    def get_root_cause_limit(self) -> int:
        """获取根因候选数量限制"""
        if self.detail == "brief":
            return 3
        elif self.detail == "full":
            return 8
        return 5


def truncate_text(text: str, max_len: int) -> str:
    """截断文本，超过长度时加省略号，避免在单词中间截断。"""
    if len(text) <= max_len:
        return text
    # 尝试在最近的空格/标点处截断
    truncated = text[:max_len]
    last_space = max(truncated.rfind(" "), truncated.rfind(","), truncated.rfind(":"), truncated.rfind(";"))
    if last_space > max_len * 0.7:
        truncated = truncated[:last_space]
    return truncated + "..."


def _brief_rc_list(result: AnalysisResult, config: ReportConfig = None):
    """brief 模式默认过滤泛化模式（含备选），Top 数量限制不变；全部为泛化时回退保留避免空报告。"""
    limit = config.get_root_cause_limit() if config else 5
    rcs = result.root_causes[:limit]
    if config is not None and config.detail == "brief":
        filtered = [rc for rc in result.root_causes if rc.pattern.name not in GENERIC_PATTERN_NAMES][:limit]
        if filtered:
            rcs = filtered
    return rcs


def generate_json_report(result: AnalysisResult, config: ReportConfig = None) -> str:
    """生成 JSON 格式的结构化分析报告，便于程序解析和后续处理。"""
    import json

    report = {
        "report_type": "log_fault_analysis",
        "version": "2.0",
        "summary": result.summary,
        "stats": {
            "total_error_lines": len(result.raw_errors),
            "matched_patterns": len([rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)]),
            "has_stack_trace": result.has_stack_trace,
            "confidence_adjusted": result.any_confidence_adjusted,
        },
        "evidence": result.evidence[:config.get_evidence_limit()] if config else result.evidence[:5],
        "root_causes": [
            {
                "pattern": rc.pattern.name,
                "category": rc.pattern.category,
                "confidence": getattr(rc, 'adjusted_confidence', rc.pattern.confidence),
                "confidence_adjusted": getattr(rc, 'confidence_adjusted', False),
                "is_alternative": getattr(rc, 'is_alternative', False),
                "primary_name": getattr(rc, 'primary_name', ''),
                "chain": getattr(rc, 'chain_id', 0),
                "role": getattr(rc, 'role', ''),
                "root_cause": rc.pattern.root_cause,
                "evidence": rc.evidence[:300],
                "line_number": rc.line_number,
            }
            for rc in _brief_rc_list(result, config)
        ],
        "troubleshooting": {
            "immediate": result.immediate_checks[:config.get_troubleshoot_limit() if config else 5],
            "deep": result.deep_checks[:config.get_troubleshoot_limit() if config else 5],
        },
        "fixes": [
            {
                "immediate": immediate,
                "short_term": short,
                "long_term": long,
            }
            for immediate, short, long in result.fixes[:3]
        ],
        "missing_info": list(set(
            [info for rc in result.root_causes for info in rc.pattern.missing_info] +
            (result.missing_info if result.missing_info else [])
        )),
    }

    # 多故障时增加关联分析（仅统计主推测；同证据备选不视为独立故障）
    primary_all = [rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)]
    if len(primary_all) >= 2:
        report["correlation_analysis"] = {
            "fault_count": len(primary_all),
            "fault_names": [rc.pattern.name for rc in primary_all],
            "note": "检测到多个错误模式，需判断是连锁反应还是独立故障，建议优先排查时间最早的故障",
        }

    return json.dumps(report, ensure_ascii=False, indent=2)


def generate_report(result: AnalysisResult, config: ReportConfig = None) -> str:
    """生成结构化 Markdown 故障分析报告。

    Args:
        result: 分析结果
        config: 报告输出配置，None 时使用默认标准配置
    """
    if config is None:
        config = ReportConfig()

    # JSON 格式输出
    if config.output_format == "json":
        return generate_json_report(result, config)

    lines_out = []
    ts_limit = config.get_troubleshoot_limit()
    ev_limit = config.get_evidence_limit()
    rc_limit = config.get_root_cause_limit()

    # 标题
    lines_out.append("# 故障分析报告")
    lines_out.append("")

    # 日志处理说明（超长截取时输出）
    if result.log_processing_note:
        lines_out.append("> ⚠️ **日志处理说明**：" + result.log_processing_note)
        lines_out.append("")

    # 1. 异常摘要
    if config.has_section("summary"):
        lines_out.append("## 1.异常摘要")
        lines_out.append("")
        if result.summary:
            lines_out.append(result.summary)
        else:
            lines_out.append("未能从日志中提取明确的故障现象，请补充更多上下文信息。")
        lines_out.append("")

    # 2. 关键日志证据摘录
    if config.has_section("evidence"):
        lines_out.append("## 2.关键日志证据摘录")
        lines_out.append("")
        if result.evidence:
            for ev in result.evidence[:ev_limit]:
                lines_out.append(f"> {truncate_text(ev, 500)}")
                lines_out.append("")
        else:
            lines_out.append("> 【证据不足】未从日志中提取到明确的错误信息。")
            lines_out.append("")

    # 3. 根因候选
    if config.has_section("root_causes"):
        lines_out.append("## 3.根因候选（按主因/诱因/结果分组）")
        lines_out.append("")

        # 结构概览：先告诉用户整体分布，降低多错误场景的阅读负担
        if result.root_causes:
            _prims = [rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)]
            _chain_ids = set(getattr(rc, "chain_id", 0) or 0 for rc in _prims)
            _multi_chains = [cid for cid in _chain_ids if len([r for r in _prims if (getattr(r, "chain_id", 0) or 0) == cid]) >= 2]
            _indep = [rc for rc in _prims if getattr(rc, "role", "") == "independent"]
            _overview_parts = []
            if _multi_chains:
                _total_in_chains = sum(len([r for r in _prims if (getattr(r, "chain_id", 0) or 0) == cid]) for cid in _multi_chains)
                _overview_parts.append(f"{len(_multi_chains)} 条故障链（共 {_total_in_chains} 个模式，含主因→诱因→结果）")
            if _indep:
                _overview_parts.append(f"{len(_indep)} 个独立故障（无因果关联，建议分别排查）")
            if _overview_parts:
                lines_out.append(f"> 📊 本次检测到 {len(_prims)} 个错误模式：{'，'.join(_overview_parts)}。")
                lines_out.append("")

        def _fmt_rc(rc):
            """格式化单条根因候选（含置信度徽标、证据、低证据与下调说明）"""
            actual_confidence = getattr(rc, 'adjusted_confidence', rc.pattern.confidence)
            emoji = CONFIDENCE_EMOJI.get(actual_confidence, "⚪低置信")
            evidence_short = truncate_text(rc.evidence.replace("\n", " "), 250)
            suffix = "（证据不足）" if actual_confidence == "low" else ""
            confidence_note = ""
            if getattr(rc, 'confidence_adjusted', False):
                confidence_note = " ⚠️（基于有限证据，置信度已下调）"
            return f"{emoji}：{rc.pattern.root_cause}，依据：`{evidence_short}`{suffix}{confidence_note}"

        if result.root_causes:
            prims = [rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)]
            alts = [rc for rc in result.root_causes if getattr(rc, "is_alternative", False)]
            # brief 模式默认过滤泛化模式（服务连接被拒绝/限流熔断/中文告警文本等），
            # 仅当过滤后为空（全部候选均为泛化）时回退保留，避免空报告
            if config.detail == "brief":
                filtered = [rc for rc in prims if rc.pattern.name not in GENERIC_PATTERN_NAMES]
                if filtered:
                    prims = filtered
            alt_map = {}
            for _a in alts:
                alt_map.setdefault(getattr(_a, "primary_name", ""), []).append(_a)

            def _alt_suffix(primary_name):
                notes = []
                for _a in alt_map.get(primary_name, []):
                    notes.append(f"（另有备选：「{_a.pattern.name}」为更泛化推测，已折叠）")
                return "".join(notes)

            # 按链分组（保持链顺序）
            chains = {}
            chain_order = []
            for rc in prims:
                cid = getattr(rc, "chain_id", 0) or 0
                if cid not in chains:
                    chains[cid] = []
                    chain_order.append(cid)
                chains[cid].append(rc)

            rendered_count = 0
            chain_no = 0  # 多模式故障链连续编号（独立故障不占编号，避免出现"故障链 2"缺 1）
            # 先渲染多模式故障链（有因果关系，优先级高），再渲染独立故障
            _sorted_order = sorted(chain_order, key=lambda c: (0 if len(chains[c]) >= 2 else 1, c))
            _indep_header_printed = False
            for cid in _sorted_order:
                members = chains[cid]
                # 链内按时间升序呈现（无时间戳的排在末尾）
                members.sort(key=lambda x: (getattr(x, '_ts', None) is None, getattr(x, '_ts', None) or ""))
                if len(members) == 1:
                    # 独立故障：平铺单条，标注独立；若前面有故障链，加小标题分隔
                    if _multi_chains and not _indep_header_printed:
                        lines_out.append("### 📌 独立故障（与上述故障链无因果关联）")
                        lines_out.append("")
                        _indep_header_printed = True
                    rc = members[0]
                    lines_out.append(f"- {_fmt_rc(rc)}{_alt_suffix(rc.pattern.name)}（独立故障）")
                    rendered_count += 1
                else:
                    chain_no += 1
                    ts_list = [rc._ts for rc in members if rc._ts]
                    span = ""
                    if len(ts_list) >= 2:
                        span = f"，时间跨度 {ts_list[0]} ~ {ts_list[-1]}"
                    lines_out.append(f"### 🔍 故障链 {chain_no}：{len(members)} 个错误模式{span}")
                    lines_out.append("")
                    mains = [rc for rc in members if rc.role == "main"]
                    triggers = [rc for rc in members if rc.role == "trigger"]
                    effects = [rc for rc in members if rc.role == "effect"]
                    if mains:
                        lines_out.append(f"- **主因**：{_fmt_rc(mains[0])}{_alt_suffix(mains[0].pattern.name)}")
                    if triggers:
                        lines_out.append("- **诱因**：")
                        for rc in triggers:
                            lines_out.append(f"  - {_fmt_rc(rc)}{_alt_suffix(rc.pattern.name)}")
                    if effects:
                        lines_out.append(f"- **结果**：{_fmt_rc(effects[0])}{_alt_suffix(effects[0].pattern.name)}")
                    lines_out.append("")
                    rendered_count += 1
                if rendered_count >= rc_limit:
                    break
            lines_out.append("")
        else:
            lines_out.append("- ⚪低置信：【证据不足】未能匹配到已知错误模式，日志信息可能不完整或格式不规范。")
            lines_out.append("")
            lines_out.append("**未匹配到已知模式的可能原因：**")
            lines_out.append(f"- 错误类型不在当前 {PATTERN_LIBRARY_COUNT} 种预设模式中（如自定义异常、第三方 SDK 报错）")
            lines_out.append("- 日志被截断，关键报错信息（异常类型/错误码）未包含在片段中")
            lines_out.append("- 错误信息使用了非标准格式或被脱敏处理")
            lines_out.append("- 这是业务逻辑错误（如参数校验失败），而非系统异常")
            lines_out.append("")
            lines_out.append("**建议：** 补充完整的异常堆栈（包含异常类名和 message），或提供错误码/异常类名以便手动分析。")
            lines_out.append("")

        # 复杂场景提示：主推测较多时建议精简模式（同证据备选不计数）
        primary_n = len([rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)])
        if primary_n > 5 and config.detail == "standard":
            lines_out.append(f"> 💡 **输出精简提示**：本次匹配到 {primary_n} 个错误模式，报告较长。如需更简洁的输出，可使用 `--detail brief` 参数（仅保留摘要、Top3 根因和修复建议），或使用 `--sections summary,root_causes,fixes` 只输出关键章节。")
            lines_out.append("")

    # 3.5 故障关联分析（多个主推测故障时；同证据的备选模式不参与关联判断）
    primary_list = [rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)]
    if config.has_section("correlation") and len(primary_list) >= 2:
        lines_out.append("## 3.5 故障关联分析")
        lines_out.append("")
        lines_out.append(f"日志中检测到 {len(primary_list)} 个错误模式，需判断是**连锁反应**（一个故障引发另一个）还是**独立故障**（同时发生的不同问题）。")
        lines_out.append("")
        lines_out.append("**连锁反应判断依据**：时间先后 + 后报错包含前故障特征 + 同一 traceId/请求ID 关联。")
        lines_out.append("")
        lines_out.append("**独立故障判断依据**：traceId 互不相同 + 故障类型完全不同 + 同时发生无因果。")
        lines_out.append("")
        fault_names = [rc.pattern.name for rc in primary_list]
        lines_out.append(f"**本次故障列表：** {'、'.join(fault_names)}")
        lines_out.append("")
        lines_out.append("> 建议：优先排查时间最早的故障，确认其是否为根因；若各故障 traceId 不同且无因果关系，则按独立故障分别处理。")
        lines_out.append("")

    # 4. 排查操作清单
    if config.has_section("troubleshoot"):
        lines_out.append("## 4.排查操作清单")
        lines_out.append("")
        lines_out.append("### 🔹立即排查（优先做）")
        lines_out.append("")
        if result.immediate_checks:
            for idx, check in enumerate(result.immediate_checks[:ts_limit], 1):
                lines_out.append(f"{idx}. {check}")
        else:
            lines_out.append("1. 【证据不足】请先补充完整的错误日志和堆栈信息。")
        lines_out.append("")

        lines_out.append("### 🔹深度排查（上面无结果再执行）")
        lines_out.append("")
        if result.deep_checks:
            for idx, check in enumerate(result.deep_checks[:ts_limit], 1):
                lines_out.append(f"{idx}. {check}")
        else:
            lines_out.append("1. 查看应用完整日志，搜索 ERROR/Exception 关键词。")
            lines_out.append("2. 用 traceId/请求ID 关联完整调用链。")
        lines_out.append("")

    # 5. 修复建议
    if config.has_section("fixes"):
        lines_out.append("## 5.修复建议")
        lines_out.append("")
        if result.fixes:
            fix_limit = 3 if config.detail == "brief" else 3
            for idx, (immediate, short, long) in enumerate(result.fixes[:fix_limit]):
                if len(result.fixes) > 1:
                    lines_out.append(f"### 方案 {idx + 1}")
                    lines_out.append("")
                lines_out.append("### 立即止血")
                lines_out.append(f"- {immediate}")
                lines_out.append("")
                if config.detail == "full":
                    # 完整模式：三层修复建议
                    lines_out.append("### 短期修复")
                    lines_out.append(f"- {short}")
                    lines_out.append("")
                    lines_out.append("### 长期优化")
                    lines_out.append(f"- {long}")
                    lines_out.append("")
                elif config.detail == "standard":
                    # standard：短期+长期合并为一行要点，控制报告长度
                    lines_out.append(f"- **短期/长期**：{short}；{long}")
                    lines_out.append("")
                # 深度分析模式：增加风险评估和回滚方案
                if config.deep_analysis or config.detail == "full":
                    lines_out.append("### ⚠️ 风险评估与回滚方案")
                    lines_out.append("- **操作风险**：立即止血操作可能影响业务连续性，建议在低峰期执行并提前通知相关方")
                    lines_out.append("- **回滚方案**：如修复后出现新问题，立即回滚到上一个稳定版本/配置，并保留现场日志用于后续分析")
                    lines_out.append("- **验证标准**：修复后需确认错误率恢复正常、相关监控指标回到基线、业务功能正常")
                    lines_out.append("")
        else:
            lines_out.append("【证据不足】无法给出针对性修复建议，请补充完整日志信息后重新分析。")
            lines_out.append("")

    # 6. 缺失信息
    if config.has_section("missing_info"):
        lines_out.append("## 6.缺失信息/待补充（如果有）")
        lines_out.append("")
        all_missing = set()
        for rc in result.root_causes:
            all_missing.update(rc.pattern.missing_info)
        if result.missing_info:
            all_missing.update(result.missing_info)
        if all_missing:
            missing_sorted = sorted(all_missing)
            if config.detail == "full":
                shown = missing_sorted
            else:
                shown = missing_sorted[:8]
            for info in shown:
                lines_out.append(f"- {info}")
            if len(missing_sorted) > len(shown):
                lines_out.append(f"- …共 {len(missing_sorted)} 条待补充信息，其余 {len(missing_sorted) - len(shown)} 条请使用 `--detail full` 查看")
        else:
            lines_out.append("- 暂无明显缺失信息，建议按排查清单逐步验证根因。")
        lines_out.append("")

    # 7. 延伸分析
    if config.has_section("extended") and result.root_causes:
        lines_out.append("## 7.延伸分析")
        lines_out.append("")
        categories = set(rc.pattern.category for rc in result.root_causes)
        lines_out.append(f"- **预防建议**：针对 `{'/'.join(categories)}` 类故障，建议增加对应监控告警（错误率/响应时间/资源使用率），提前发现异常。")
        lines_out.append(f"- **监控建议**：配置关键指标告警（如错误率 > 5%、P99 响应时间 > 阈值、内存/磁盘使用率 > 85%）。")
        primary_n_total = len([rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)])
        lines_out.append(f"- **同类模式**：本次匹配到 {primary_n_total} 个错误模式，建议排查是否有同类故障的历史记录，参考已有解决方案。")
        if config.deep_analysis or config.detail == "full":
            lines_out.append(f"- **监控配置建议**：建议配置 Prometheus/Grafana 监控面板，关键指标包括：错误率（5xx 占比）、P99 响应时间、资源使用率（CPU/内存/磁盘/网络）、连接池使用率、队列积压量。告警阈值建议：错误率 > 3% 持续 2 分钟、P99 > 基线 2 倍、资源使用率 > 80%。")
            lines_out.append(f"- **复盘建议**：故障恢复后建议进行故障复盘（Postmortem），内容包括：故障时间线、根因分析、修复措施、改进计划、责任人。建议在 48 小时内完成复盘并同步团队。")
        lines_out.append("")

    # 报告生成信息
    lines_out.append("---")
    lines_out.append("")
    primary_n_tail = len([rc for rc in result.root_causes if not getattr(rc, "is_alternative", False)])
    lines_out.append(f"> 本报告由故障日志诊断专家自动生成，共提取 {len(result.raw_errors)} 条错误信息，匹配 {primary_n_tail} 个错误模式。")
    if result.log_processing_note:
        lines_out.append(f"> 日志处理：原始 {result.original_line_count} 行 → 智能截取后 {result.processed_line_count} 行，保留所有错误和堆栈信息。")
    if result.any_confidence_adjusted:
        stack_note = "无堆栈信息" if not result.has_stack_trace else "堆栈信息不完整"
        lines_out.append(f"> ⚠️ 证据强度评估：{stack_note}，根因置信度已保守下调。建议补充完整堆栈和上下文后重新分析以获得更准确的结论。")
    lines_out.append("> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。")

    return "\n".join(lines_out)


def generate_plain_summary(result: AnalysisResult) -> str:
    """生成一句话通俗摘要，用非技术语言说明发生了什么和该怎么办。"""
    if not result.root_causes:
        return "📝 **一句话总结**：日志中没有找到明确的错误信息，可能是日志不完整，建议补充完整的报错堆栈后再分析。"

    top_rc = result.root_causes[0]
    rc_name = top_rc.pattern.name
    confidence = getattr(top_rc, 'adjusted_confidence', top_rc.pattern.confidence)
    confidence_text = {"high": "很可能", "medium": "有可能", "low": "不确定"}.get(confidence, "有可能")

    # 根据故障类型生成通俗描述
    plain_descriptions = {
        "npe": "程序试图使用一个不存在的对象（空指针）",
        "db": "数据库查询或连接出了问题",
        "redis": "Redis 缓存服务出了问题",
        "network": "网络连接失败或超时",
        "os": "服务器资源不足（内存/磁盘/文件句柄）",
        "k8s": "Kubernetes 容器或集群出了问题",
        "microservice": "微服务之间的调用出了问题",
        "alert": "收到了运维告警通知",
        "go": "Go 程序运行时崩溃（panic）",
        "python": "Python 程序运行时异常",
        "mq": "消息队列服务出了问题",
        "security": "安全认证或权限出了问题",
        "middleware": "中间件服务出了问题",
    }
    plain_desc = plain_descriptions.get(top_rc.pattern.category, "系统运行出现异常")

    # 立即修复建议
    immediate_fix = top_rc.pattern.fix_immediate if top_rc.pattern.fix_immediate else "先查看详细排查清单"

    fault_count = len(result.root_causes)
    if fault_count > 1:
        multi_note = f"，同时发现 {fault_count} 个相关问题"
    else:
        multi_note = ""

    return (
        f"📝 **一句话总结**：{confidence_text}是「{rc_name}」导致的——{plain_desc}{multi_note}。"
        f"建议先做：{immediate_fix}。"
    )


def extract_glossary_terms(report_text: str, result: AnalysisResult) -> List[Tuple[str, str]]:
    """从报告中提取出现的专业术语，返回需要解释的术语列表。"""
    found_terms = []
    seen = set()

    # 按术语长度降序匹配，避免短词先匹配（如 "OOM" 先于 "OutOfMemoryError"）
    sorted_terms = sorted(GLOSSARY.keys(), key=len, reverse=True)

    for term in sorted_terms:
        if term.lower() in report_text.lower() and term not in seen:
            found_terms.append((term, GLOSSARY[term]))
            seen.add(term)
            # 标记相关同义词也已找到，避免重复
            for other_term, other_desc in GLOSSARY.items():
                if other_desc == GLOSSARY[term] and other_term not in seen:
                    seen.add(other_term)

    # 限制最多解释 15 个术语，避免报告过长
    return found_terms[:15]


def generate_glossary_section(report_text: str, result: AnalysisResult) -> str:
    """生成术语解释章节。"""
    terms = extract_glossary_terms(report_text, result)
    if not terms:
        return ""

    lines = []
    lines.append("")
    lines.append("## 8. 术语解释（通俗版）")
    lines.append("")
    lines.append("> 以下是本报告中出现的专业术语的通俗解释，帮助非技术用户理解。")
    lines.append("")
    for term, explanation in terms:
        lines.append(f"- **{term}**：{explanation}")
    lines.append("")
    return "\n".join(lines)


def friendly_error(error_type: str, detail: str = "", suggestion: str = "") -> None:
    """输出友好的错误提示，包含通俗解释和解决建议。"""
    emoji_map = {
        "file_not_found": "📁",
        "file_read": "📄",
        "empty_input": "📭",
        "no_input": "💡",
        "encoding": "🔤",
        "binary": "🖥️",
        "no_errors": "✅",
        "unknown": "⚠️",
    }
    emoji = emoji_map.get(error_type, "⚠️")
    messages = {
        "file_not_found": f"找不到这个文件：{detail}",
        "file_read": f"读取文件时出了点问题：{detail}",
        "empty_input": "日志内容是空的，没有可以分析的内容",
        "no_input": "还没有收到日志内容",
        "encoding": "文件编码可能有问题：{detail}",
        "binary": "这个文件看起来不是文本日志（可能是二进制文件）",
        "no_errors": "日志里没有找到明显的错误信息",
        "unknown": f"处理时遇到了意外问题：{detail}",
    }
    suggestions = {
        "file_not_found": "请检查文件路径是否正确，文件名是否拼写正确，文件是否还在原来的位置。",
        "file_read": "请检查文件是否被其他程序占用，或者尝试将日志复制到一个新文件中再分析。",
        "empty_input": "请粘贴包含 ERROR/Exception/报错 的日志片段，至少需要有一条错误信息才能分析。",
        "no_input": "请在命令后面加上日志文件路径，例如：python log_analyzer.py app.log；或者用管道传入：cat app.log | python log_analyzer.py",
        "encoding": "请尝试将文件另存为 UTF-8 编码，或者直接复制日志内容粘贴到新的文本文件中。",
        "binary": "请提供文本格式的日志文件（.log/.txt），不支持压缩包、图片、可执行文件等二进制格式。",
        "no_errors": "这段日志看起来都是正常运行记录（INFO/DEBUG），没有发现 ERROR、Exception 或报错。如果确实有问题，请提供包含报错信息的日志片段，或者检查是否选错了时间段。",
        "unknown": "请尝试使用 --quick 快速模式，或者减少日志行数后重试。如问题持续，请将日志片段和报错信息反馈给开发者。",
    }
    print(f"\n{emoji} {messages.get(error_type, messages['unknown'])}", file=sys.stderr)
    if suggestion or suggestions.get(error_type):
        print(f"   💡 建议：{suggestion or suggestions.get(error_type, '')}", file=sys.stderr)
    print("", file=sys.stderr)


def validate_log_content(log_text: str) -> Tuple[bool, str]:
    """验证日志内容是否适合分析，返回(是否有效, 提示信息)。"""
    if not log_text.strip():
        return False, "empty_input"

    lines = log_text.split("\n")
    non_empty_lines = [l for l in lines if l.strip()]

    if len(non_empty_lines) == 0:
        return False, "empty_input"

    # 检测是否为二进制内容（包含大量不可打印字符）
    # 小文件（< 100 字符）跳过二进制检测，避免 BOM 等编码标记误判
    if len(log_text) > 100:
        printable_count = sum(1 for c in log_text if c.isprintable() or c in "\n\r\t")
        if printable_count / len(log_text) < 0.7:
            return False, "binary"

    # 检测是否全是正常日志（没有错误关键词）
    has_error = False
    for line in non_empty_lines[:200]:  # 只检查前200行，避免太慢
        if ERROR_KEYWORDS.search(line):
            has_error = True
            break

    if not has_error and len(non_empty_lines) >= 2:
        # 没有错误关键词，但内容不为空，给出提示但不拒绝
        return True, "no_errors"

    return True, ""


# ============================================================
# 主分析流程
# ============================================================

def truncate_long_log(lines: List[str], max_lines: int = 500, context_lines: int = 5) -> Tuple[List[str], str]:
    """
    智能截取超长日志，保留关键错误信息和上下文。

    策略：
    1. 保留所有 ERROR/FATAL/CRITICAL 行及其前后 context_lines 行上下文
    2. 保留所有异常堆栈行（at / Caused by / Traceback / File / panic / goroutine）
    3. 保留 WARN 行
    4. 过滤纯 INFO/DEBUG 行（无错误关键词的）
    5. 如果截取后仍超过 max_lines，按时间顺序保留最早和最晚的错误片段

    返回:
        (截取后的日志行列表, 处理说明文本)
    """
    total_lines = len([l for l in lines if l.strip()])
    if total_lines <= max_lines:
        return lines, ""

    # 标记需要保留的行
    keep = [False] * len(lines)
    error_line_count = 0
    warn_line_count = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # ERROR/FATAL/CRITICAL 行：保留并标记上下文
        if re.search(r"\b(ERROR|FATAL|CRITICAL)\b", stripped, re.IGNORECASE):
            error_line_count += 1
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            for j in range(start, end):
                keep[j] = True
        # 异常堆栈行：直接保留
        elif (JAVA_EXCEPTION_START.match(stripped)
              or PYTHON_TRACEBACK_START.search(stripped)
              or PYTHON_FRAME.match(stripped)
              or GO_PANIC_START.match(stripped)
              or GO_FRAME.match(stripped)
              or GO_GOROUTINE.match(stripped)
              or stripped.startswith("Caused by:")
              or re.match(r"^\s+at\s", stripped)
              or re.match(r'^\s*File "', stripped)):
            keep[i] = True
        # WARN 行：保留
        elif re.search(r"\b(WARN|WARNING)\b", stripped, re.IGNORECASE):
            warn_line_count += 1
            keep[i] = True

    # 收集保留的行
    truncated = [lines[i] for i in range(len(lines)) if keep[i]]
    truncated_count = len([l for l in truncated if l.strip()])

    # 如果截取后仍超过 max_lines，进一步压缩：只保留错误行前后 2 行上下文
    if truncated_count > max_lines:
        keep2 = [False] * len(lines)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if re.search(r"\b(ERROR|FATAL|CRITICAL)\b", stripped, re.IGNORECASE):
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                for j in range(start, end):
                    keep2[j] = True
            elif (JAVA_EXCEPTION_START.match(stripped)
                  or PYTHON_TRACEBACK_START.search(stripped)
                  or GO_PANIC_START.match(stripped)
                  or stripped.startswith("Caused by:")):
                keep2[i] = True
        truncated = [lines[i] for i in range(len(lines)) if keep2[i]]
        truncated_count = len([l for l in truncated if l.strip()])

    note = (
        f"日志过长（原始 {total_lines} 行，超过 {max_lines} 行阈值），已自动智能截取："
        f"保留 {error_line_count} 条 ERROR/FATAL 行及其上下文、{warn_line_count} 条 WARN 行、"
        f"所有异常堆栈帧，过滤纯 INFO/DEBUG 行。处理后 {truncated_count} 行。"
        f"如需完整分析，请用 --max-lines 参数调大阈值，或分段提供日志。"
    )

    return truncated, note


def analyze_log(log_text: str, max_lines: int = 500, quick_mode: bool = False) -> AnalysisResult:
    """
    主分析流程：预处理 → 模式匹配 → 结果汇总。

    Args:
        log_text: 原始日志文本
        max_lines: 最大处理行数，超过则智能截取（默认 500）
    """
    result = AnalysisResult()
    lines = log_text.split("\n")
    result.original_line_count = len([l for l in lines if l.strip()])

    # 0. 超长日志智能截取
    lines, processing_note = truncate_long_log(lines, max_lines=max_lines)
    result.log_processing_note = processing_note
    result.processed_line_count = len([l for l in lines if l.strip()])

    # 快速模式提示
    if quick_mode:
        quick_note = "快速模式：已启用快速扫描，仅提取关键错误信息，不展开完整堆栈。如需完整分析请去掉 --quick 参数。"
        if result.log_processing_note:
            result.log_processing_note = result.log_processing_note + " " + quick_note
        else:
            result.log_processing_note = quick_note

    # 1. 预处理
    error_lines, error_segments = preprocess_log(lines)
    result.raw_errors = error_lines
    result.has_errors = len(error_lines) > 0

    if not error_lines:
        # 检测日志格式，给出更有针对性的建议
        total_lines = len([l for l in lines if l.strip()])
        json_lines = sum(1 for l in lines if l.strip() and JSON_LOG_LINE.match(l.strip()))
        warn_lines = sum(1 for l in lines if re.search(r"\bWARN\b|\bWARNING\b", l, re.IGNORECASE))

        format_note = ""
        if json_lines > 0 and json_lines >= total_lines * 0.5:
            format_note = f"检测到 JSON 结构化日志（{json_lines}/{total_lines} 行），已自动解析。"
        elif json_lines > 0:
            format_note = f"检测到混合格式日志（{json_lines} 行 JSON + {total_lines - json_lines} 行纯文本）。"

        warn_note = f"日志中包含 {warn_lines} 条 WARN 级别日志，" if warn_lines > 0 else ""

        result.summary = (
            f"日志中未检测到 ERROR/Exception/失败等错误关键词。{format_note}"
            f"{warn_note}可能原因：①日志片段不完整，错误部分未包含 ②日志级别配置过高（只记录了 INFO/WARN）"
            f"③错误信息使用了非标准格式（如自定义错误码）④这是正常运行日志，确实无错误。"
            f"建议：提供完整的报错时间窗口日志（报错前后各 50 行），或直接粘贴包含堆栈的完整报错片段。"
        )
        result.evidence = []
        # 即使没有错误，也给出通用排查建议
        result.immediate_checks = [
            "确认日志时间窗口是否覆盖报错时间段",
            "检查应用日志级别配置（是否为 INFO/DEBUG 级别）",
            "搜索日志中是否有 WARN 级别异常信息",
            "如有 traceId/请求ID，用它搜索完整调用链日志",
        ]
        result.deep_checks = [
            "检查是否有多个日志文件（应用日志/错误日志/访问日志分开存储）",
            "检查容器标准输出日志（kubectl logs）是否有未捕获的错误",
            "检查 APM/链路追踪系统中该时间段的异常记录",
        ]
        result.missing_info = [
            "完整的报错时间窗口日志（报错前后各 50 行）",
            "应用日志级别配置",
            "traceId/请求ID（如有）",
            "报错的业务现象和触发操作",
        ]
        return result

    # 2. 生成异常摘要
    first_error = error_lines[0][:200]
    error_count = len(error_lines)
    result.summary = f"日志中检测到 {error_count} 条错误信息，首条错误为：{first_error}。具体故障现象和根因需结合堆栈信息分析。"

    # 3. 证据摘录（收集全部去重后的错误段，由渲染阶段按 detail 级别截断：
    #    standard 5 条 / full 8 条；这里不硬编码 5，否则 full 模式永远达不到 8 条）
    result.evidence = error_segments if error_segments else error_lines

    # 4. 模式匹配
    matched = match_patterns(error_segments, lines)
    result.root_causes = matched

    # 4.5 证据强度评估：低证据场景自动下调置信度，避免过于自信
    has_stack_trace = any(
        re.search(r"(^\s*at\s+\w+\.|Caused by:|Traceback \(most recent|^\s*File \".*\", line)", seg, re.MULTILINE)
        for seg in error_segments
    )
    result.has_stack_trace = has_stack_trace
    error_segment_count = len(error_segments)
    total_error_lines = len(error_lines)

    # 判断证据强度
    evidence_strong = has_stack_trace and error_segment_count >= 1 and total_error_lines >= 2
    evidence_medium = has_stack_trace or (error_segment_count >= 2 and total_error_lines >= 3)

    confidence_downgrade_map = {"high": "medium", "medium": "low", "low": "low"}
    any_adjusted = False

    for rc in matched:
        original = rc.pattern.confidence
        if not evidence_strong:
            if original == "high" and not evidence_medium:
                rc.adjusted_confidence = "medium"
                rc.confidence_adjusted = True
                any_adjusted = True
            elif original == "medium" and not evidence_medium:
                rc.adjusted_confidence = "low"
                rc.confidence_adjusted = True
                any_adjusted = True
            else:
                rc.adjusted_confidence = original
        else:
            rc.adjusted_confidence = original

    result.any_confidence_adjusted = any_adjusted

    # 5. 汇总排查和修复建议
    immediate_set = []
    deep_set = []
    seen_immediate = set()
    seen_deep = set()

    for rc in matched:
        for cmd in rc.pattern.troubleshoot:
            if cmd not in seen_immediate:
                immediate_set.append(cmd)
                seen_immediate.add(cmd)
        for cmd in rc.pattern.deep_troubleshoot:
            if cmd not in seen_deep:
                deep_set.append(cmd)
                seen_deep.add(cmd)
        result.fixes.append((
            rc.pattern.fix_immediate,
            rc.pattern.fix_short,
            rc.pattern.fix_long,
        ))

    result.immediate_checks = immediate_set
    result.deep_checks = deep_set

    # 如果匹配到模式，优化摘要（只统计主推测；同证据的备选模式不当作独立故障）
    if matched:
        primary = [rc for rc in matched if not rc.is_alternative]
        if primary:
            categories = [rc.pattern.name for rc in primary]
            evidence_note = ""
            if any_adjusted:
                stack_note = "无堆栈信息" if not has_stack_trace else "堆栈信息不完整"
                evidence_note = f"（{stack_note}，证据有限，根因置信度已保守下调，建议补充完整日志后重新分析）"

            if len(primary) >= 2:
                # 多故障场景：判断是否为连锁反应还是独立故障
                categories_str = "、".join(categories)
                fault_note = f"日志中检测到 {len(primary)} 个错误模式（{categories_str}），可能存在连锁反应或多个独立故障，建议逐一排查确认关联关系。"
                result.summary = f"{fault_note}{evidence_note}"
            else:
                result.summary = f"检测到错误模式：{categories[0]}。{primary[0].pattern.root_cause}。{evidence_note}"

    # 5.5 故障链分组：同一时间链上的模式合并呈现（主因/诱因/结果）
    assign_fault_chains(result.root_causes)

    return result


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="故障日志诊断专家 - 自动分析日志并生成结构化故障报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python log_analyzer.py app.log
  python log_analyzer.py app.log -o report.md
  python log_analyzer.py app.log --detail brief          # 简洁输出
  python log_analyzer.py app.log --detail full --deep     # 深度分析
  python log_analyzer.py app.log --format json             # JSON 格式输出
  python log_analyzer.py app.log --sections summary,root_causes,fixes
  cat app.log | python log_analyzer.py
        """,
    )
    parser.add_argument("input_file", nargs="?", help="日志文件路径（不指定则从 stdin 读取）")
    parser.add_argument("-o", "--output", help="输出报告文件路径（不指定则输出到 stdout）")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示详细分析过程")
    parser.add_argument("--detail", choices=["brief", "standard", "full"], default="standard",
                        help="输出详细程度：brief(简洁)/standard(标准)/full(完整)，默认 standard")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown",
                        help="输出格式：markdown/json，默认 markdown")
    parser.add_argument("--sections", type=str, default=None,
                        help="指定输出章节，逗号分隔，可选：summary,evidence,root_causes,correlation,troubleshoot,fixes,missing_info,extended")
    parser.add_argument("--deep", action="store_true",
                        help="深度分析模式：更多排查步骤(8条)、修复建议含风险评估和回滚方案、监控配置建议")
    parser.add_argument("--max-lines", type=int, default=500,
                        help="最大处理行数，超过则智能截取保留错误和堆栈（默认 500，设为 0 表示不限制）")
    parser.add_argument("--patterns", type=str, default=None,
                        help="自定义错误模式 JSON 文件路径，与内置模式合并使用（用于支持新框架/新异常类型）")
    parser.add_argument("--quick", action="store_true",
                        help="快速模式：只扫描错误行和关键信息，不提取完整堆栈，处理更快（适合超大日志快速定位）")
    parser.add_argument("--timeout", type=int, default=30,
                        help="处理超时时间（秒，默认 30），超时后自动降级为快速模式并提示")
    parser.add_argument("--explain", action="store_true",
                        help="术语解释模式：报告末尾自动添加专业术语通俗解释章节，适合非技术用户阅读")
    parser.add_argument("--version", action="store_true",
                        help="显示模式库版本信息")

    args = parser.parse_args()

    # 显示版本信息
    if args.version:
        print(f"故障日志诊断专家 v{PATTERN_LIBRARY_VERSION}")
        print(f"错误模式库版本: {PATTERN_LIBRARY_VERSION} (更新于 {PATTERN_LIBRARY_UPDATED})")
        print(f"内置错误模式数量: {PATTERN_LIBRARY_COUNT} 种")
        print(f"术语解释数量: {len(GLOSSARY)} 个")
        print(f"支持日志格式: 纯文本/JSON/Java堆栈/Python traceback/Go panic/数仓/中间件/中文告警")
        sys.exit(0)

    # 加载自定义模式
    if args.patterns:
        custom_patterns = load_custom_patterns(args.patterns)
        ERROR_PATTERNS.extend(custom_patterns)

    # 读取日志
    if args.input_file:
        if os.path.isdir(args.input_file):
            friendly_error("file_read", "输入的是一个文件夹，不是日志文件", "请将路径指向具体的日志文件（如 app.log），而不是目录。")
            sys.exit(1)
        log_text = None
        used_encoding = "utf-8"
        for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
            try:
                with open(args.input_file, "r", encoding=enc) as f:
                    log_text = f.read()
                used_encoding = enc
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
            except FileNotFoundError:
                friendly_error("file_not_found", args.input_file)
                sys.exit(1)
            except PermissionError:
                friendly_error("file_read", "没有权限读取这个文件", "请检查文件权限，或将文件复制到其他位置后重试。")
                sys.exit(1)
            except Exception as e:
                friendly_error("file_read", str(e))
                sys.exit(1)
        if log_text is None:
            friendly_error("encoding", "无法识别文件编码", "请尝试将文件另存为 UTF-8 编码，或者直接复制日志内容粘贴到新的文本文件中。")
            sys.exit(1)
        if used_encoding != "utf-8":
            print(f"[INFO] 检测到文件编码为 {used_encoding}，已自动转换读取", file=sys.stderr)
    else:
        # 从 stdin 读取
        if sys.stdin.isatty():
            parser.print_help()
            friendly_error("no_input")
            sys.exit(1)
        log_text = sys.stdin.read()

    # 验证日志内容
    is_valid, validation_msg = validate_log_content(log_text)
    if not is_valid:
        friendly_error(validation_msg)
        sys.exit(1)
    if validation_msg == "no_errors":
        print("ℹ️ 这段日志前 200 行未检测到 ERROR/Exception 关键词，仍会继续分析全文，如有深层报错会一并列出。", file=sys.stderr)

    import time
    start_time = time.time()

    if args.verbose:
        print(f"[进度] 读取日志完成: {len(log_text)} 字符, {len(log_text.splitlines())} 行, 开始分析...", file=sys.stderr)

    # 执行分析（全局异常兜底）
    try:
        max_lines = args.max_lines if args.max_lines > 0 else float('inf')
        result = analyze_log(log_text, max_lines=max_lines, quick_mode=args.quick)
    except Exception as e:
        friendly_error("unknown", str(e), "分析过程中遇到意外错误。请尝试使用 --quick 快速模式，或减少日志行数后重试。")
        if args.verbose:
            import traceback
            traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    analyze_time = time.time() - start_time
    if args.verbose:
        print(f"[进度] 分析完成: 提取 {len(result.raw_errors)} 条错误, 匹配 {len(result.root_causes)} 个根因, 耗时 {analyze_time:.2f}s", file=sys.stderr)

    # 超时检测
    if analyze_time > args.timeout and not args.quick:
        timeout_note = f"⚠️ 处理耗时 {analyze_time:.1f}s 超过阈值 {args.timeout}s，建议使用 --quick 快速模式或 --max-lines 限制行数以提升处理速度。"
        if result.log_processing_note:
            result.log_processing_note = result.log_processing_note + " " + timeout_note
        else:
            result.log_processing_note = timeout_note
        if args.verbose:
            print(f"[警告] 处理超时: {analyze_time:.2f}s > {args.timeout}s", file=sys.stderr)

    # 无错误日志时增加友好提示
    if validation_msg == "no_errors" and not result.root_causes:
        no_error_note = "📝 这段日志中没有找到明显的错误信息（ERROR/Exception/报错），看起来都是正常运行记录。如果确实有问题，请提供包含报错信息的日志片段，或检查是否选错了时间段/日志文件。"
        if result.log_processing_note:
            result.log_processing_note = result.log_processing_note + " " + no_error_note
        else:
            result.log_processing_note = no_error_note

    # 构建输出配置
    sections = args.sections.split(",") if args.sections else None
    config = ReportConfig(
        detail=args.detail,
        output_format=args.format,
        sections=sections,
        deep_analysis=args.deep,
    )

    if args.verbose:
        print(f"[INFO] 输出配置：detail={args.detail}, format={args.format}, deep={args.deep}, explain={args.explain}", file=sys.stderr)

    # 生成报告
    report = generate_report(result, config)

    # 术语解释模式：添加通俗摘要和术语解释
    if args.explain and args.format == "markdown":
        plain_summary = generate_plain_summary(result)
        glossary = generate_glossary_section(report, result)
        # 在标题后插入通俗摘要，在末尾追加术语解释
        report_lines = report.split("\n")
        # 找到标题后的第一个空行位置
        insert_idx = 0
        for i, line in enumerate(report_lines):
            if line.startswith("# ") and i > 0:
                insert_idx = i + 1
                break
        if insert_idx == 0:
            insert_idx = 2  # 默认在标题后
        report_lines.insert(insert_idx, "")
        report_lines.insert(insert_idx + 1, plain_summary)
        report = "\n".join(report_lines)
        if glossary:
            report = report + glossary
        if args.verbose:
            print(f"[INFO] 术语解释模式：已添加通俗摘要和术语解释章节", file=sys.stderr)

    # 输出
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"✅ 报告已生成：{args.output}", file=sys.stderr)
        except PermissionError:
            friendly_error("file_read", "没有权限写入这个文件", "请检查输出路径是否有写入权限，或换一个目录保存报告。")
            sys.exit(1)
        except Exception as e:
            friendly_error("file_read", f"写入输出文件失败：{e}", "请检查输出路径是否正确，磁盘空间是否充足。")
            sys.exit(1)
    else:
        print(report)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹️ 已取消操作。", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        friendly_error("unknown", str(e), "遇到了意外错误。请尝试使用 --quick 快速模式，或减少日志行数后重试。如问题持续，请将日志片段和报错信息反馈给开发者。")
        sys.exit(1)
