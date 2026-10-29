# Grafana Dashboard JSON 结构参考

本文档描述 Grafana Dashboard JSON 的完整结构，供生成面板时参考。

---

## 顶层结构

```json
{
  "dashboard": {
    "id": null,
    "uid": "unique-string-id",
    "title": "Dashboard 标题",
    "description": "面板描述",
    "tags": ["tag1", "tag2"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 0,
    "refresh": "30s",
    "time": {
      "from": "now-1h",
      "to": "now"
    },
    "timepicker": {
      "refresh_intervals": ["5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h"],
      "time_options": ["5m", "15m", "1h", "6h", "12h", "24h", "2d", "7d", "30d"]
    },
    "templating": {
      "list": []
    },
    "annotations": {
      "list": []
    },
    "panels": []
  },
  "overwrite": true
}
```

**关键说明：**
- `id` 设为 `null` 表示创建新 Dashboard，Grafana 导入时自动分配
- `uid` 用 kebab-case，如 `go-microservice-monitor`
- `schemaVersion` 使用 39（Grafana 10+ 兼容）
- `version` 设为 0，Grafana 导入时自动递增
- `overwrite: true` 允许覆盖同 UID 的已有 Dashboard

---

## Panel 通用字段

每个面板都必须包含以下字段：

```json
{
  "id": 1,
  "title": "面板标题",
  "type": "timeseries",
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "description": "面板说明（可选）",
  "transparent": false,
  "targets": [],
  "fieldConfig": {
    "defaults": {},
    "overrides": []
  },
  "options": {}
}
```

### gridPos 规则

- `x`：列位置，0-23（24 列网格）
- `y`：行位置，从 0 递增
- `w`：面板宽度（列数）
- `h`：面板高度（单位行，每行约 30px）

**布局算法**：同一 Row 内面板从左到右排列，x 值依次递增；新 Row 从 y = 上一行 y + 上一行 h 开始。

---

## 面板类型详细字段

### stat（单个数值展示）

```json
{
  "id": 1,
  "title": "请求速率",
  "type": "stat",
  "gridPos": { "h": 4, "w": 6, "x": 0, "y": 0 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "targets": [{
    "expr": "sum(rate(http_requests_total{job=\"$job\"}[5m]))",
    "legendFormat": "",
    "refId": "A"
  }],
  "fieldConfig": {
    "defaults": {
      "unit": "reqps",
      "thresholds": {
        "mode": "absolute",
        "steps": [
          { "color": "green", "value": null },
          { "color": "yellow", "value": 100 },
          { "color": "red", "value": 500 }
        ]
      }
    },
    "overrides": []
  },
  "options": {
    "reduceOptions": { "calcs": ["lastNotNull"] },
    "orientation": "auto",
    "textMode": "auto",
    "wideLayout": true,
    "colorMode": "value"
  }
}
```

### timeseries（时间序列图，Grafana 10+ 推荐）

```json
{
  "id": 2,
  "title": "延迟趋势",
  "type": "timeseries",
  "gridPos": { "h": 8, "w": 12, "x": 6, "y": 0 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "targets": [{
    "expr": "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{job=\"$job\"}[5m])) by (le))",
    "legendFormat": "P99",
    "refId": "A"
  }],
  "fieldConfig": {
    "defaults": {
      "unit": "s",
      "custom": {
        "drawStyle": "line",
        "lineInterpolation": "smooth",
        "fillOpacity": 10,
        "pointSize": 5,
        "showPoints": "never",
        "spanNulls": true
      },
      "thresholds": {
        "mode": "absolute",
        "steps": [
          { "color": "green", "value": null },
          { "color": "red", "value": 1 }
        ]
      }
    },
    "overrides": []
  },
  "options": {
    "tooltip": { "mode": "multi", "sort": "desc" },
    "legend": { "displayMode": "list", "placement": "bottom", "calcs": ["mean", "max"] }
  }
}
```

### table（表格）

```json
{
  "id": 3,
  "title": "Pod 资源详情",
  "type": "table",
  "gridPos": { "h": 8, "w": 24, "x": 0, "y": 20 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "targets": [{
    "expr": "kube_pod_container_resource_requests{resource=\"cpu\", namespace=\"$namespace\"}",
    "format": "table",
    "instant": true,
    "refId": "A"
  }],
  "fieldConfig": {
    "defaults": {},
    "overrides": [
      {
        "matcher": { "id": "byName", "options": "Value" },
        "properties": [{ "id": "custom.cellOptions", "value": { "type": "sparkline" } }]
      }
    ]
  },
  "options": {
    "showHeader": true,
    "footer": { "show": false }
  }
}
```

### bargauge（柱状仪表盘）

