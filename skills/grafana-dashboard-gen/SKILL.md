---
name: "grafana-dashboard-gen"
display_name: "Grafana Dashboard Generator"
display_name_en: "Grafana Dashboard Gen"
description_zh: "自然语言描述自动生成 Grafana 面板 JSON 配置。当用户提到 Grafana 面板、Grafana Dashboard、监控看板、可观测性面板、Dashboard 生成、监控大屏、Grafana 配置、仪表盘生成，或想了解\"帮我生成 Grafana 面板\"\"监控看板怎么做\"\"Grafana Dashboard 怎么配\"\"帮我做个监控大屏\"时触发。不适用于 Grafana 服务器安装部署、Grafana 插件开发、告警规则配置、Prometheus 规则编写。"
description_en: "AI-powered skill: Grafana Dashboard Gen. Grafana JSON Grafana Grafana Dashboard Dashboard Grafana \" Grafana \"\" \"\"Grafana Dashboard \"\" \" Grafana Grafana Prometheus"
version: 1.0.0
agent_created: true
---

# Grafana Dashboard Generator

用自然语言描述监控需求，自动生成可直接导入的 Grafana Dashboard JSON。

可观测性配置的核心痛点是：每个新服务上线都要重复配面板，面板 JSON 结构繁琐易错，指标命名和数据源差异导致模板难以复用。本技能将自然语言转化为结构化的 Dashboard JSON，消除重复劳动。

## 你的工作方式

你是一个 Grafana 可观测性配置专家。用户描述他们想监控什么，你生成可直接导入的完整 Dashboard JSON。

流程如下：

1. **理解监控需求** — 明确监控对象、数据源、关注指标
2. **设计面板结构** — 规划行、面板布局、变量、查询
3. **生成 Dashboard JSON** — 输出完整可导入的 JSON 文件
4. **确认与调整** — 用户反馈后迭代修改

## Phase 1：理解监控需求

用户描述可能详细也可能模糊。你的目标是确定以下信息：

| 信息项 | 说明 | 缺省默认值 |
|--------|------|-----------|
| 监控对象 | 什么服务/基础设施/业务 | 需追问 |
| 数据源类型 | Prometheus / Loki / InfluxDB / Elasticsearch / CloudWatch / Datadog 等 | Prometheus |
| 数据源名称 | Grafana 中已配置的数据源名称 | 需确认，影响查询语句 |
| 关注维度 | 性能/可用性/资源/业务 | 全部 |
| 面板风格 | 紧凑汇总 vs 详细展开 | 混合 |
| 刷新间隔 | 面板自动刷新频率 | 30s |

**判断逻辑：**

- 用户只说了"帮我做个 XX 监控面板" → 用默认值填充，但**必须**确认数据源名称
- 用户提供了具体的指标名或 PromQL → 直接进入 Phase 2，无需再问
- 用户上传了现有 Dashboard JSON 想修改 → 进入修改模式（见"多轮修改"章节）

**一轮对齐原则：** 将你的理解和默认选择整理为摘要，一次性让用户确认。不要逐条追问。

## Phase 2：设计面板结构

根据监控对象选择合适的面板模式。先读取 `references/dashboard-patterns.md` 获取常见监控场景的面板布局参考。

### 面板布局原则

1. **信息层次**：顶部放关键摘要（Stat 面板），中部放趋势图（Time Series），底部放详细表（Table）
2. **Row 分组**：用 Row 将面板按逻辑分组（如：概览 / 请求指标 / 资源指标 / 错误分析）
3. **变量驱动**：为可筛选维度创建变量（如 `instance`、`job`、`namespace`），面板查询引用变量实现动态筛选
4. **合理密度**：每行 3-4 个面板，避免信息过载

### 面板类型选择

| 展示需求 | 推荐面板类型 | 适用场景 |
|----------|-------------|---------|
| 单个关键数值 | stat | 可用率、在线数、错误率 |
| 数值趋势 | time-series / timeseries | QPS、延迟 P99、CPU 使用率 |
| 分布热力图 | heatmap | 请求延迟分布 |
| 多值对比 | bargauge | 各实例资源占用排名 |
| 日志/事件 | logs / table | 错误日志、事件列表 |
| 百分比占比 | piechart / gauge | 存储使用率、配额消耗 |

### 变量设计

为面板添加变量时遵循以下规则：

- `datasource` 变量：类型为 `datasource`，让用户切换数据源
- 服务筛选变量：类型为 `query`，查询 `label_values(metric, label)` 或 `metrics(label)` 
- 变量支持多选（`multi: true`）和全选（`includeAll: true`）
- 面板查询中使用 `$variable` 引用变量值

## Phase 3：生成 Dashboard JSON

根据 Phase 2 的设计，生成完整的 Grafana Dashboard JSON。

### 生成规则

1. **JSON 结构必须完整**：包含 `dashboard` 对象（标题、uid、panels、templating、time、refresh 等）和 `overwrite` 标志
2. **面板 ID 从 1 递增**：同一 Dashboard 内 panel id 不重复
3. **查询与数据源绑定**：每个面板的 `datasource` 字段使用变量 `$datasource` 或指定数据源名称
4. **单位正确**：CPU 用 `percent`、内存用 `bytes`、延迟用 `ms`/`s`、QPS 用 `reqps`
5. **颜色语义**：错误率/延迟用红黄色系，正常指标用绿色系
6. **阈值设置**：关键指标设置阈值，让面板颜色反映状态（绿 → 黄 → 红）

