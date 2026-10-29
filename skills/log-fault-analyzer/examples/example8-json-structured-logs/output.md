# 故障分析报告

## 1.异常摘要

日志中检测到 3 个错误模式（Redis 内存溢出 (OOM)、Kafka 无可用 broker、SQL 执行超时），可能存在连锁反应或多个独立故障，建议逐一排查确认关联关系。

## 2.关键日志证据摘录

> 2026-08-30T19:15:05.890Z ERROR [abc-json-001] user-service/cache.go:156 : Failed to get user from cache  | error: OOM command not allowed when used memory > 'maxmemory'

> 2026-08-30T19:15:06.123Z ERROR [abc-json-001] user-service/repository.go:78 : Database query failed  | error: pq: canceling statement due to statement timeout

> 2026-08-30T19:15:06.134Z ERROR [abc-json-001] user-service/auth.go:108 : Login failed  | error: failed to get user: cache OOM + db timeout

> 2026-08-30T19:15:15.000Z ERROR [abc-json-002] user-service/event.go:67 : Failed to publish user event  | error: kafka: client has run out of available brokers to talk to: dial tcp 10.0.2.15:9092: connect: connection refused

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 3 个错误模式：1 条故障链（共 2 个模式，含主因→诱因→结果），1 个独立故障（无因果关联，建议分别排查）。

### 🔍 故障链 1：2 个错误模式，时间跨度 2026-08-30 19:15:05 ~ 2026-08-30 19:15:06

- **主因**：🔴高置信：Redis 内存使用量达到 maxmemory 上限，且淘汰策略为 noeviction，写入命令被拒绝，依据：`2026-08-30T19:15:05.890Z ERROR [abc-json-001] user-service/cache.go:156 : Failed to get user from cache  | error: OOM command not allowed when used memory > 'maxmemory'`
- **结果**：🔴高置信：SQL 执行时间超过 statement_timeout 阈值被强制取消，可能是慢查询或数据库负载高，依据：`2026-08-30T19:15:06.123Z ERROR [abc-json-001] user-service/repository.go:78 : Database query failed  | error: pq: canceling statement due to statement timeout`

### 📌 独立故障（与上述故障链无因果关联）

- 🔴高置信：Kafka 客户端找不到可用 broker，通常是 broker 宕机、网络不通、bootstrap.servers 配置错误或 ACL 限制，依据：`2026-08-30T19:15:15.000Z ERROR [abc-json-002] user-service/event.go:67 : Failed to publish user event  | error: kafka: client has run out of available brokers to talk to: dial tcp 10.0.2.15:9092: connect: connection refused`（另有备选：「服务连接被拒绝」为更泛化推测，已折叠）（另有备选：「连接被拒绝（Connection refused）」为更泛化推测，已折叠）（独立故障）

## 3.5 故障关联分析

日志中检测到 3 个错误模式，需判断是**连锁反应**（一个故障引发另一个）还是**独立故障**（同时发生的不同问题）。

**连锁反应判断依据**：时间先后 + 后报错包含前故障特征 + 同一 traceId/请求ID 关联。

**独立故障判断依据**：traceId 互不相同 + 故障类型完全不同 + 同时发生无因果。

**本次故障列表：** Redis 内存溢出 (OOM)、Kafka 无可用 broker、SQL 执行超时

> 建议：优先排查时间最早的故障，确认其是否为根因；若各故障 traceId 不同且无因果关系，则按独立故障分别处理。

## 4.排查操作清单

### 🔹立即排查（优先做）

1. redis-cli INFO memory 查看内存使用量和 maxmemory
2. redis-cli CONFIG GET maxmemory-policy 确认淘汰策略
3. redis-cli --bigkeys 查找大 key
4. 检查 bootstrap.servers 配置与 broker 地址可访问性
5. 查看 broker 进程状态与日志（是否宕机/重启/磁盘满）

### 🔹深度排查（上面无结果再执行）

1. 检查是否有未设置 TTL 的 key 持续增长
2. 检查内存使用趋势（是否突发增长）
3. 检查是否有热 key 或异常写入
4. 检查 RDB/AOF 持久化是否导致额外内存占用
5. 查看 Kafka 集群元数据（kafka-topics --describe --bootstrap-server）确认 broker 状态

## 5.修复建议

### 方案 1

### 立即止血
- 手动 UNLINK 大 key 释放内存，或临时调大 maxmemory

- **短期/长期**：将 maxmemory-policy 改为 allkeys-lru，修复未设 TTL 的缓存；内存使用率告警（>80%），大 key 监控，缓存规范（必须设 TTL）

### 方案 2

### 立即止血
- 确认 broker 存活并恢复网络连通，或修正 bootstrap.servers 指向可用 broker

- **短期/长期**：修复 broker 异常（宕机重启/磁盘清理/配置纠正），重试客户端连接；为 Kafka 集群配置高可用与监控告警，客户端配置多 broker 与重试策略

### 方案 3

### 立即止血
- 临时调大 statement_timeout，或为查询增加索引

- **短期/长期**：优化慢 SQL（加索引/改写查询/分页），VACUUM ANALYZE 更新统计信息；慢查询监控告警，定期审查慢查询日志，SQL 规范审查

## 6.缺失信息/待补充（如果有）

- --bigkeys 分析结果
- EXPLAIN ANALYZE 结果
- INFO memory 输出
- bootstrap.servers 配置内容
- broker 进程状态与日志
- maxmemory 和 maxmemory-policy 配置值
- statement_timeout 配置值
- 内存使用趋势监控
- …共 17 条待补充信息，其余 9 条请使用 `--detail full` 查看

---

> 本报告由故障日志诊断专家自动生成，共提取 4 条错误信息，匹配 3 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
