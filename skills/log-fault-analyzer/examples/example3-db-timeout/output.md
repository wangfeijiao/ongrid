# 故障分析报告

## 1.异常摘要

检测到错误模式：SQL 执行超时。SQL 执行时间超过 statement_timeout 阈值被强制取消，可能是慢查询或数据库负载高。

## 2.关键日志证据摘录

> 2026-08-30 16:45:05.500 ERROR [product-service,jkl012] 4567 --- [http-nio-8080-exec-15] c.e.p.service.ProductService : 查询商品详情失败
org.springframework.dao.QueryTimeoutException: SQL query timed out; nested exception is org.postgresql.util.PSQLException: ERROR: canceling statement due to statement timeout

> Caused by: org.postgresql.util.PSQLException: ERROR: canceling statement due to statement timeout

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 1 个错误模式：1 个独立故障（无因果关联，建议分别排查）。

- 🔴高置信：SQL 执行时间超过 statement_timeout 阈值被强制取消，可能是慢查询或数据库负载高，依据：`2026-08-30 16:45:05.500 ERROR [product-service,jkl012] 4567 --- [http-nio-8080-exec-15] c.e.p.service.ProductService : 查询商品详情失败 org.springframework.dao.QueryTimeoutException: SQL query timed out; nested exception is org.postgresql.util.PSQLException...`（独立故障）

## 4.排查操作清单

### 🔹立即排查（优先做）

1. EXPLAIN ANALYZE <SQL> 查看执行计划和实际耗时
2. 检查相关表索引是否存在：\d <table>
3. 查看数据库当前活动：SELECT * FROM pg_stat_activity WHERE state != 'idle';

### 🔹深度排查（上面无结果再执行）

1. 检查表膨胀（dead tuples）：SELECT relname, n_dead_tup FROM pg_stat_user_tables;
2. 检查是否有长事务持锁：SELECT pid, xact_start FROM pg_stat_activity WHERE state = 'idle in transaction';
3. 检查数据库服务器资源（CPU/IO/内存）

## 5.修复建议

### 立即止血
- 临时调大 statement_timeout，或为查询增加索引

- **短期/长期**：优化慢 SQL（加索引/改写查询/分页），VACUUM ANALYZE 更新统计信息；慢查询监控告警，定期审查慢查询日志，SQL 规范审查

## 6.缺失信息/待补充（如果有）

- EXPLAIN ANALYZE 结果
- statement_timeout 配置值
- 完整 SQL 语句
- 表结构和索引信息

---

> 本报告由故障日志诊断专家自动生成，共提取 2 条错误信息，匹配 1 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
