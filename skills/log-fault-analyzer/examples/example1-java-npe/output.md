# 故障分析报告

## 1.异常摘要

检测到错误模式：Java NullPointerException (对象为null)。代码中对象为 null 时调用了其方法/属性，未做 null 校验。

## 2.关键日志证据摘录

> 2026-08-30 14:23:11.234 ERROR [order-service,abc123456] 8765 --- [nio-8080-exec-3] c.e.o.service.OrderService : 创建订单失败
java.lang.NullPointerException: Cannot invoke "com.example.order.model.User.getUserId()" because "user" is null
at com.example.order.service.OrderService.createOrder(OrderService.java:87)
at com.example.order.controller.OrderController.create(OrderController.java:45)
at sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
at...

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 1 个错误模式：1 个独立故障（无因果关联，建议分别排查）。

- 🔴高置信：代码中对象为 null 时调用了其方法/属性，未做 null 校验，依据：`2026-08-30 14:23:11.234 ERROR [order-service,abc123456] 8765 --- [nio-8080-exec-3] c.e.o.service.OrderService : 创建订单失败 java.lang.NullPointerException: Cannot invoke "com.example.order.model.User.getUserId()" because "user" is null at...`（独立故障）

## 4.排查操作清单

### 🔹立即排查（优先做）

1. 查看堆栈最顶层行号，定位 null 对象的来源（DB查询/RPC调用/缓存获取）
2. 用 traceId 搜索完整调用链日志，确认对象获取环节
3. 用相同输入复现请求，观察是否稳定复现

### 🔹深度排查（上面无结果再执行）

1. 检查上游服务是否有发布/降级导致返回 null
2. 检查缓存是否过期且未回源 DB
3. 检查 DB 中该记录是否存在（可能被物理删除）

## 5.修复建议

### 立即止血
- 在调用处增加 null 校验，为 null 时抛出明确业务异常

- **短期/长期**：排查对象为 null 的上游原因，在源头增加兜底逻辑；全局异常处理 + 防御性编程规范，对所有外部依赖返回值做 null 检查

## 6.缺失信息/待补充（如果有）

- null 对象的获取代码（堆栈行号之前的逻辑）
- 上游服务日志
- 具体业务 ID/参数

---

> 本报告由故障日志诊断专家自动生成，共提取 1 条错误信息，匹配 1 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
