# most_agent 功能移植 ongrid 技术评估

> 任务阶段：阶段二（架构师技术评估） · 承接阶段一（产品经理可移植候选清单）
> 评估人：高见远（架构师） · 日期：2026-10
> 上游依据：`deliverables/most-agent-功能模块与可移植候选.md`（许清楚，阶段一）
> 核验方式：对 `D:\code\AI\ongrid` 仓库逐项 Grep/Read，结论均附文件路径与关键行号；无法核实处标注「未核实」
> 约束：本评估只做设计建议，不写实现代码、不修改仓库任何文件

---

## 0. TL;DR

> **产品经理的 P0 判断（③ SNMP 深度监控）成立且改动面比预期更大（需新增接口计数器时序 + OID 字典 + 自定义 OID 下发 + NOC 大屏，约 25–35 人日）；④ IM 审批卡片的投入被低估（ongrid 双向 IM 目前仅支持文本、无卡片/按钮/回调，需新造卡片能力，约 12–18 人日）；⑧ 告警降噪被高估（ongrid 已具备去重/静默/抑制/降噪发送策略，只剩「自定义抑制规则表 + 告警升级 + 事件聚类」的增量）；②/①/⑥/⑩/⑦/⑨ 产品经理判断基本成立；⑭ 的结论从「待验证」修正为「架构不适用」（ongrid 不做 SSH 客户端进设备）。**

---

## 1. 代码核验结论（逐项）

> 表格「产品经理判断」列指阶段一文档的结论；「核验结论」给出成立/部分成立/不成立及修正说明。