### JSON 输出格式

生成的 JSON 文件保存到 `/sandbox/workspace/outputs/` 目录，文件名格式：`grafana-dashboard-{monitoring-object}.json`

JSON 顶层结构示例：

```json
{
  "dashboard": {
    "title": "Dashboard 标题",
    "tags": ["标签1", "标签2"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 0,
    "refresh": "30s",
    "time": { "from": "now-1h", "to": "now" },
    "templating": { "list": [] },
    "panels": []
  },
  "overwrite": true
}
```

**面板核心字段**（以 Time Series 为例）：

```json
{
  "id": 1,
  "title": "请求速率",
  "type": "timeseries",
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "targets": [{
    "expr": "rate(http_requests_total{job=\"$job\"}[5m])",
    "legendFormat": "{{method}} {{status}}",
    "refId": "A"
  }],
  "fieldConfig": {
    "defaults": {
      "unit": "reqps",
      "custom": { "drawStyle": "line", "fillOpacity": 10 }
    },
    "overrides": []
  }
}
```

### 数据源查询语法

不同数据源的查询语法差异很大，生成时必须匹配：

- **Prometheus**：使用 PromQL（`rate()`, `histogram_quantile()`, `sum by` 等）
- **Loki**：使用 LogQL（`{app=\"$app\"} |= \"error\"`）
- **InfluxDB**：使用 InfluxQL 或 Flux
- **Elasticsearch**：使用 Lucene 或 KQL
- **CloudWatch**：使用 Namespace/MetricName/Dimensions 结构

如果不确定用户的数据源查询语法，读取 `references/grafana-json-structure.md` 获取详细参考。

## Phase 4：确认与调整

生成 JSON 后：

1. **展示摘要**：用简洁的表格或列表说明生成的面板结构（行名、面板名、面板类型、核心指标）
2. **提示导入方式**：Grafana → Dashboards → Import → Upload JSON file
3. **等待反馈**：用户可能要求调整指标、增删面板、修改变量等

## 多轮修改

用户对已生成的 Dashboard 提出修改时：

1. **增量修改**：只修改用户要求的部分，不要重新生成整个 JSON
2. **定位面板**：通过面板标题或 ID 精确定位要修改的面板
3. **常见修改**：
   - 增删面板 → 调整 `gridPos` 布局和 panel id
   - 修改查询 → 调整 `targets[].expr`
   - 添加变量 → 在 `templating.list` 中添加，面板中用 `$var` 引用
   - 调整阈值 → 修改 `fieldConfig.defaults.thresholds`

如果用户上传了现有 Dashboard JSON 想修改，先解析 JSON 理解当前结构，再按用户要求调整。

## 工作流示例

### 示例 1：微服务监控面板

**用户输入：** "帮我做一个 Go 微服务的监控面板，数据源是 Prometheus"

**执行流程：**

1. 确认：Go 微服务 + Prometheus，覆盖 RED 指标（Rate/Errors/Duration）和资源指标
2. 设计面板：
   - Row 1「概览」：4 个 Stat 面板（QPS / 错误率 / P99 延迟 / 在线实例数）
   - Row 2「请求指标」：Time Series（QPS 趋势）、Time Series（延迟分布）
   - Row 3「资源指标」：Time Series（CPU）、Time Series（内存）、Time Series（Goroutine 数）
   - Row 4「错误分析」：Stat（5xx 数量）、Logs Panel（错误日志）
3. 变量：`datasource`（数据源）、`job`（服务名）、`instance`（实例）
4. 生成 JSON → 保存到 `/sandbox/workspace/outputs/grafana-dashboard-go-microservice.json`

### 示例 2：Kubernetes 集群监控面板

**用户输入：** "我需要一个 K8s 集群监控面板，看 Node 和 Pod 的资源使用情况"

**执行流程：**

1. 确认：K8s 集群 + Prometheus（kube-state-metrics / node-exporter 指标）
2. 设计面板：
   - Row 1「集群概览」：Stat（Node 数 / Pod 数 / CPU 总量 / 内存总量）
   - Row 2「Node 资源」：Bar Gauge（各 Node CPU 使用率排名）、Bar Gauge（各 Node 内存排名）
   - Row 3「Pod 资源」：Table（Pod 名称 / CPU / 内存 / 状态 / 重启次数）
   - Row 4「资源趋势」：Time Series（集群 CPU 趋势）、Time Series（集群内存趋势）
3. 变量：`datasource`、`node`、`namespace`
4. 生成 JSON → 保存到 `/sandbox/workspace/outputs/grafana-dashboard-k8s-cluster.json`

## 资源目录

### references/

- `dashboard-patterns.md` — 常见监控场景的面板布局模式（微服务、K8s、数据库、中间件、业务大盘），按需加载
- `grafana-json-structure.md` — Grafana Dashboard JSON 完整结构参考（面板类型字段、变量格式、gridPos 规则），不确定字段时加载

### scripts/

- `validate_dashboard.py` — 校验生成的 Dashboard JSON 结构完整性（检查必填字段、panel id 唯一性、gridPos 合法性），生成后自动执行

