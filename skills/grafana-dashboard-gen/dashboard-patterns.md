# 常见监控场景的面板布局模式

以下是各典型监控场景推荐的面板结构，可直接套用或按需裁剪。

---

## 1. 微服务应用监控

适用：HTTP/gRPC 服务、API 网关、Web 后端

### 面板结构

| Row | 面板 | 类型 | 核心指标 |
|-----|------|------|---------|
| 概览 | 请求速率 (QPS) | stat | `rate(http_requests_total[5m])` |
| 概览 | 错误率 | stat | `rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])` |
| 概览 | P99 延迟 | stat | `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))` |
| 概览 | 在线实例数 | stat | `count(up{job="$job"})` |
| 请求指标 | QPS 趋势 | timeseries | 按 method/status 分组的 rate 趋势 |
| 请求指标 | 延迟分布 | timeseries | P50/P90/P99 延迟趋势 |
| 资源指标 | CPU 使用率 | timeseries | `rate(process_cpu_seconds_total[5m])` |
| 资源指标 | 内存使用 | timeseries | `process_resident_memory_bytes` |
| 错误分析 | 5xx 错误数 | stat | `sum(rate(http_requests_total{status=~"5.."}[5m]))` |
| 错误分析 | 错误趋势 | timeseries | 按 status code 分组的错误趋势 |

### 推荐变量

- `datasource` — 数据源选择
- `job` — 服务名筛选
- `instance` — 实例筛选
- `method` — HTTP 方法筛选

---

## 2. Kubernetes 集群监控

适用：K8s 集群整体资源监控（依赖 node-exporter、kube-state-metrics、cAdvisor）

### 面板结构

| Row | 面板 | 类型 | 核心指标 |
|-----|------|------|---------|
| 集群概览 | Node 数量 | stat | `count(kube_node_info)` |
| 集群概览 | Pod 总数 | stat | `count(kube_pod_info)` |
| 集群概览 | CPU 总量/使用率 | stat | `sum(kube_node_status_allocatable{resource="cpu"})` |
| 集群概览 | 内存总量/使用率 | stat | `sum(kube_node_status_allocatable{resource="memory"})` |
| Node 资源 | 各 Node CPU 排名 | bargauge | `rate(node_cpu_seconds_total{mode!="idle"}[5m])` by instance |
| Node 资源 | 各 Node 内存排名 | bargauge | `node_memory_MemUsed_bytes / node_memory_MemTotal_bytes` by instance |
| Pod 资源 | Pod 资源详情 | table | Pod 名称 / CPU 请求 / CPU 使用 / 内存请求 / 内存使用 / 状态 / 重启次数 |
| 资源趋势 | 集群 CPU 趋势 | timeseries | 总量 vs 已分配 vs 实际使用 |
| 资源趋势 | 集群内存趋势 | timeseries | 总量 vs 已分配 vs 实际使用 |

### 推荐变量

- `datasource` — 数据源选择
- `node` — Node 名称筛选
- `namespace` — 命名空间筛选

---

## 3. 数据库监控（MySQL/PostgreSQL）

适用：关系型数据库实例监控

### 面板结构

| Row | 面板 | 类型 | 核心指标 |
|-----|------|------|---------|
| 概览 | 活跃连接数 | stat | `mysql_global_status_threads_running` |
| 概览 | QPS | stat | `rate(mysql_global_status_queries[5m])` |
| 概览 | 慢查询数 | stat | `rate(mysql_global_status_slow_queries[5m])` |
| 概览 | 连接使用率 | stat | `threads_connected / max_connections` |
| 连接指标 | 连接数趋势 | timeseries | 活跃 / 空闲 / 总连接数 |
| 查询指标 | QPS 趋势 | timeseries | Select / Insert / Update / Delete 分组 |
| 查询指标 | 慢查询趋势 | timeseries | 慢查询数量 |
| 资源指标 | Buffer Pool 命中率 | gauge | `Innodb_buffer_pool_read_hits` |
| 资源指标 | 磁盘使用 | timeseries | 数据大小 / 日志大小趋势 |

### 推荐变量

- `datasource` — 数据源选择
- `instance` — 数据库实例筛选

---

## 4. 中间件监控（Redis/Kafka/RabbitMQ）

适用：消息队列和缓存中间件

### Redis 面板结构

| Row | 面板 | 类型 | 核心指标 |
|-----|------|------|---------|
| 概览 | 连接数 | stat | `redis_connected_clients` |
| 概览 | 内存使用 | stat | `redis_memory_used_bytes` |
| 概览 | 命令速率 | stat | `rate(redis_commands_processed_total[5m])` |
| 概览 | Key 总数 | stat | `redis_db_keys` |
| 性能指标 | 命令延迟 | timeseries | `redis_latency_seconds` 分位数 |
| 性能指标 | 命令速率分布 | timeseries | 按 command 分组 |
| 内存指标 | 内存趋势 | timeseries | used / peak / maxmemory |
| 内存指标 | Key 过期/淘汰 | timeseries | expired / evicted 趋势 |

### Kafka 面板结构

| Row | 面板 | 类型 | 核心指标 |
|-----|------|------|---------|
| 概览 | 消息速率 | stat | `rate(kafka_topic_partition_current_offset[5m])` |
| 概览 | Consumer Lag | stat | `kafka_consumergroup_lag` |
| 概览 | Broker 在线数 | stat | `count(kafka_brokers)` |
| 生产指标 | 消息生产速率 | timeseries | 按 topic 分组 |
| 消费指标 | Consumer Lag 趋势 | timeseries | 按 group + topic 分组 |
| 消费指标 | 消费速率 | timeseries | 按 group 分组 |

---

## 5. 业务大盘

适用：业务指标监控（非基础设施指标）

### 面板结构

| Row | 面板 | 类型 | 核心指标 |
|-----|------|------|---------|
| 核心指标 | 日活用户 | stat | 业务自定义指标 |
| 核心指标 | 订单量 | stat | 业务自定义指标 |
| 核心指标 | 营收 | stat | 业务自定义指标 |
| 核心指标 | 转化率 | stat | 业务自定义指标 |
| 趋势分析 | 核心指标趋势 | timeseries | 日活 / 订单量 / 营收趋势 |
| 趋势分析 | 同比/环比 | timeseries | 与上周/上月同期对比 |
| 漏斗分析 | 转化漏斗 | bargauge | 各环节转化率 |
| 异常监控 | 异常事件 | table | 异常类型 / 时间 / 影响范围 |

### 推荐变量

- `datasource` — 数据源选择
- `region` — 地区筛选
- `product_line` — 产品线筛选

---

## 布局参考值

### gridPos 尺寸推荐

Grafana 默认 24 列网格：

| 面板宽度 | gridPos.w | 适用场景 |
|----------|-----------|---------|
| 全宽 | 24 | 趋势图、大表 |
| 2/3 宽 | 16 | 宽趋势图 |
| 半宽 | 12 | 标准趋势图 |
| 1/3 宽 | 8 | 窄趋势图 |
| 1/4 宽 | 6 | Stat 面板 |
| 1/6 宽 | 4 | 紧凑 Stat |

### 行高推荐

| 面板类型 | gridPos.h |
|----------|-----------|
| Stat | 4-6 |
| Time Series | 8 |
| Bar Gauge | 8 |
| Table | 8-12 |
| Heatmap | 8 |
| Logs | 8-12 |
