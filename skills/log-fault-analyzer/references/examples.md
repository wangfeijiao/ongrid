# 完整使用示例

> 本文件包含故障日志诊断专家的完整输入输出示例，帮助用户理解调用方式和预期输出效果。

## 示例 1：Java NullPointerException（完整演示）

### 用户输入

```
调用【故障日志诊断专家】
环境：生产
服务：order-service
traceId: abc123456
现象：用户提交订单时接口返回500，约10%的请求失败

2026-08-30 14:23:11.234 ERROR [order-service,abc123456] 8765 --- [nio-8080-exec-3] c.e.o.service.OrderService : 创建订单失败
java.lang.NullPointerException: Cannot invoke "com.example.order.model.User.getUserId()" because "user" is null
	at com.example.order.service.OrderService.createOrder(OrderService.java:87)
	at com.example.order.controller.OrderController.create(OrderController.java:45)
	at sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
	at sun.reflect.NativeMethodAccessorImpl.invoke(NativeMethodAccessorImpl.java:62)
	at org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(InvocableHandlerMethod.java:205)
2026-08-30 14:23:11.235  WARN [order-service,abc123456] 8765 --- [nio-8080-exec-3] c.e.o.controller.OrderController : 订单创建接口返回500
```

### 预期输出（结构要点）

**异常摘要**：order-service 创建订单时，user 对象为 null 调用 getUserId() 抛出 NPE，导致约 10% 的订单创建请求返回 500。

**关键证据**：
- `NullPointerException: Cannot invoke "User.getUserId()" because "user" is null`
- `at com.example.order.service.OrderService.createOrder(OrderService.java:87)`

**根因候选**：
- 🔴高置信：OrderService.java:87 未对 user 做 null 校验
- 🟡中置信：上游用户查询（RPC/缓存/DB）返回 null，需确认 user 来源

**排查清单**（立即排查）：
1. 查看 OrderService.java 第 87 行，确认 user 对象来源
2. 用 traceId abc123456 搜索完整调用链日志
3. 用失败用户的 ID 复现请求

**修复建议**：
- 立即止血：第 87 行前加 null 校验，抛出明确业务异常
- 短期修复：排查 user 为 null 的上游原因（缓存未命中未回源/RPC降级）
- 长期优化：全局异常处理 + 防御性编程规范

---

## 示例 2：Redis OOM + 消息消费失败（复合故障）

### 用户输入

```
帮我分析这个报错，环境：生产，服务：inventory-service
现象：库存扣减消息消费失败，队列积压越来越多

2026-08-30 20:15:33.456 ERROR [inventory-service,ghi789] 3210 --- [msg-consumer-1] c.e.i.mq.InventoryConsumer : 消费消息失败, msgId: MSG20260830201533001, topic: inventory_deduct
org.springframework.data.redis.RedisSystemException: Error in execution; nested exception is io.lettuce.core.RedisCommandExecutionException: OOM command not allowed when used memory > 'maxmemory'.
	at org.springframework.data.redis.core.RedisTemplate.execute(RedisTemplate.java:225)
Caused by: io.lettuce.core.RedisCommandExecutionException: OOM command not allowed when used memory > 'maxmemory'.
2026-08-30 20:15:33.458 WARN [inventory-service,ghi789] 3210 --- [msg-consumer-1] c.e.i.mq.InventoryConsumer : 消息进入重试队列, 当前重试次数: 1/5
2026-08-30 20:15:45.000 WARN [inventory-service,ghi789] 3210 --- [msg-consumer-1] c.e.i.mq.InventoryConsumer : 消费延迟增加, 队列积压: 1280 条
```

### 预期输出（结构要点）

**异常摘要**：inventory-service 消费库存扣减消息时，Redis 写入触发 OOM（内存达到 maxmemory 且禁止淘汰），消息消费失败进入重试队列，积压 1280 条且持续增长。

**关键证据**：
- `OOM command not allowed when used memory > 'maxmemory'`
- `消息进入重试队列, 当前重试次数: 1/5`
- `队列积压: 1280 条`

**根因候选**：
- 🔴高置信：Redis 内存达到 maxmemory 上限，maxmemory-policy 为 noeviction，写入被拒绝
- 🟡中置信：存在大 key 或未设 TTL 的缓存导致内存持续增长
- ⚪低置信：maxmemory 配置值过小（证据不足）

**排查清单**（立即排查）：
1. `redis-cli INFO memory` 查看内存使用量和 maxmemory
2. `redis-cli CONFIG GET maxmemory-policy` 确认淘汰策略
3. `redis-cli --bigkeys` 查找大 key
4. 手动 UNLINK 大 key 或临时调大 maxmemory 止血