| # | 候选功能 | 核验事实与证据（文件路径:行） | 产品经理判断 | 核验结论 |
|---|---|---|---|---|
| ③/⑪/⑫ | SNMP 多厂商 OID 深度监控 | `internal/edgeagent/biz/snmp_probe.go` L18-38 硬编码约 16 个标准 OID；L59-87 仅支持 v2c/v3（**无 v1**）；L130-202 接口采集为只读 `BulkWalk`，产物 `tunnel.NetworkInterfaceReport` 只存 **最新值**（InOctets/OutOctets/InErrors/OutErrors 为 uint64 快照，见 `internal/manager/model/device/network.go` L85-103）。`network_discovery.go` L233-288 轮询只做 snapshot + `ReplaceNetworkInterfaces`。无 OID 字典、无自定义 OID 下发、无厂商→OID 映射、无吞吐速率（rate 需时序，当前无）。Vendor 仅存字符串（network.go L11）。 | 高价值+低重叠，P0 | **成立，且为唯一「高价值+低重叠」集中区**。补充：SNMP 已有 v2c/v3 能力，缺 v1（老旧设备场景可加）；NOC 大屏需新增接口计数器时序采样，改动面较产品经理估计更大。 |
| ④ | 移动端 IM 审批卡片（HITL） | `internal/manager/biz/imbridge/model` 仅 4 个双向 IM bot：feishu/dingtalk/telegram/slack（`model/imbridge/model.go` L20-31）；**无企微双向 bot**。`sender.go` L15-26 Sender 仅 `SendText`/`EditText`；`provider/feishu/stream.go` L67 明确「S1 text-only，cards dropped silently」；全仓 grep card/button/interactive 无卡片发送能力。审批闸门独立存在：`biz/approval/usecase.go` L86 Propose / L129 Approve（executor 按 Kind）/ L172 Reject，HTTP 仅 admin（`server/approval/http.go` L105 requireAdmin）。 | 高价值+低投入，P1，复用 5 渠道 | **部分成立，投入被低估**。渠道事实修正：双向 IM 是 **4** 个（无企微），另有一组 6 个**单向告警通知渠道**（feishu/wecom/dingtalk/slack/telegram/webhook，见 `internal/pkg/notify/webhook.go` L29-185 + `model/alert/model.go` L196-203）。「审批前移 IM 卡片」是**全新能力**（新造 SendCard + 各平台交互卡片 + 按钮回调），复杂度从「低」上调为「中」。 |
| ② | 本地私有化大模型接入 | `internal/pkg/llm/router.go` L33-41 ProviderConfig 已含 `BaseURL`+`TLSInsecure`；`web/src/pages/settings/LLM.tsx` L25 LLMProviderID 已含 `custom`（通用 OpenAI-compatible），L37-49 定义 provider 元信息。Ollama/vLLM 均暴露 OpenAI 兼容端点（/v1），技术上已可接。 | 部分重叠，P1，补 preset/文档 | **成立**。移植收敛为「新增 ollama/vllm 两个 preset 卡 + 文档 + 隔离内网引导」，低复杂度。 |
| ⑧ | 告警降噪/聚合策略 | `model/alert/model.go` L232 `Incident.DedupeKey`（uniqueIndex 去重）；L276 `Silence` 静默表；`biz/alert/inhibit.go` L33-59 `BuiltinInhibitor`（edge_offline 抑制 host、prom_ingest_fail 抑制 scrape_down，**当前仅硬编码 2 条，注释 L26 明示「future inhibition_rules table」**）；`model.go` L326-338 `NotifyWindowSeconds`+`NotifyMinFires`（发送策略降噪）；`biz/alert/pipeline.go` L147 cooldown 默认 10min、L207「30s ticker + 30s evaluator tick」；`model.go` L27-35 `EventTypeRepeatSuppressed/Inhibited`；`RuleKindMetricRaw`=任意 PromQL（L96）。 | 部分重叠，降噪聚合待验证，P1 | **不成立（高估）**。ongrid 已具备去重、静默、抑制、降噪发送策略、cooldown，缺的是：①admin 自定义抑制规则表（当前硬编码）；②告警升级 escalation（全仓 grep 无）；③多事件聚类为单条「风暴通知」。评估粒度 30s（vs most_agent 10s）。价值收敛为**增量增强**，优先级从 P1 降为 P1 末位或 P2。 |
| ⑬ | 自愈二次探活 + Post-Mortem | 远程执行：`biz/operatorrun/service.go` L676-803 已含 ping/dig/tcp/http 探测原语（`tcp` 可探 SSH 端口）；`biz/aiops/model/operation.go` L23-80 `Operation`+`OperationArtifact`（报告/复盘产物基础设施已存在）；审批闸门 + `chat_mutating_proposals`（`model/aiops/mutating_proposal.go` L44-128）软件审查器。全仓 grep postmortem/二次探活/自愈闭环：**无自动化「执行→二次探活→判定恢复→生成复盘」编排**（仅知识库 `builtin_vault/concepts/incident-response.md` 有 postmortem 概念文档）。 | 部分重叠，验证/复盘待验证，P1 | **成立，但需明确「雏形已存在」**。探活原语（ping/tcp/http/SNMP）与报告产物（OperationArtifact）均有，缺的是**编排层**：执行后自动触发二次探活、判定恢复、生成 Post-Mortem 报告。是「新编排」而非「新原语」，复杂度中等。 |
| ⑤ | IPAM 子网规划 | 全仓 grep ipam/subnet/vlan/address_pool 无业务代码（仅知识库 markdown 提及）。拓扑为 typed property graph（`model/topology/model.go` L43-78 Node/Relation），是故障传播图，**非 IPAM 数据模型**。 | 空白，P2 | **成立**。IPAM 是**全新模块**（子网/VLAN/地址池/预留/回收表 + CIDR 算法 + 前端），可选 link 到 topology 的 rack node，而非扩展拓扑。 |
| ⑥ | 分库隔离思想（app/metrics/oid） | `internal/pkg/dbx/dbx.go` L49-58：MySQL 默认 + SQLite opt-in（glebarez/sqlite 纯 Go）；`cmd/ongrid/bootstrap_storage.go` L57-81 单库 RunMigrations；指标走外部 Prometheus/ClickHouse（`internal/pkg/prom*`）。 | 部分重叠，P2 | **成立（且基本已实现）**。ongrid 已有「业务 MySQL vs 指标 Prometheus/ClickHouse」分层，SQLite 轻量后端已存在。oid 字典在 ongrid 是业务数据（新增 GORM 表或 go:embed 内置 map），无需独立物理库。⑥ 本质「已满足」，仅剩 oid 字典落位取舍。 |
| ① | 单二进制 go:embed 交付范式 | `Makefile` L272-307：`CGO_ENABLED=0` 交叉编译 linux/darwin amd64/arm64 四目标；全仓 `go:embed` 仅 4 处（authz/grafana/builtin_vault），**前端不内嵌**；edge 无 UI（`cmd/ongrid-edge` 纯 Go）。 | 部分重叠，P2 | **成立**。edge 已是零依赖单二进制；most_agent 的「go:embed 前端内嵌」在 ongrid 无落点（edge 无 UI、manager 前端为中心化 web）。① 无移植动作，仅工程规范确认。 |
| ⑩ | WebShell 高危命令拦截 | `internal/edgeagent/cmdpolicy/`（policy.go/sandbox.go）已含 deny-list、`rm -rf /` 拦截、binary 分级、network allowlist、awk 逃逸（阶段一已实证）。注：ongrid 的 shell 是**反向隧道 PTY**（`MethodShellOpen/Input`，`internal/pkg/tunnel/messages.go` L73-78），非 SSH 客户端。 | 重复，不建议 | **成立**。ongrid 安全能力不弱于 most_agent 的「7 类拦截」，路线不同（PTY 命令策略 vs SSH 客户端过滤）但目标一致。不建议移植。 |
| ⑦ | 3D 机房数字孪生 | 全仓无 three.js（`web/package.json` L18-30 仅 recharts/xterm/@xyflow/react）；定位偏离 AI 编排。 | 不建议 | **成立**。不建议。 |
| ⑨ | VM 纳管（vSphere/Proxmox/KVM） | 全仓 grep vsphere/proxmox/kvm 无业务代码；ongrid 已有 K8s 生命周期（`internal/edgeagent/k8s/`、`internal/manager/biz/k8s/`）。 | 不建议 | **成立**。不建议。 |
| ⑭ | 老旧设备 SSH 算法兼容 | 全仓 grep diffie-hellman/kex/ssh-rsa/HostKeyAlgorithms 于 webshell/tunnel **无命中**；ongrid 不做「SSH 客户端进网络设备」（webshell 是反向隧道 PTY，网络设备交互走 SNMP 而非 SSH）。 | 不建议（待验证） | **不成立（修正）**。结论从「待验证」修正为「**架构不适用**」：most_agent 的 WebShell 是 SSH 客户端模型，ongrid 是反向隧道 PTY 模型，无 SSH 算法栈可谈。 |

