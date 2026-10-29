# 故障分析报告

## 1.异常摘要

检测到错误模式：唯一约束冲突 (重复键)。写入数据违反唯一约束，可能是重复提交或业务逻辑未做幂等。

## 2.关键日志证据摘录

> 2026-08-30 15:30:22.101 ERROR [payment-service,pay-78901234] 12345 --- [http-nio-8080-exec-8] c.e.p.controller.PaymentController : 支付处理失败
org.springframework.transaction.UnexpectedRollbackException: Transaction silently rolled back because it has been marked as rollback-only
at org.springframework.transaction.interceptor.TransactionAspectSupport.completeTransactionAfterThrowing(TransactionAspectSupport.java:542)
at...

> 2026-08-30 15:30:22.115 WARN  [payment-service,pay-78901234] 12345 --- [http-nio-8080-exec-8] c.e.p.service.PaymentService : 支付记录保存失败，订单号 ORD-20260830-001 已存在，可能是重复支付

> 2026-08-30 15:30:22.120 INFO  [payment-service,pay-78901234] 12345 --- [http-nio-8080-exec-8] c.e.p.controller.PaymentController : 返回支付失败响应，errorCode=PAY_001, errorMsg=订单已支付

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 1 个错误模式：1 个独立故障（无因果关联，建议分别排查）。

- 🔴高置信：写入数据违反唯一约束，可能是重复提交或业务逻辑未做幂等，依据：`2026-08-30 15:30:22.101 ERROR [payment-service,pay-78901234] 12345 --- [http-nio-8080-exec-8] c.e.p.controller.PaymentController : 支付处理失败 org.springframework.transaction.UnexpectedRollbackException: Transaction silently rolled back because it has...`（另有备选：「权限不足（Permission denied / EACCES）」为更泛化推测，已折叠）（独立故障）

## 4.排查操作清单

### 🔹立即排查（优先做）

1. 查看冲突的唯一键名和重复值
2. 检查业务逻辑是否有重复写入路径
3. 检查是否有重试机制导致重复提交
4. 看报错里具体是哪个路径/端口没权限
5. 文件权限：ls -l 看权限位，chmod 修改（Linux/Mac）

### 🔹深度排查（上面无结果再执行）

1. 检查是否有并发写入同一唯一键
2. 检查上游是否有重复消息/重复请求
3. Linux: ls -la 文件路径 查看所有者和权限
4. Linux: chown -R $USER:$USER 文件路径 修改所有者
5. Windows: 用管理员身份运行终端（右键→以管理员身份运行）

## 5.修复建议

### 方案 1

### 立即止血
- 删除重复数据或修改冲突数据

- **短期/长期**：增加幂等性检查，使用 INSERT ... ON DUPLICATE KEY UPDATE 或 UPSERT；唯一键设计规范，幂等性设计审查

### 方案 2

### 立即止血
- Linux/Mac 加 sudo（临时），Windows 用管理员身份运行终端

- **短期/长期**：修改文件/目录所有者，避免每次加 sudo；遵循最小权限原则，配置用户目录下的开发环境，不用 root 跑开发

## 6.缺失信息/待补充（如果有）

- 上游请求/消息样本
- 业务写入逻辑代码
- 冲突的唯一键名和值
- 执行的具体命令
- 报错的完整路径或端口号
- 操作系统（Linux/Mac/Windows）

---

> 本报告由故障日志诊断专家自动生成，共提取 3 条错误信息，匹配 1 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