**修复建议**：
- 立即止血：删除大 key 或调大 maxmemory，恢复消费
- 短期修复：将 maxmemory-policy 改为 allkeys-lru，修复未设 TTL 的缓存
- 长期优化：消费端增加 Redis 不可用时的降级逻辑，配置内存使用率告警

---

## 示例 3：信息不足的泛化报错（边界场景）

### 用户输入

```
分析这个报错
ERROR app-server: 处理请求失败
ERROR app-server: 系统内部错误
```

### 预期输出（结构要点）

**异常摘要**：app-server 记录了"处理请求失败"和"系统内部错误"，但日志未包含异常类型、堆栈、错误码或业务上下文，无法确定具体故障。

**关键证据**：仅两条泛化 ERROR 消息。

**根因候选**：
- ⚪低置信：【证据不足】应用内部抛出未捕获异常，但日志未记录堆栈，无法定位
- ⚪低置信：【证据不足】可能是下游依赖异常，但无调用信息

**排查清单**（立即排查）：
1. 用时间戳搜索该时间点前后 1 分钟完整日志：`grep "2026-08-30 22:00" app.log`
2. 检查全局异常处理器是否吞掉了堆栈（`log.error("xxx")` 未传异常对象）
3. 查看该时间点应用监控指标（CPU/内存/错误率）

**修复建议**：
- 修改代码中所有 `log.error("xxx")` 为 `log.error("xxx", e)`，确保堆栈被记录
- 全局异常处理器统一打印完整堆栈和 traceId

**缺失信息**（明确标注）：
- 【严重缺失】异常堆栈——无法确定异常类型和根因
- 【严重缺失】traceId——无法关联调用链
- 缺少请求参数和业务上下文——无法复现

---

## 示例 4：数仓 ETL 任务失败（Spark SQL）

### 用户输入

```
日志故障分析，任务ID: task_20260830_001，环境：生产数仓

2026-08-30 03:15:22 INFO org.apache.spark.sql.execution.command.AlterTableRecoverPartitionsCommand: Recovering all partitions for table dwd.dwd_order_detail
2026-08-30 03:15:23 ERROR org.apache.spark.sql.hive.client.HiveClientImpl: org.apache.hadoop.hive.ql.metadata.InvalidTableException: Table dwd.dwd_order_detail not found
2026-08-30 03:15:23 ERROR org.apache.spark.deploy.yarn.ApplicationMaster: User class threw exception: org.apache.spark.sql.AnalysisException: Table or view not found: dwd.dwd_order_detail; line 1 pos 14
2026-08-30 03:15:23 INFO org.apache.spark.deploy.yarn.ApplicationMaster: Final app status: FAILED, exitCode: 15
2026-08-30 03:15:24 ERROR com.xxx.etl.scheduler.TaskExecutor: 任务[dwd_order_detail_etl]执行失败
```

### 预期输出（结构要点）

**异常摘要**：数仓 ETL 任务 dwd_order_detail_etl 在 Spark on YARN 上执行时，Hive 表 dwd.dwd_order_detail 不存在，导致 SQL 解析失败，任务状态 FAILED（exitCode: 15）。

**关键证据**：
- `InvalidTableException: Table dwd.dwd_order_detail not found`
- `AnalysisException: Table or view not found: dwd.dwd_order_detail`
- `Final app status: FAILED, exitCode: 15`

**根因候选**：
- 🔴高置信：Hive Metastore 中不存在该表（未创建/被删除/库名错误）
- 🟡中置信：Spark 连接了错误的 Metastore（测试环境而非生产）
- ⚪低置信：表名拼写错误（证据不足）

**排查清单**（立即排查）：
1. Hive 客户端确认表是否存在：`SHOW TABLES IN dwd LIKE 'dwd_order_detail';`
2. 检查 Spark 任务的 Metastore 配置：`cat hive-site.xml | grep hive.metastore.uris`
3. 检查近期是否有 DROP TABLE 操作（审计日志）
4. HDFS 层面确认数据目录：`hdfs dfs -ls /user/hive/warehouse/dwd.db/`

---

## 输出效果说明

以上示例展示了本助手的标准输出模式：
1. **先证据后结论**：所有根因候选都标注了对应的日志原文证据
2. **置信度分层**：高/中/低置信一目了然，低置信必须标注【证据不足】
3. **排查可执行**：每步都有具体命令，开发/测试可直接复制执行
4. **修复分层次**：立即止血 → 短期修复 → 长期优化
5. **缺失信息明确**：信息不足时明确告诉用户需要补充什么，而非简单说"证据不足"
