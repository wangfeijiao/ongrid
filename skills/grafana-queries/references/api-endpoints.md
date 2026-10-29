# Grafana HTTP API 端点速查（api-endpoints.md）

触发条件：当需要为本 skill 未覆盖的端点补查询、确认 Grafana 版本差异（ruler vs
provisioned）、或写 curl 模板给用户自查时读本文。

> help 即真相：全部端点以官方文档 https://grafana.com/docs/grafana/latest/developers/http_api/
> 为准。认证统一用请求头 `Authorization: Bearer $GRAFANA_TOKEN`（服务账号 token，
> viewer 即可满足本 skill 全部只读查询）。

## 端点表（全部 GET）

| 用途 | 端点 | 说明 |
|------|------|------|
| 健康/版本 | `/api/health` | 返回 version/database/commit，预检首选 |
| 告警实时状态 | `/api/prometheus/grafana/api/v1/rules` | Grafana ≥9 统一告警；响应 `data.groups[].rules[]` 含 `state`（firing/pending/normal/nodata/error）与 `health`（ok/error/nodata） |
| 告警规则定义 | `/api/v1/provisioned/alert-rules` | 只有定义（uid/ruleGroup/folderUid/for），**无实时 state** |
| dashboard 搜索 | `/api/search?type=dash-db&query=<text>` | 返回 uid/title/url/folderTitle |
| 单个 dashboard | `/api/dashboards/uid/{uid}` | 响应 `dashboard.panels[]` |
| 数据源清单 | `/api/datasources` | 敏感面：可能含 password/database 字段，务必走脚本白名单 |

## state 与 health 的语义（告警快照解读）

- `state=firing`：条件满足、正在报警；`pending`：已超阈值但未到 `for` 时长；
  `normal`：安静。
- `health=error`：规则执行本身报错（如查询超时）——即使 state=normal 也需要点名，
  否则会漏报"监控失效"。
- 脚本把 alerting→firing、inactive/ok→normal 归一化；未知 state 落入 `other_state`
  并保留 raw_state，不要猜测语义。

## curl 模板（给用户自查用，token 走环境变量）

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/health"
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "$GRAFANA_URL/api/prometheus/grafana/api/v1/rules" | python -m json.tool
```

## 版本差异提示

- Grafana < 9 无统一告警 API（legacy `/api/alerts` 已废弃）——404 时改用
  provisioned 端点看定义，并如实说明实时状态不可得。
- 云服务（Grafana Cloud）URL 带实例 slug，端点路径相同。