---

## 2. 逐项移植评估表

> 复杂度：低（≤3 人日）/ 中（3–15 人日）/ 高（>15 人日）。

| 候选功能 | 实现复杂度 | 估算人日 | 潜在风险 | 关键依赖 | 与 ongrid 架构兼容性 | 结论 |
|---|---|---|---|---|---|---|
| ③ SNMP 多厂商 OID 深度监控（含⑪自定义 OID 下发、⑫三合一探测、NOC 大屏） | 高 | **25–35** | ①接口计数器时序采样量大（若存 MySQL 需分区/限流，建议写 Prometheus remote_write）；②v1 老设备兼容需 gosnmp Version1 扩展；③自定义 OID 下发经反向隧道，超时/分页需谨慎；④NOC 大屏实时性 vs 30s 轮询的延迟落差 | 已有 gosnmp v1.41.0（go.mod L20）、已有 tunnel `probe_network_snmp`、已有 `NetworkInterface`/`DeviceNetwork` 模型 | **高**。完全落在既有「edge SNMP probe → manager device 域 → Prometheus → 前端」链路内，属深度扩展而非新域 | **建议移植（P0）** |
| ④ IM 审批卡片（HITL） | 中 | **12–18** | ①4 平台交互卡片格式差异大（飞书卡片回调/钉钉 action card/Slack block kit buttons/Telegram inline keyboard）；②企微无双向 bot，需补企微或仅覆盖 4 渠道；③卡片按钮回调的签名校验与去重（复用现有 dedup）；④审批安全边界（回调需 admin 鉴权） | 已有 approval inbox（Kind/executor）、已有 imbridge 4 provider + inbound 分发 | **中高**。需在 Sender 接口新增 SendCard/按钮回调能力，并回调路由到 `approval.Approve/Reject`；与现有「文本流式编辑」并存 | **建议移植（P1）** |
| ② 本地大模型 preset | 低 | **2–3** | 极低；Ollama 无 API key 时需绕过「空 APIKey 跳过」逻辑（router.go L105-108 会跳过空 key provider） | 已有 custom provider + BaseURL/TLSInsecure | **高**。纯配置/文档层；需让空 key 的本地 provider 通过配置校验 | **建议移植（P1）** |
| ⑧ 告警降噪增量（自定义抑制规则表 + 升级 + 聚类） | 中 | **8–12** | ①自定义抑制规则的语义设计（匹配器 vs 优先级）；②升级策略与现有 cooldown/静默/通知路由叠加的交互复杂；③聚类可能改变现有 DedupeKey 语义，需向后兼容 | 已有 Silence/Inhibitor/dampening/cooldown/DedupeKey | **高**。纯增强现有 alert 域，不动现有去重/静默主链路 | **按需（P1 末位/P2）** |
| ⑬ 自愈二次探活 + Post-Mortem 编排 | 中 | **10–15** | ①编排闭环的状态机与超时/失败处理；②Post-Mortem 报告依赖 LLM，成本与质量需管控；③「判定恢复」的阈值/重试设计 | 已有 operatorrun 探活原语、approval、Operation/Artifact、LLM 路由 | **高**。属新编排层，复用全部既有原语，不新增协议 | **建议移植（P1）** |
| ⑤ IPAM 子网规划 | 中 | **12–18** | ①CIDR/子网拆分/地址池分配算法细节多；②与拓扑/设备的关联口径需定义；③热力图需地址使用率数据源 | 无既有依赖；可复用 topology rack node + GORM 迁移框架 | **中**。全新模块，需新增 proto/DTO/biz/data/server 四层 + 前端页 | **按需（P2）** |
| ⑥ 分库隔离思想 | 低 | **0–2** | 无（基本已实现） | 已有 MySQL+SQLite+Prometheus 分层 | **高**。仅 oid 字典落位（GORM 表 vs go:embed）的取舍 | **按需（P2/已满足）** |
| ① 单二进制 go:embed | 低 | **0** | 无 | 无 | **高**。edge 已零依赖单二进制 | **不建议（无移植动作，仅规范确认）** |
| ⑩ WebShell 高危命令拦截 | — | **0** | — | 已有 cmdpolicy | **重复** | **不建议** |
| ⑦ 3D 机房数字孪生 | — | **0** | — | 需引入 three.js（package.json 无） | **偏离** | **不建议** |
| ⑨ VM 纳管 | — | **0** | — | 需引入 vsphere/proxmox/kvm SDK | **偏离** | **不建议** |
| ⑭ 老旧 SSH 算法兼容 | — | **0** | — | 无 SSH 客户端栈 | **架构不适用** | **不建议** |