```json
{
  "id": 4,
  "title": "各节点 CPU 排名",
  "type": "bargauge",
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 12 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "targets": [{
    "expr": "100 - (avg by(instance) (rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100)",
    "legendFormat": "{{instance}}",
    "refId": "A"
  }],
  "fieldConfig": {
    "defaults": {
      "unit": "percent",
      "thresholds": {
        "mode": "absolute",
        "steps": [
          { "color": "green", "value": null },
          { "color": "yellow", "value": 60 },
          { "color": "red", "value": 85 }
        ]
      }
    },
    "overrides": []
  },
  "options": {
    "displayMode": "gradient",
    "orientation": "horizontal",
    "reduceOptions": { "calcs": ["lastNotNull"] }
  }
}
```

### heatmap（热力图）

```json
{
  "id": 5,
  "title": "延迟分布",
  "type": "heatmap",
  "gridPos": { "h": 8, "w": 12, "x": 12, "y": 12 },
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "targets": [{
    "expr": "sum(rate(http_request_duration_seconds_bucket{job=\"$job\"}[5m])) by (le)",
    "format": "heatmap",
    "refId": "A"
  }],
  "fieldConfig": {
    "defaults": {
      "custom": {
        "calculate": false,
        "cellGap": 2,
        "cellRadius": 0,
        "cellValues": {},
        "color": { "exponent": 0.5, "fill": "dark-orange", "mode": "scheme", "reverse": false, "scale": "exponential", "scheme": "Oranges", "steps": 64 },
        "yBucketBound": "auto"
      }
    },
    "overrides": []
  },
  "options": {
    "calculation": {},
    "cellGap": 2,
    "cellRadius": 0,
    "color": { "exponent": 0.5, "fill": "dark-orange", "mode": "scheme", "reverse": false, "scale": "exponential", "scheme": "Oranges", "steps": 64 }
  }
}
```

### Row（行分组）

Row 是特殊的面板类型，用于将面板分组为可折叠的行：

```json
{
  "id": 6,
  "title": "请求指标",
  "type": "row",
  "gridPos": { "h": 1, "w": 24, "x": 0, "y": 4 },
  "collapsed": false,
  "panels": []
}
```

- `collapsed: false` 表示展开状态，内部面板直接出现在 `dashboard.panels` 数组中
- `collapsed: true` 表示折叠状态，内部面板放在 Row 的 `panels` 数组中
- Row 自身高度通常为 1

---

## 变量（Templating）

### 数据源变量

```json
{
  "name": "datasource",
  "type": "datasource",
  "query": "prometheus",
  "current": { "text": "Prometheus", "value": "prometheus" },
  "hide": 0,
  "includeAll": false,
  "label": "数据源"
}
```

### Prometheus Label 查询变量

```json
{
  "name": "job",
  "type": "query",
  "datasource": { "type": "prometheus", "uid": "$datasource" },
  "query": "label_values(http_requests_total, job)",
  "sort": 1,
  "multi": true,
  "includeAll": true,
  "current": { "text": "All", "value": "$__all" },
  "hide": 0,
  "label": "服务"
}
```

### 自定义变量

```json
{
  "name": "interval",
  "type": "interval",
  "query": "1m,5m,10m,30m,1h",
  "auto": true,
  "auto_count": 30,
  "current": { "text": "auto", "value": "$__auto_interval_interval" },
  "hide": 2,
  "label": "间隔"
}
```

### 变量字段说明

| 字段 | 说明 |
|------|------|
| `name` | 变量名，面板中用 `$name` 引用 |
| `type` | datasource / query / interval / custom / constant / textbox |
| `hide` | 0=显示、1=隐藏标签只显示值、2=完全隐藏 |
| `multi` | 是否支持多选 |
| `includeAll` | 是否包含"全部"选项 |
| `sort` | 0=无、1=字母升序、2=字母降序、3=数值升序、4=数值降序 |
| `refresh` | 1=按时刷新、2=面板加载时刷新 |

---

## 常用单位参考

| 指标类型 | unit 值 | 说明 |
|----------|---------|------|
| CPU 使用率 | `percent` / `percentunit` | percent=0-100, percentunit=0-1 |
| 内存 | `bytes` / `decbytes` | bytes=IEC, decbytes=SI |
| 磁盘 | `bytes` / `decbytes` | 同上 |
| 网络 | `bps` / `Bps` | bps=bit/s, Bps=byte/s |
| 请求速率 | `reqps` | 请求/秒 |
| 延迟 | `s` / `ms` | 秒/毫秒 |
| 温度 | `celsius` | 摄氏度 |
| 数量 | `none` / `short` | none=原始值, short=K/M/G 缩写 |
| 百分比 | `percent` | 0-100 |
