# 监控与告警体系

> 监控回答"系统现在怎么样"，告警回答"什么时候该把人叫醒"。两者的最高境界：**告警每一条都可执行，监控每一张图都有人看**。

## 监控分层（从下到上）

| 层 | 关注指标 | 工具 |
|---|---|---|
| 基础设施 | CPU/内存/磁盘/网络 | node_exporter + Prometheus |
| 中间件 | MySQL 连接/慢查询、Redis 内存/命中率 | mysqld_exporter / redis_exporter |
| 应用 | QPS/延迟/错误率（RED 三剑客） | Micrometer / OpenTelemetry |
| 业务 | 下单量/支付成功率 | 自定义指标埋点 |
| 外部视角 | 可用性拨测、证书到期 | blackbox_exporter |

**黄金信号（Google SRE）**：延迟、流量、错误、饱和度——任何服务先监控这四个。

## Prometheus 实战

### node_exporter 常用 PromQL

```promql
# CPU 使用率（%）
100 - (avg by(instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# 内存使用率（%）
(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100

# 磁盘使用率（%）
(1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes) * 100

# 磁盘 IO 饱和度
irate(node_disk_io_time_seconds_total[5m]) * 100

# 网络流量（Mbps）
irate(node_network_receive_bytes_total{device!~"lo|veth.*"}[5m]) * 8 / 1000000

# 预测磁盘 4 小时内会写满（告警神器）
predict_linear(node_filesystem_avail_bytes[6h], 4*3600) < 0
```

### 告警规则示例（prometheus rules）

```yaml
groups:
- name: host
  rules:
  - alert: HighCPU
    expr: 100 - (avg by(instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 90
    for: 5m                        # 持续 5 分钟才报，防毛刺
    labels:
      severity: warning
    annotations:
      summary: "{{ $labels.instance }} CPU 超 90%"
      runbook: "https://wiki/runbook/high-cpu"   # 每条告警配处理手册！

  - alert: DiskWillFill
    expr: predict_linear(node_filesystem_avail_bytes[6h], 4*3600) < 0
    for: 10m
    labels:
      severity: critical            # 预测型告警：在出事前报警

  - alert: InstanceDown
    expr: up == 0
    for: 1m
    labels:
      severity: critical
```

## 告警设计（80% 的告警体系死于"告警疲劳"）

**分级原则**：

| 级别 | 含义 | 通知方式 | 例子 |
|---|---|---|---|
| critical | 用户已受影响，立即处理 | 电话/PagerDuty | 核心接口错误率 >5% |
| warning | 不处理会恶化 | 钉钉/企微群 | CPU >90% 持续 5 分钟 |
| info | 知道了就行 | 邮件/报表 | 磁盘 4 小时后会满 |

**告警可执行性检查**：每条告警问自己——"半夜收到这条，我知道该干什么吗？" 不知道 → 这条告警要么配 runbook，要么删掉。

**反模式**：
- ❌ 阈值拍脑袋（先跑一周看基线再定阈值）
- ❌ 一个群里 500 条/天（人会自动忽略全部）
- ❌ 只报现象不报影响（"CPU 高"不如"接口 P99 超 2s"）

**告警抑制与静默**（Alertmanager）：

```yaml
inhibit_rules:
  - source_match: {severity: 'critical'}   # 机房挂了，抑制该机房所有 warning
    target_match: {severity: 'warning'}
    equal: ['instance']

route:
  group_by: ['alertname', 'instance']
  group_wait: 30s           # 攒 30 秒批量发，防告警风暴
  repeat_interval: 4h       # 同一告警 4 小时内不重复发
```

## 日志体系

### 方案选型

| 方案 | 适合 | 特点 |
|---|---|---|
| **Loki + Grafana** | 中小团队首选 | 只索引标签不索引全文，成本是 ELK 的 1/10 |
| **ELK/EFK** | 需要复杂全文检索 | 功能强但吃资源，ES 集群本身就要运维 |
| **ClickHouse + 自研** | 海量日志 | 写入快查询快，运维成本高 |
| 直接 grep + 日志中心 | 起步期 | 别笑，很多团队这样就够了 |

### 日志规范（比工具更重要）

```
# 结构化日志（JSON），字段统一：
{"ts":"2026-09-19T10:00:01.234+08:00","level":"ERROR","trace_id":"abc123",
 "service":"order","msg":"pay failed","user_id":1001,"err":"timeout"}

# 三个必须：
# 1. 必须带 trace_id（全链路串联）
# 2. 必须带时间戳（毫秒级，带时区）
# 3. 错误日志必须带上下文（谁、做了什么、参数是什么）——"支付失败"四个字没法查
```

### 日志切割（logrotate）

```bash
# /etc/logrotate.d/myapp
/var/log/myapp/*.log {
    daily              # 每天切
    rotate 14          # 留 14 天
    compress           # 压缩旧的
    delaycompress      # 最新一份不压（方便 tail）
    missingok
    notifempty
    copytruncate       # ⚠️ 不通知进程重开句柄，直接截断——Java 应用最省心
    # postrotate ... endscript   # 需要进程重开时用（nginx: kill -USR1）
}
```

## SLO 与错误预算

```
SLO 示例：核心接口 30 天内可用性 ≥ 99.9%（错误预算 = 43.2 分钟/月）

用途：
- 预算充足 → 可以大胆发布、做实验
- 预算烧完 → 冻结发布，全员还债（稳定性优先）
- 告警按"烧钱速度"分级：1 小时烧掉 2% 预算 = critical
```

## 搭建速查（docker-compose 最小监控栈）

```yaml
# prometheus + grafana + node_exporter，10 分钟起步
services:
  prometheus:
    image: prom/prometheus:latest
    volumes: ["./prometheus.yml:/etc/prometheus/prometheus.yml"]
    ports: ["9090:9090"]
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
    environment: ["GF_SECURITY_ADMIN_PASSWORD=changeme"]  # 上线必改
  node-exporter:
    image: prom/node-exporter:latest
    pid: host
    volumes: ["/:/host:ro,rslave"]
    command: ["--path.rootfs=/host"]
```

Grafana 仪表盘导入现成 ID：node_exporter 用 **1860**，MySQL 用 **7362**，别从零画图。
