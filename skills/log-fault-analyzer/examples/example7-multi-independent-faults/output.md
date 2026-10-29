# 故障分析报告

## 1.异常摘要

日志中检测到 4 个错误模式（Redis 内存溢出 (OOM)、SQL 执行超时、中文日志/告警：接口或服务异常、Kafka 消费者重平衡中），可能存在连锁反应或多个独立故障，建议逐一排查确认关联关系。

## 2.关键日志证据摘录

> 2026-08-30 18:00:01.050 ERROR [user-service,usr-aaa111] 20001 --- [http-nio-8080-exec-1] c.e.u.service.UserService : 从Redis获取用户信息失败
org.springframework.data.redis.RedisSystemException: Error in execution; nested exception is io.lettuce.core.RedisCommandExecutionException: OOM command not allowed when used memory > 'maxmemory'.
at org.springframework.data.redis.connection.lettuce.LettuceExceptionConverter.convert(LettuceExceptionConverter.java:74)
at...

> 2026-08-30 18:00:02.000 WARN  [user-service,] 20002 --- [kafka-consumer-1] org.apache.kafka.clients.consumer.internals.AbstractCoordinator : [Consumer clientId=consumer-user-group-1, groupId=user-group] Attempt to heartbeat failed since group is rebalancing

> 2026-08-30 18:00:02.200 ERROR [user-service,usr-bbb222] 20004 --- [kafka-consumer-1] c.e.u.consumer.UserEventConsumer : 消费用户事件失败，分区被回收
org.apache.kafka.common.errors.RebalanceInProgressException: Offset commit cannot be completed since the consumer is undergoing a rebalance for auto partition assignment. You can try completing the rebalance by calling poll() and then retry the operation.
at...

> 2026-08-30 18:00:03.000 ERROR [user-service,usr-ccc333] 20005 --- [http-nio-8080-exec-2] c.e.u.service.UserService : 用户订单查询失败
org.springframework.dao.QueryTimeoutException: StatementCallback; SQL [SELECT o.*, u.name FROM orders o LEFT JOIN users u ON o.user_id = u.id WHERE o.user_id = ? ORDER BY o.created_at DESC LIMIT 100]; query timed out after 10000 ms; nested exception is com.mysql.cj.jdbc.exceptions.MySQLTimeoutException: Statement cancelled due to timeout or client request
at...

> 2026-08-30 18:00:04.001 ERROR [user-service,usr-ddd444] 20007 --- [http-nio-8080-exec-3] c.e.u.controller.UserController : 登录接口返回500，errorCode=AUTH_001, errorMsg=缓存服务不可用

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 4 个错误模式：4 个独立故障（无因果关联，建议分别排查）。

- 🔴高置信：Redis 内存使用量达到 maxmemory 上限，且淘汰策略为 noeviction，写入命令被拒绝，依据：`2026-08-30 18:00:01.050 ERROR [user-service,usr-aaa111] 20001 --- [http-nio-8080-exec-1] c.e.u.service.UserService : 从Redis获取用户信息失败 org.springframework.data.redis.RedisSystemException: Error in execution; nested exception is...`（另有备选：「权限不足（Permission denied / EACCES）」为更泛化推测，已折叠）（独立故障）
- 🔴高置信：SQL 执行时间超过 statement_timeout 阈值被强制取消，可能是慢查询或数据库负载高，依据：`2026-08-30 18:00:03.000 ERROR [user-service,usr-ccc333] 20005 --- [http-nio-8080-exec-2] c.e.u.service.UserService : 用户订单查询失败 org.springframework.dao.QueryTimeoutException: StatementCallback; SQL [SELECT o.*, u.name FROM orders o LEFT JOIN users u...`（独立故障）
- 🔴高置信：日志或告警明确指向接口超时/报错或服务不可用，优先检查目标服务状态、下游依赖性能与网络链路（区别于资源类告警的泛化推测），依据：`2026-08-30 18:00:04.001 ERROR [user-service,usr-ddd444] 20007 --- [http-nio-8080-exec-3] c.e.u.controller.UserController : 登录接口返回500，errorCode=AUTH_001, errorMsg=缓存服务不可用`（另有备选：「中文告警文本」为更泛化推测，已折叠）（独立故障）
- 🟡中置信：消费者组正在进行 Rebalance（消费者加入/退出/超时），期间消费暂停，依据：`2026-08-30 18:00:02.200 ERROR [user-service,usr-bbb222] 20004 --- [kafka-consumer-1] c.e.u.consumer.UserEventConsumer : 消费用户事件失败，分区被回收 org.apache.kafka.common.errors.RebalanceInProgressException: Offset commit cannot be completed since the consumer...`（独立故障）

## 3.5 故障关联分析

日志中检测到 4 个错误模式，需判断是**连锁反应**（一个故障引发另一个）还是**独立故障**（同时发生的不同问题）。

**连锁反应判断依据**：时间先后 + 后报错包含前故障特征 + 同一 traceId/请求ID 关联。

**独立故障判断依据**：traceId 互不相同 + 故障类型完全不同 + 同时发生无因果。

**本次故障列表：** Redis 内存溢出 (OOM)、SQL 执行超时、中文日志/告警：接口或服务异常、Kafka 消费者重平衡中

> 建议：优先排查时间最早的故障，确认其是否为根因；若各故障 traceId 不同且无因果关系，则按独立故障分别处理。

## 4.排查操作清单

### 🔹立即排查（优先做）

1. redis-cli INFO memory 查看内存使用量和 maxmemory
2. redis-cli CONFIG GET maxmemory-policy 确认淘汰策略
3. redis-cli --bigkeys 查找大 key
4. EXPLAIN ANALYZE <SQL> 查看执行计划和实际耗时
5. 检查相关表索引是否存在：\d <table>

### 🔹深度排查（上面无结果再执行）

1. 检查是否有未设置 TTL 的 key 持续增长
2. 检查内存使用趋势（是否突发增长）
3. 检查是否有热 key 或异常写入
4. 检查 RDB/AOF 持久化是否导致额外内存占用
5. 检查表膨胀（dead tuples）：SELECT relname, n_dead_tup FROM pg_stat_user_tables;

## 5.修复建议

### 方案 1

### 立即止血
- 手动 UNLINK 大 key 释放内存，或临时调大 maxmemory

- **短期/长期**：将 maxmemory-policy 改为 allkeys-lru，修复未设 TTL 的缓存；内存使用率告警（>80%），大 key 监控，缓存规范（必须设 TTL）

### 方案 2

### 立即止血
- 临时调大 statement_timeout，或为查询增加索引

- **短期/长期**：优化慢 SQL（加索引/改写查询/分页），VACUUM ANALYZE 更新统计信息；慢查询监控告警，定期审查慢查询日志，SQL 规范审查

### 方案 3

### 立即止血
- 对告警接口执行降级/重试/切换实例，先恢复可用性

- **短期/长期**：定位并修复超时根因（慢查询/下游慢/资源不足），优化接口性能；配置接口超时分级告警与自动降级策略，建立容量与性能基线

## 6.缺失信息/待补充（如果有）

- --bigkeys 分析结果
- EXPLAIN ANALYZE 结果
- INFO memory 输出
- maxmemory 和 maxmemory-policy 配置值
- statement_timeout 配置值
- 内存使用趋势监控
- 同期服务日志与链路追踪
- 告警中的具体指标值和阈值
- …共 21 条待补充信息，其余 13 条请使用 `--detail full` 查看

---

> 本报告由故障日志诊断专家自动生成，共提取 6 条错误信息，匹配 4 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
