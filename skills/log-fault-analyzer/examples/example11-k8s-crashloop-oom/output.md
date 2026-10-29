# 故障分析报告

## 1.异常摘要

日志中检测到 4 个错误模式（K8s Pod CrashLoopBackOff、数据库连接池耗尽、连接被拒绝（Connection refused）、容器 OOMKilled），可能存在连锁反应或多个独立故障，建议逐一排查确认关联关系。

## 2.关键日志证据摘录

> 2026-08-30T22:00:06.500Z ERROR order-service-7d8f9c6b5d-x2k4p order-service Failed to connect to database
org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
at org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:82)
at...

> 2026-08-30T22:00:06.600Z ERROR order-service-7d8f9c6b5d-x2k4p order-service Application startup failed
org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'orderService': Invocation of init method failed; nested exception is org.springframework.jdbc.CannotGetJdbcConnectionException
at org.springframework.beans.factory.annotation.InitDestroyAnnotationBeanPostProcessor.postProcessBeforeInitialization(InitDestroyAnnotationBeanPostProcessor.java:137)
at...

> 2026-08-30T22:00:10Z Warning  Failed     pod/order-service-7d8f9c6b5d-x2k4p  Container order-service failed liveness probe, will be restarted

> 2026-08-30T22:00:15Z Warning  BackOff    pod/order-service-7d8f9c6b5d-x2k4p  Back-off restarting failed container order-service in pod order-service-7d8f9c6b5d-x2k4p_default

> 2026-08-30T22:00:20Z Warning  Unhealthy  pod/order-service-7d8f9c6b5d-x2k4p  Liveness probe failed: Get "http://10.244.1.23:8080/actuator/health": dial tcp 10.244.1.23:8080: connect: connection refused

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 4 个错误模式：1 条故障链（共 4 个模式，含主因→诱因→结果）。

### 🔍 故障链 1：4 个错误模式，时间跨度 2026-08-30 22:00:06 ~ 2026-08-30 22:00:25

- **主因**：🔴高置信：连接池所有连接被占用且等待超时，可能是慢查询持锁不释放、连接泄漏或连接池配置过小，依据：`2026-08-30T22:00:06.500Z ERROR order-service-7d8f9c6b5d-x2k4p order-service Failed to connect to database org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is...`
- **诱因**：
  - 🔴高置信：Pod 内容器反复崩溃重启，达到 CrashLoopBackOff 状态，可能是应用启动失败、配置错误、依赖不可用或健康检查失败，依据：`2026-08-30T22:00:15Z Warning  BackOff    pod/order-service-7d8f9c6b5d-x2k4p  Back-off restarting failed container order-service in pod order-service-7d8f9c6b5d-x2k4p_default`
  - 🔴高置信：目标服务器/服务没在运行，或者端口不对，或者防火墙拦了。人话翻译：你想连的地方没人接电话。，依据：`2026-08-30T22:00:20Z Warning  Unhealthy  pod/order-service-7d8f9c6b5d-x2k4p  Liveness probe failed: Get "http://10.244.1.23:8080/actuator/health": dial tcp 10.244.1.23:8080: connect: connection refused`（另有备选：「服务连接被拒绝」为更泛化推测，已折叠）（另有备选：「K8s 健康检查探针失败」为更泛化推测，已折叠）
- **结果**：🔴高置信：容器内存使用超过 cgroup limit 被 OOM Killer 终止，可能是内存泄漏、配置 limit 过小或突发大内存占用，依据：`2026-08-30T22:00:25Z Warning  OOMKilled  pod/order-service-7d8f9c6b5d-x2k4p  Container order-service was killed (OOMKilled): memory limit exceeded`


## 3.5 故障关联分析

日志中检测到 4 个错误模式，需判断是**连锁反应**（一个故障引发另一个）还是**独立故障**（同时发生的不同问题）。

**连锁反应判断依据**：时间先后 + 后报错包含前故障特征 + 同一 traceId/请求ID 关联。

**独立故障判断依据**：traceId 互不相同 + 故障类型完全不同 + 同时发生无因果。

**本次故障列表：** K8s Pod CrashLoopBackOff、数据库连接池耗尽、连接被拒绝（Connection refused）、容器 OOMKilled

> 建议：优先排查时间最早的故障，确认其是否为根因；若各故障 traceId 不同且无因果关系，则按独立故障分别处理。

## 4.排查操作清单

### 🔹立即排查（优先做）

1. kubectl describe pod <pod-name> -n <namespace> 查看 Events 和 Last State（退出码、退出原因）
2. kubectl logs <pod-name> -n <namespace> --previous 查看上一次崩溃的容器日志
3. kubectl logs <pod-name> -n <namespace> 查看当前容器启动日志
4. 检查退出码：137=OOMKilled，1=应用错误，126=命令不可执行，127=命令未找到
5. 查看连接池监控指标（active/idle/waiting），确认是否 active=max 且 waiting>0

### 🔹深度排查（上面无结果再执行）

1. 检查 ConfigMap/Secret 挂载是否正确：kubectl get configmap <name> -o yaml
2. 检查环境变量配置是否完整：kubectl exec <pod> -- env | grep KEY
3. 检查依赖服务（DB/Redis/MQ）是否可从 Pod 内访问：kubectl exec <pod> -- nc -zv <host> <port>
4. 检查启动命令和参数是否正确：kubectl get pod <pod> -o jsonpath='{.spec.containers[0].command}'
5. 检查是否有 init container 失败导致主容器无法启动

## 5.修复建议

### 方案 1

### 立即止血
- 根据退出码定位原因，修复配置或代码后 kubectl delete pod <pod> 触发重建

- **短期/长期**：修复应用启动逻辑，增加启动失败时的明确错误日志，检查配置和依赖的可用性；配置启动探针（startupProbe）避免慢启动被 liveness 杀掉，增加 Pod 就绪检查，配置 PDB 保证滚动更新可用性

### 方案 2

### 立即止血
- 重启应用释放连接，临时调大 maximumPoolSize 和 connectionTimeout

- **短期/长期**：优化慢查询/长事务，修复连接泄漏，合理配置连接池大小；连接池监控告警（active使用率>80%、waiting>0持续告警），慢查询治理，连接泄漏检测

### 方案 3

### 立即止血
- 先启动目标服务（数据库/Redis/API），确认端口正确

- **短期/长期**：健康检查：服务启动后再发起请求；配置服务依赖管理和启动顺序，熔断降级避免连锁报错

## 6.缺失信息/待补充（如果有）

- --previous 崩溃日志
- JVM 内存参数
- OOM 前内存使用趋势
- Pod Events
- Pod 的 yaml 配置（command/env/volume）
- kubectl describe pod 输出（Events/Last State）
- 依赖服务状态
- 堆 dump 文件
- …共 22 条待补充信息，其余 14 条请使用 `--detail full` 查看

---

> 本报告由故障日志诊断专家自动生成，共提取 7 条错误信息，匹配 4 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
