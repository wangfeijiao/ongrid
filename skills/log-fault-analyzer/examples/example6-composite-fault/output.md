# 故障分析报告

## 1.异常摘要

日志中检测到 3 个错误模式（数据库连接池耗尽、SQL 执行超时、中文日志/告警：接口或服务异常），可能存在连锁反应或多个独立故障，建议逐一排查确认关联关系。

## 2.关键日志证据摘录

> 2026-08-30 16:45:01.300 WARN  [order-service,ord-11111111] 10001 --- [http-nio-8080-exec-1] com.zaxxer.hikari.pool.HikariPool : order-pool - Connection is not available, request timed out after 30000ms.

> 2026-08-30 16:45:31.301 ERROR [order-service,ord-11111111] 10001 --- [http-nio-8080-exec-1] c.e.o.service.OrderService : 库存校验失败，数据库连接获取超时
org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: order-pool - Connection is not available, request timed out after 30000ms.
at org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:82)
at...

> 2026-08-30 16:45:31.350 ERROR [order-service,ord-22222222] 10002 --- [http-nio-8080-exec-2] c.e.o.service.OrderService : 订单查询失败
org.springframework.dao.QueryTimeoutException: StatementCallback; SQL [SELECT * FROM orders WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT 50]; query timed out after 5000 ms; nested exception is org.postgresql.util.PSQLException: ERROR: canceling statement due to statement timeout
at...

> 2026-08-30 16:45:32.001 ERROR [order-service,ord-33333333] 10006 --- [http-nio-8080-exec-3] c.e.o.controller.OrderController : 创建订单接口返回500，errorCode=SYS_001, errorMsg=系统繁忙，请稍后重试

> 2026-08-30 16:45:32.100 ERROR [order-service,ord-44444444] 10007 --- [http-nio-8080-exec-4] c.e.o.controller.OrderController : 订单查询接口返回500，errorCode=SYS_002, errorMsg=查询超时

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 3 个错误模式：1 条故障链（共 2 个模式，含主因→诱因→结果），1 个独立故障（无因果关联，建议分别排查）。

### 🔍 故障链 1：2 个错误模式，时间跨度 2026-08-30 16:45:01 ~ 2026-08-30 16:45:31

- **主因**：🔴高置信：连接池所有连接被占用且等待超时，可能是慢查询持锁不释放、连接泄漏或连接池配置过小，依据：`2026-08-30 16:45:01.300 WARN  [order-service,ord-11111111] 10001 --- [http-nio-8080-exec-1] com.zaxxer.hikari.pool.HikariPool : order-pool - Connection is not available, request timed out after 30000ms.`
- **结果**：🔴高置信：SQL 执行时间超过 statement_timeout 阈值被强制取消，可能是慢查询或数据库负载高，依据：`2026-08-30 16:45:31.350 ERROR [order-service,ord-22222222] 10002 --- [http-nio-8080-exec-2] c.e.o.service.OrderService : 订单查询失败 org.springframework.dao.QueryTimeoutException: StatementCallback; SQL [SELECT * FROM orders WHERE user_id = ? AND status...`

### 📌 独立故障（与上述故障链无因果关联）

- 🔴高置信：日志或告警明确指向接口超时/报错或服务不可用，优先检查目标服务状态、下游依赖性能与网络链路（区别于资源类告警的泛化推测），依据：`2026-08-30 16:45:32.001 ERROR [order-service,ord-33333333] 10006 --- [http-nio-8080-exec-3] c.e.o.controller.OrderController : 创建订单接口返回500，errorCode=SYS_001, errorMsg=系统繁忙，请稍后重试`（独立故障）

## 3.5 故障关联分析

日志中检测到 3 个错误模式，需判断是**连锁反应**（一个故障引发另一个）还是**独立故障**（同时发生的不同问题）。

**连锁反应判断依据**：时间先后 + 后报错包含前故障特征 + 同一 traceId/请求ID 关联。

**独立故障判断依据**：traceId 互不相同 + 故障类型完全不同 + 同时发生无因果。

**本次故障列表：** 数据库连接池耗尽、SQL 执行超时、中文日志/告警：接口或服务异常

> 建议：优先排查时间最早的故障，确认其是否为根因；若各故障 traceId 不同且无因果关系，则按独立故障分别处理。

## 4.排查操作清单

### 🔹立即排查（优先做）

1. 查看连接池监控指标（active/idle/waiting），确认是否 active=max 且 waiting>0
2. 检查数据库慢查询日志，定位长时间运行的 SQL
3. 检查是否有长事务持连接不释放（idle in transaction）
4. EXPLAIN ANALYZE <SQL> 查看执行计划和实际耗时
5. 检查相关表索引是否存在：\d <table>

### 🔹深度排查（上面无结果再执行）

1. 用 jstack 抓取线程栈，查看持有连接的线程在执行什么操作
2. 检查连接池配置（maximumPoolSize、connectionTimeout）是否合理
3. 检查是否有连接泄漏（代码中未关闭连接/未释放连接到池）
4. 检查数据库端是否有锁等待导致连接长时间不释放
5. 检查表膨胀（dead tuples）：SELECT relname, n_dead_tup FROM pg_stat_user_tables;

## 5.修复建议

### 方案 1

### 立即止血
- 重启应用释放连接，临时调大 maximumPoolSize 和 connectionTimeout

- **短期/长期**：优化慢查询/长事务，修复连接泄漏，合理配置连接池大小；连接池监控告警（active使用率>80%、waiting>0持续告警），慢查询治理，连接泄漏检测

### 方案 2

### 立即止血
- 临时调大 statement_timeout，或为查询增加索引

- **短期/长期**：优化慢 SQL（加索引/改写查询/分页），VACUUM ANALYZE 更新统计信息；慢查询监控告警，定期审查慢查询日志，SQL 规范审查

### 方案 3

### 立即止血
- 对告警接口执行降级/重试/切换实例，先恢复可用性

- **短期/长期**：定位并修复超时根因（慢查询/下游慢/资源不足），优化接口性能；配置接口超时分级告警与自动降级策略，建立容量与性能基线

## 6.缺失信息/待补充（如果有）

- EXPLAIN ANALYZE 结果
- statement_timeout 配置值
- 同期服务日志与链路追踪
- 告警涉及的服务名与接口路径
- 完整 SQL 语句
- 接口响应时间分布
- 数据库慢查询日志
- 线程栈快照
- …共 11 条待补充信息，其余 3 条请使用 `--detail full` 查看

---

> 本报告由故障日志诊断专家自动生成，共提取 5 条错误信息，匹配 3 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
