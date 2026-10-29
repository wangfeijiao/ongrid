dev-004 docker-container-ops

# Changelog

## 0.1.0 — 2026-10-06

- 首版交付（规格卡 dev-004 [P0·C2]）。
- scripts/docker_preflight.sh：CLI/daemon/权限/compose 四项预检，--json 信封输出。
- scripts/space_report.py：system df 解析 + 三级清理建议（只读不删），支持 --df-input 离线模式。
- references/cleanup-levels.md：三级清理判定、命令与确认门。
- tests/fixtures/df.json + golden_space_report.json（离线 golden 基线）。
- 安全：L1/L2/L3 分级确认门；禁止直接执行 docker system prune -a --volumes。
