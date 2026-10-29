# 故障分析报告

## 1.异常摘要

日志中检测到 4 个错误模式（Redis 连接被拒绝、K8s Pod 调度失败、K8s HPA 自动扩缩容异常、K8s API Server 连接异常），可能存在连锁反应或多个独立故障，建议逐一排查确认关联关系。

## 2.关键日志证据摘录

> --- 新 Pod 调度失败 ---

> 2026-08-30T23:05:00Z Warning  FailedScheduling  pod/payment-service-6f9c8d7e5f-xyz123  0/6 nodes are available: 3 Insufficient cpu, 2 Insufficient memory, 1 node(s) had taint {node-role.kubernetes.io/master: }, that the pod didn't tolerate.

> 2026-08-30T23:05:30Z Warning  FailedScheduling  pod/payment-service-6f9c8d7e5f-xyz123  0/6 nodes are available: 3 Insufficient cpu, 2 Insufficient memory, 1 node(s) had taint {node-role.kubernetes.io/master: }, that the pod didn't tolerate.

> 2026-08-30T23:10:05Z Warning  ContainerGCFailed  node/node-worker-05  failed to garbage collect containers: rpc error: code = DeadlineExceeded desc = context deadline exceeded

> 2026-08-30T23:10:10Z Warning  ImageGCFailed  node/node-worker-05  failed to garbage collect images: rpc error: code = DeadlineExceeded desc = context deadline exceeded

## 3.根因候选（按主因/诱因/结果分组）

> 📊 本次检测到 4 个错误模式：1 条故障链（共 3 个模式，含主因→诱因→结果），1 个独立故障（无因果关联，建议分别排查）。

### 🔍 故障链 1：3 个错误模式，时间跨度 2026-08-30 23:05:00 ~ 2026-08-30 23:15:00

- **主因**：🔴高置信：Pod 无法被调度到任何节点，可能是节点资源不足（CPU/内存/存储）、节点选择器不匹配、Taint/Toleration 不兼容、节点不可用或 Pod 反亲和性冲突，依据：`2026-08-30T23:05:00Z Warning  FailedScheduling  pod/payment-service-6f9c8d7e5f-xyz123  0/6 nodes are available: 3 Insufficient cpu, 2 Insufficient memory, 1 node(s) had taint {node-role.kubernetes.io/master: }, that the pod didn't tolerate.`
- **诱因**：
  - 🟡中置信：Kubernetes API Server 连接超时、TLS 握手失败或证书过期，可能是 API Server 过载、网络问题、证书过期或 etcd 故障，依据：`2026-08-30T23:10:05Z Warning  ContainerGCFailed  node/node-worker-05  failed to garbage collect containers: rpc error: code = DeadlineExceeded desc = context deadline exceeded`
- **结果**：🟡中置信：Horizontal Pod Autoscaler 无法获取指标或扩缩容异常，可能是 metrics-server 未安装、指标不可用、目标资源未设置 request 或 HPA 配置错误，依据：`2026-08-30T23:15:00Z Warning  FailedGetResourceMetric  horizontalpodautoscaler/payment-service-hpa  failed to get cpu utilization: unable to get metrics for resource cpu: unable to fetch metrics from resource metrics API: the server could not find...`

### 📌 独立故障（与上述故障链无因果关联）

- 🔴高置信：Redis 服务未运行或端口未监听，导致连接被拒绝，依据：`io.lettuce.core.RedisConnectionException: Unable to connect to 10.0.2.10:6379 at io.lettuce.core.RedisConnectionException.create(RedisConnectionException.java:78) at io.lettuce.core.AbstractRedisClient.initializeChannel(AbstractRedisClient.java:267)...`（另有备选：「连接被拒绝（Connection refused）」为更泛化推测，已折叠）（另有备选：「服务连接被拒绝」为更泛化推测，已折叠）（独立故障）

## 3.5 故障关联分析

日志中检测到 4 个错误模式，需判断是**连锁反应**（一个故障引发另一个）还是**独立故障**（同时发生的不同问题）。

**连锁反应判断依据**：时间先后 + 后报错包含前故障特征 + 同一 traceId/请求ID 关联。

**独立故障判断依据**：traceId 互不相同 + 故障类型完全不同 + 同时发生无因果。

**本次故障列表：** Redis 连接被拒绝、K8s Pod 调度失败、K8s HPA 自动扩缩容异常、K8s API Server 连接异常

> 建议：优先排查时间最早的故障，确认其是否为根因；若各故障 traceId 不同且无因果关系，则按独立故障分别处理。

## 4.排查操作清单

### 🔹立即排查（优先做）

1. systemctl status redis 检查 Redis 服务状态
2. ss -tlnp | grep 6379 确认端口是否监听
3. redis-cli ping 测试连接
4. kubectl describe pod <pod> -n <namespace> 查看 FailedScheduling 事件中的具体原因
5. kubectl get nodes 查看节点状态（Ready/NotReady）和资源使用情况

### 🔹深度排查（上面无结果再执行）

1. 查看 Redis 日志确认是否崩溃：tail -100 /var/log/redis/redis-server.log
2. 检查是否有 OOM Killer 杀掉 Redis：dmesg | grep -i redis
3. 检查磁盘空间（RDB 持久化失败可能导致 Redis 拒绝写入）
4. 检查节点资源是否真的不足：kubectl describe node <node> | grep -A5 'Allocated resources'
5. 检查 Taint/Toleration：kubectl describe node <node> | grep Taints，对比 Pod 的 tolerations

## 5.修复建议

### 方案 1

### 立即止血
- 启动 Redis 服务：systemctl start redis

- **短期/长期**：排查 Redis 崩溃原因（OOM/配置错误/磁盘满），修复后确保自启；Redis 高可用（主从/哨兵/集群），服务存活监控告警

### 方案 2

### 立即止血
- 临时调大节点资源或减少 Pod 资源请求，或添加 Toleration 匹配节点 Taint

- **短期/长期**：合理配置 Pod 的 resources.requests/limits，调整 nodeSelector/affinity，确保节点资源充足；集群容量规划和自动扩缩容（Cluster Autoscaler），节点标签和 Taint 规范，资源请求配置审查，调度失败监控告警

### 方案 3

### 立即止血
- 安装或修复 metrics-server，或为目标资源配置 resources.requests

- **短期/长期**：修复 HPA 配置，确保 metrics-server 正常运行，目标资源配置 request；HPA 配置规范（指标类型、阈值、扩缩容冷却时间），metrics-server 高可用部署，扩缩容效果监控，自定义指标（Prometheus Adapter）支持

## 6.缺失信息/待补充（如果有）

- API Server 状态和日志
- HPA 事件和警告
- HPA 名称和配置
- Pod 的 nodeSelector/affinity/tolerations 配置
- Redis 配置文件
- Redis 错误日志
- etcd 状态
- kubectl describe pod 的 FailedScheduling 事件
- …共 21 条待补充信息，其余 13 条请使用 `--detail full` 查看

---

> 本报告由故障日志诊断专家自动生成，共提取 11 条错误信息，匹配 4 个错误模式。
> 根因候选为基于日志证据的可能性推断，请通过排查清单中的命令实际验证后再下结论。
