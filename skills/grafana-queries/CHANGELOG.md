dev-018 grafana-queries

# Changelog

## 0.1.0 — 2026-10-06

- 首版交付（规格卡 dev-018 [P1·C2]）。
- scripts/grafana_report.py：单脚本五模式（--alerts 告警实时快照 / --provisioned 规则定义 /
  --dashboards / --dashboard <uid> / --datasources / --health），全部只读 GET；支持 5 种
  --*-input 离线模式。state 归一化（alerting→firing、inactive→normal、未知→other 保 raw_state）。
- 安全：数据源响应白名单剔除（database/user/password/secureJsonData，golden 复核 0 泄漏）；
  token 只经环境变量。
- references/api-endpoints.md：ruler vs provisioned 差异、state/health 语义、curl 模板、
  help 即真相条款。
- tests/fixtures：alert_rules.json（ruler 形态，含 firing/pending/normal/health=error）、
  provisioned_rules.json、search_dashboards.json、datasources.json（含假凭据字段）+ 4 个 golden。
