---
name: dashboard
display_name: "看板工坊"
display_name_en: "Dashboard Studio"
description: "把业务指标设计成可监控的看板布局，并产出可运行代码骨架（HTML+Chart.js / Streamlit / Grafana）。支持四语与多版本方案，覆盖 KPI 卡片、图表区、刷新机制、告警与实时数据流，适用于把「分散的指标」变成「一屏掌控的看板」。"
description_zh: "把业务指标设计成可监控的看板布局，并产出可运行代码骨架（HTML+Chart.js / Streamlit / Grafana）。支持四语与多版本方案，覆盖 KPI 卡片、图表区、刷新机制、告警与实时数据流，适用于把“分散的指标”变成“一屏掌控的看板”的场景。"
description_en: "Designs business metrics into a monitorable dashboard layout and emits runnable code skeletons (HTML+Chart.js / Streamlit / Grafana). Four-language, multi-version plans covering KPI cards, chart zones, refresh mechanics, alerts and real-time data flow. For turning scattered metrics into a single screen you control."
category: data
version: 1.0.0
author: 汪老师
---

# 看板工坊（dashboard）

把用户给的业务指标与监控诉求，设计成「一屏掌控」的看板布局，并产出可运行的代码骨架与刷新/实时方案。本 Skill 只产出「方案 + 代码骨架」，不直接运行服务——运行骨架需要用户侧装对应运行时（已标注）。

## 六步工作流
1. **收简报**：读 `assets/dashboard_brief.md`，拿到指标清单、刷新诉求（静态/定时/实时）、数据源、语言。
2. **定布局**：KPI 卡片区 + 图表区 + 明细/告警区，按重要性排布。
3. **选技术**：静态（HTML+Chart.js）/ 定时刷新（Streamlit）/ 实时（WebSocket + ECharts 或 Grafana）。
4. **出三版**：static（一次性快照）/ refresh（定时刷新）/ realtime（流式推送）。
5. **五维评审**：布局 25 + 指标 25 + 刷新 20 + 交互 15 + 清晰 15，自动推荐最优版本。
6. **出代码骨架**：按刷新诉求选技术，英文注释，中性示例指标（如「日活」「转化率」）。

## 核心交付
- 看板布局草图（ASCII 栅格）+ 三版方案
- 刷新/实时数据流设计
- 代码骨架（runnable，标注所需运行时）

## 合规底线
- 不接入未授权的数据源；凭证走环境变量，不入库。
- 实时看板须限速、设断线重连，避免打爆数据源。
- 敏感指标（营收/PII）须权限控制，不公开暴露。
- 代码骨架仅作模板，运行结果与数据质量由用户负责。