---

## 3. 优先级排序移植建议清单

### P0（建议优先落地）

| 项 | 理由 | 建议实现顺序 | 涉及模块 |
|---|---|---|---|
| ③ SNMP 多厂商 OID 深度监控（⑪ 自定义 OID 下发 + ⑫ 三合一探测 + NOC 大屏） | 唯一「高价值+低重叠」集中区，直接补 ongrid 网络监控深度短板，强化 `specialist-network` 取证与 RCA 证据链，且与既有 PromQL/Prometheus 告警体系天然契合 | ① OID 字典（内置 go:embed 或 GORM 表 + 多厂商 preset）→ ② edge `snmp_probe.go` 扩展自定义 OID GET/WALK 与 v1 支持 → ③ 接口计数器时序采样（Prometheus remote_write）→ ④ 三合一在线探测（SNMP+ping+tcp 统一判定，复用 operatorrun 原语）→ ⑤ NOC 大屏（recharts）→ ⑥ `query_network_devices`/网络 specialist 工具扩展 | `internal/edgeagent/biz/snmp_probe.go`、`internal/pkg/tunnel`、`internal/manager/biz|model|data|server/device`、`internal/manager/biz/aiops/tools/network_inventory.go`、`web/src/pages`（新增 NOC 页）、`agents/specialist-network.md` |

