# 故障分析报告

## 1.异常摘要

检测到错误模式：Redis 内存溢出 (OOM)。Redis 内存使用量达到 maxmemory 上限，且淘汰策略为 noeviction，写入命令被拒绝。

## 2.关键日志证据摘录

> 2026-08-30 20:15:33.456 ERROR [inventory-service,ghi789] 3210 --- [msg-consumer-1] c.e.i.mq.InventoryConsumer : 消费消息失败, msgId: MSG20260830201533001, topic: inventory_deduct
org.springframework.data.redis.RedisSystemException: Error in execution; nested exception is io.lettuce.core.RedisCommandExecutionException: OOM command not allowed when used memory > 'maxmemory'.
at org.springframework.data.redis.core.RedisTemplate.execute(RedisTemplate.java:225)
at...

> 2026-08-30 20:15:38.500 ERROR [inventory-service,ghi789] 3210 --- [msg-consumer-2] c.e.i.mq.InventoryConsumer : 消费消息失败, msgId: MSG20260830201538002

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 1 个错误模式：1 个独立故障（无因果关联，建议分别排查）。

- 🔴高置信：Redis 内存使用量达到 maxmemory 上限，且淘汰策略为 noeviction，写入命令被拒绝，依据：`2026-08-30 20:15:33.456 ERROR [inventory-service,ghi789] 3210 --- [msg-consumer-1] c.e.i.mq.InventoryConsumer : 消费消息失败, msgId: MSG20260830201533001, topic: inventory_deduct org.springframework.data.redis.RedisSystemException: Error in execution;...`（独立故障）

## 4.排查操作清单

### 🔹立即排查（优先做）

1. redis-cli INFO memory 查看内存使用量和 maxmemory
2. redis-cli CONFIG GET maxmemory-policy 确认淘汰策略
3. redis-cli --bigkeys 查找大 key

### 🔹深度排查（上面无结果再执行）

1. 检查是否有未设置 TTL 的 key 持续增长
2. 检查内存使用趋势（是否突发增长）
3. 检查是否有热 key 或异常写入
4. 检查 RDB/AOF 持久化是否导致额外内存占用

## 5.修复建议

### 立即止血
- 手动 UNLINK 大 key 释放内存，或临时调大 maxmemory

- **短期/长期**：将 maxmemory-policy 改为 allkeys-lru，修复未设 TTL 的缓存；内存使用率告警（>80%），大 key 监控，缓存规范（必须设 TTL）

## 6.缺失信息/待补充（如果有）

- --bigkeys 分析结果
- INFO memory 输出
- maxmemory 和 maxmemory-policy 配置值
- 内存使用趋势监控

---

> 本报告由故障日志诊断专家自动生成，共提取 3 条错误信息，匹配 1 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