### P1（高价值，条件成熟时做）

| 项 | 理由 | 建议实现顺序 | 涉及模块 |
|---|---|---|---|
| ④ IM 审批卡片（HITL） | 复用已有 4 双向 IM bot + approval 闸门，投入中等、体验增益明确，降低 on-call 成本 | ① Sender 接口扩展 `SendCard` + 回调模型 → ② 飞书/钉钉/Slack/Telegram 四平台交互卡片 + 按钮回调 → ③ approval.Propose 触发卡片推送 + 回调路由 Approve/Reject → ④ 企微双向 bot 按需补 | `internal/manager/biz/imbridge/`（sender.go/provider/*）、`internal/manager/biz/approval/`、`internal/manager/server/imbridge/` |
| ② 本地大模型 preset | 服务隔离内网 edge 落地（10.7.30.x），把已有 custom provider 变开箱即用 | ① router.go 放开「空 APIKey 的本地 provider」→ ② LLM.tsx 加 ollama/vllm preset 卡 → ③ 隔离内网接入文档 | `internal/pkg/llm/router.go`、`web/src/pages/settings/LLM.tsx`、docs |
| ⑬ 自愈二次探活 + Post-Mortem 编排 | 补全「执行完→确认已恢复」的验证闭环，复用全部既有原语 | ① 定义自愈闭环状态机（执行→探活→判定→复盘）→ ② 执行后自动触发 operatorrun 探活 → ③ 基于 OperationArtifact 生成 Post-Mortem 报告 | `internal/manager/biz/operatorrun/`、`internal/manager/biz/approval/`、`internal/manager/model/aiops/operation.go`、`internal/manager/biz/aiops/` |

### P2（按需/低优先）

| 项 | 理由 | 涉及模块 |
|---|---|---|
| ⑧ 告警降噪增量（自定义抑制规则表 + 升级 + 聚类） | 主体降噪已具备，仅补三项增量；当告警风暴成为实际痛点时再做 | `internal/manager/biz/alert/inhibit.go`（扩展为规则表）、`model/alert`、`web/src/pages/AlertRules.tsx` |
| ⑤ IPAM 子网规划 | 有 IP 资产管理诉求时作为轻量模块/MCP 能力补充，非核心主线 | 新增 `internal/manager/{biz,model,data,server}/ipam` + 前端页 |
| ⑥ 分库隔离思想 | 已基本实现，仅 oid 字典落位取舍 | 并入 P0 OID 字典设计决策 |

### 不建议

| 项 | 理由 |
|---|---|
| ① 单二进制 go:embed | edge 已是零依赖单二进制，go:embed 前端无落点；仅作工程规范确认 |
| ⑩ WebShell 高危命令拦截 | ongrid `cmdpolicy` 等价/更强，无移植增量 |
| ⑦ 3D 机房数字孪生 | 定位偏离、工程投入大、ROI 低（需引入 three.js） |
| ⑨ VM 纳管 | 用户为物理机 edge 场景、非 VM 资产；ongrid 定位云原生（已有 K8s 生命周期） |
| ⑭ 老旧 SSH 算法兼容 | **架构不适用**：ongrid 不做 SSH 客户端进设备（反向隧道 PTY + SNMP），无 SSH 算法栈 |

### P0 功能移植后的数据流/模块关系示意（Mermaid）

```mermaid
flowchart LR
    subgraph Edge["ongrid-edge（CGO_ENABLED=0 单二进制）"]
        SNMP[snmp_probe.go<br/>OID 字典 + 自定义 OID<br/>GET/WALK + v1/v2c/v3]
        OPR[operatorrun 探活<br/>ping / tcp(SSH端口) / http]
    end

    subgraph Manager["ongrid manager"]
        TUN[tunnel 反向隧道<br/>probe_network_snmp / operator.exec]
        DEV[device 域<br/>DeviceNetwork / NetworkInterface]
        OIDDB[(OID 字典<br/>GORM 表 / go:embed)]
        TS[(接口计数器时序<br/>Prometheus remote_write)]
        ALERT[alert 域<br/>metric_raw PromQL<br/>DedupeKey / Silence / Inhibit]
        AGENT[specialist-network Agent<br/>query_network_devices 扩展 OID 指标]
    end

    subgraph UI["web 控制台（React+recharts）"]
        NOC[NOC 大屏<br/>吞吐速率 / Top 排行 / 厂商分布]
        OIDUI[自定义 OID 下发页]
    end

    SNMP -->|只读 identity + 接口快照| TUN
    OPR -->|在线探测| TUN
    TUN --> DEV
    DEV --> OIDDB
    DEV --> TS
    TS --> ALERT
    OIDDB --> SNMP
    DEV --> AGENT
    DEV --> NOC
    OIDDB --> OIDUI
    TS --> NOC
```

---

## 4. 关键依赖与兼容性注意事项

### 4.1 新增第三方依赖及许可证

| 依赖 | 用途 | 许可证 | 说明 |
|---|---|---|---|
| `github.com/gosnmp/gosnmp v1.41.0` | SNMP 采集（v2c/v3 已用） | BSD-2-Clause | **已在用**，P0 需扩展 v1（Version1）与自定义 OID，无需新增依赖 |
| 图表（recharts） | NOC 大屏图表 | MIT | **已在用**（web/package.json L27）；**不引入 ECharts**，避免重复依赖 |
| 飞书卡片/回调、钉钉 action card、Slack block kit、Telegram inline keyboard | ④ IM 审批卡片 | — | 均在现有 SDK 内（`larksuite/oapi-sdk-go/v3`、`open-dingtalk/dingtalk-stream-sdk-go`），**无新 SDK**；Slack/Telegram 走现有 HTTP/WS |
| （如需企微双向 bot）企业微信 SDK | ④ 企微渠道补全 | — | 需评估引入企业微信回调 SDK，或复用 `notify` 单向 webhook（无按钮回调） |
| 无 | ② Ollama/vLLM | — | 走 OpenAI 兼容端点，零新依赖 |
| 无 | ⑬ 自愈编排 | — | 复用 operatorrun + approval + LLM，零新依赖 |

### 4.2 proto / API 变更

- **③ P0**：`internal/pkg/tunnel` 需扩展 `ProbeNetworkSNMPRequest/Response`（新增自定义 OID 列表、WALK 结果、v1 参数）；`internal/manager/server/device/http.go` 需新增 `/v1/network/oid-definitions`、`/v1/network/devices/{id}/oid-probe`、`/v1/network/noc/summary` 等路由；SPA `api/` 对应新增 client。
- **④ P1**：`internal/manager/server/imbridge/http.go` 需新增卡片按钮回调路由（如 `/api/v1/im/{provider}/actions`）；审批回调复用 `POST /v1/approvals/{id}/approve|reject`，但需提供「从 IM 上下文鉴权」的入口（当前 requireAdmin 只认 web JWT tenant）。
- **⑬ P1**：无强制 proto 变更，编排层走内部 biz 调用；可选暴露 `/v1/aiops/operations/{id}/postmortem`。
- **⑧/⑤**：⑧ 新增抑制规则表 + API；⑤ 新增 ipam 域 API。均为纯新增路由，不动存量。

### 4.3 DB schema 变更

- **③**：新增 `oid_definitions`（厂商/型号/OID/类型/单位/Tag，可 go:embed 内置 + GORM 表可编辑）、`network_interface_metrics`（接口计数器时序，**建议改走 Prometheus remote_write 而非 MySQL 大表**，避免单库膨胀）；`device_network` 可能新增 `oid_profile` 外键。
- **④**：`approvals` 可能新增 `notify_message_id`/`notify_channel` 列用于回执关联（或存 PayloadJSON）。
- **⑧**：新增 `inhibition_rules`（自定义抑制规则）；`alert_rules` 可能新增升级相关列。
- **⑤**：新增 `ipam_subnets`/`ipam_vlans`/`ipam_address_pools`/`ipam_allocations` 四表。
- 所有变更走既有 `RunMigrations`（`cmd/ongrid/bootstrap_storage.go` L57-81）追加 `Migrate` 函数，保持「迁移列表只增不减、逐包自治」。

### 4.4 前端组件复用

- NOC 大屏 / OID 下发 / IPAM 页面均复用 `web/src/components/ui`（Card/Chip/Input/Button）、`recharts`（图表）、`useI18n()/tr()`（i18n，`web/src/i18n/locale.ts`）、路由挂在 `App.tsx` 既有 AdminLayout 下。**无需引入 ECharts/Three.js**。
- ④ 审批卡片为后端推送，前端仅需在 Approvals 页增加「IM 卡片已推送」状态回显，复用现有 `Approvals.tsx`。

### 4.5 滚动升级兼容

- 所有新增均为**纯新增表/新增路由/新增字段（带默认值）**，不修改存量列语义；`DedupeKey`、`device_network`、`approvals` 等现有语义保持不变。
- edge 二进制需向后兼容：`snmp_probe.go` 扩展须保证旧 manager 不发送新字段时行为不变（新字段缺省走旧 OID 集）。
- 分阶段发布：先发 manager（容忍旧 edge 不响应自定义 OID），再发 edge（新 probe 能力），避免反向依赖。

### 4.6 测试策略

- **③**：单测覆盖 OID 字典匹配（厂商 sysObjectID → 指标集）、自定义 OID GET/WALK 解析（含 index 提取复用 `oidIndex`/`oidIPv4Address`）、v1 兼容；集成测复用 `snmp_probe_test.go` 模式（fake gosnmp / testcontainers）。
- **④**：单测覆盖各平台卡片 payload 构造（golden file）与按钮回调路由；回调鉴权/去重/重放用例。
- **⑬**：状态机单测（成功/失败/超时/取消四态）+ 探活原语复用 `operatorrun` 既有测试。
- **⑧**：自定义抑制规则匹配器单测 + 与现有 BuiltinInhibitor/dampening/cooldown 的组合测试。
- 整体遵循 `.go-arch-lint.yml` 分层约束（biz 不依赖 server、data 依赖 model），新增模块按既有四层（model/biz/data/server）落位。

---

*本文档由架构师高见远基于 ongrid 仓库逐项核验产出，全部结论附文件路径与行号证据；对阶段一产品经理结论中「⑧ 高估」「④ 投入低估」「⑭ 架构不适用」「5 渠道→4+6 渠道」四处做出修正。结论转交主理人向用户交付。*
