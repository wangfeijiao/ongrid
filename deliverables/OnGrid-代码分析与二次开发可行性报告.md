# OnGrid 开源监控平台代码分析报告
## ——现状深度分析 + 统一监控与智能预警平台二次开发可行性

> **分析对象**：OnGrid v0.18.2（工作区 `D:/code/AI/ongrid`，Go + React，AGPLv3）
> **分析日期**：2026-10-26
> **分析方法**：逐文件代码取证（架构、依赖、配置、采集、告警、存储、前端），所有结论附文件路径证据；证据链另见 `docs/architecture_review.md`、`docs/class-diagram.mermaid`、`docs/sequence-diagram.mermaid`。
> **一句话结论**：OnGrid 本质是「**能在客户机真动手的 AI 运维 Agent**」（geminio 双向通道 + 可观测三件套内置），分层与扩展点设计干净，但**时序存储写关系库、无租户隔离、无 HA、无动环/拨测/机电协议**四大短板决定：在其上二次开发统一监控与智能预警平台是**中等偏重的改造**（约 12–15 人月），而非浅层定制。

---

# 第一部分 OnGrid 现状分析

## 1. 核心模块与架构设计

### 1.1 模块划分与职责边界

系统由 **2 个进程**（`cmd/ongrid` 云端 Manager、`cmd/ongrid-edge` 边缘 Agent）+ `cmd/ongrid-edge-supervisor`（Windows 看护）组成，内部按 **3 个业务上下文（BC）+ 2 个共享包** 划分：

| 模块 | 职责 | 关键证据 |
| --- | --- | --- |
| `internal/iam` | 身份访问管理：用户/组织/成员 + Casbin RBAC-with-domains（org 即租户，扁平单层） | `internal/iam/biz/authz`、`model/model.go`（users/orgs/org_memberships 三表）、casbin/v2 + gorm-adapter |
| `internal/manager` | 云端管控（唯一有持久化层）：edge/alert/metric/log/trace/apm/aiops/knowledge/automation 等 | server 约 30 个 handler 包、service 16、biz 27、data 23 store、model 21（`internal/manager/` 下） |
| `internal/edgeagent` | 边缘代理：采集、K8s、operator、webshell、升级、技能执行 | `internal/edgeagent/`：biz/service/model 三层 + runtime（collector、plugins、k8s、operator、webshell、streamrouter…）；**无 data 层，持久化在云端** |
| `internal/pkg` | 共享基础设施：config/logger/dbx/auth/jwt/tunnel/prom/notify/promwrite/llm/embedding/qdrantx/secretbox/passwd 等 | `internal/pkg/` 各子包 |
| `internal/skill` | 共享技能注册表（Executor 接口 + Class safe/mutating/dangerous + Scope host/manager） | `internal/skill/types.go`、`registry.go`、`builtin/` |

**五层分层**（manager 为例）：server（入口校验/解析）→ service（用例编排/事务）→ biz（领域逻辑，唯一可调 data 的层）→ data（GORM 存储实现）→ model（纯类型）。三 BC 均遵循该分层，edgeagent 缺 data 层。

### 1.2 依赖关系与跨域通信（`.go-arch-lint.yml` 强制）

| 规则 | 内容 | 证据 |
| --- | --- | --- |
| BC 隔离 | iam / manager / edgeagent 三大 BC **两两禁止互 import** | `.go-arch-lint.yml:22-36`；实测 manager 无任何 `internal/iam` import |
| 共享包方向 | `internal/pkg` 不得 import 任何 BC；service 层禁止直接 import data（必须经 biz） | `.go-arch-lint.yml:114-117`、`:79,91` |
| 入口约束 | `cmd_manager` 仅可依赖 iam+manager+shared；`cmd_edge` 仅 edgeagent+shared | `.go-arch-lint.yml:122-125` |

**跨 BC 通信不靠 import，靠 wire 消息**：`internal/pkg/tunnel/messages.go`（:10-18 注明 wire=JSON，不生成 protobuf）定义 `register_edge`、`push_prom_samples` 等方法常量与结构体，经 singchia/geminio + frontier 消息总线传输。manager 侧反向调用统一由 `frontierbound.Install` 注册（`internal/manager/service/frontierbound/handlers.go:127`），其中 **Wiring 结构（:71）以窄接口注入 biz 依赖，是装配与业务之间的防腐层**——这是全库最成熟的抽象，可 mock、可测试。

### 1.3 关键代码流程（两条端到端链）

**① 指标采集上报链**（edge → manager → 存储）：

```
edgeagent/collector/embedded.go:54 CollectAll(gopsutil，node_exporter 兼容)
  ｜或 scrape.go:197（主动抓 Prom 端点）
  ↓
edgeagent/biz/agent.go:623 metricsLoop → :651 pushSnapshot → :670 pushOne
  → client.Call(MethodPushHostMetrics / MethodPushPromSamples, tunnel/messages.go:25-26)
  ↓
manager/service/frontierbound/handlers.go:432 push_host_metrics → MetricIngester.Push
  ｜:478 push_prom_samples → PromIngester.Push
  ↓
manager/biz/metric/ingester.go:166 / biz/promwrite/ingester.go:103 Push → :196 write
  ↓
manager/data/metric/store/writer.go:56/87 CreateInBatches(500) 写关系库（host_metrics_raw/_5m/_1h）
```

**② 告警评估通知链**：

```
manager/biz/alert/pipeline.go PipelineEvaluator.Loop(30s tick，PromQL 为主)
  → phaseA.go evaluateMetricAnomaly / evaluateMetricForecast / runVectorRule
  → :381 RecordFiring → :417 notify
  ↓
usecase.go:1349 MaybeNotify（三道门：silence:1363 / cooldown / dampening:1377 / inhibition:1398）
  → :1573 resolveChannels → :1536 BuildSenderFromChannel
  ↓
pkg/notify/notify.go:95 Router.Send → webhook.go 各渠道 Sender.Send（6 渠道）
```

**装配顺序**：`cmd/ongrid/main.go: run()` 纯构造函数显式装配（config.Load→logger→bootstrapTracing→bootstrapStorage(先开 DB 并 AutoMigrate)→bootstrapIAM→…→buildHTTPServers→runWorkers），无 DI 框架；workers 在 HTTP 之后 errgroup 并发启动（`cmd/ongrid/workers.go:21-128`，含 PipelineEvaluator + RetryWorker）。

### 1.4 架构风格评价

- ✅ **优点**：BC 边界清晰可 lint 化；防腐层 Wiring + 窄接口注入利于测试与替换；edge 插件运行时、skill 框架、通知 Sender 均为干净扩展面。
- ⚠️ **缺点**：单体 manager（HTTP、告警评估、降采样、通知、AI agent 共进程 errgroup），**无法水平扩展、无 HA**；时序数据与业务数据共用一个关系库。

---

## 2. 典型使用场景与工作流程

> 场景均由代码/文档事实支撑。注意：OnGrid 目前**未覆盖**机房动环（UPS/PDU/温湿度）与主动拨测/链路探测（唯一 ping 是 AI 技能 `internal/skill/builtin/probe_ping.go`，非监控数据源），故下列场景不含此二类。

| # | 场景 | 适用对象 | 工作流程（关键步骤 → 代码证据） |
| --- | --- | --- | --- |
| 1 | **多机房/多服务器统一纳管与主机巡检** | 运维/平台团队 | ①`install.sh` 一行命令自托管全栈；②Edge 主动外联零入站（README「Zero inbound ports」）+ 批量安装（PRD-002）；③`collector/embedded.go` gopsutil 采集 + `plugins/`（hostmetrics/procmetrics/databasemetrics/custommetrics）；④bash 沙箱 26+ 只读巡检工具全审计 + `get_host_summary/rank_hosts/find_outlier_hosts`（CHANGELOG Unreleased）；⑤`web/src/pages/Edges.tsx`、`Topology.tsx` 可视化。价值：零入站安全纳管 + 主机可观测历史（重装保留） |
| 2 | **告警触发 AI 自动排障（RCA）** | SRE / on-call | ①`biz/alert/rules.go` 9 种规则 kind + `pipeline.go` 30s 评估；②`alert_fired` 触发器拉起 investigator 生成 RCA worker（CHANGELOG v0.9.0）；③Coordinator 派发 SRE/网络/DB Specialist；④RCA Phase1/2 已落地（ROADMAP A.1/A.2 ✓）：因果遍历 + `query_change_events` 读审计日志；⑤拓扑 blast-radius 影响面 + 跨指标/日志/链路定位到源码行。价值：告警→根因自动闭环，缩短 MTTR |
| 3 | **IM 对话式运维 + 浏览器 SSH** | 值班工程师（移动/离线） | ①双向通道 Slack/Telegram/飞书（ADR-021/031）+ 钉钉/企微（`pkg/notify/` 6 渠道）；②IM 提问 Agent 理解基础设施调只读工具；③浏览器 SSH 反向隧道（无 key/无跳板/全审计）；④`serve_page`/`send_notification` 技能回传产物（CHANGELOG v0.9.0）；⑤AI 写操作默认关闭 + 审批/写入闸门（`Approvals.tsx`）。价值：随时随地安全排障，写操作有审批边界 |
| 4 | **K8s 集群纳管** | 云原生/容器平台团队 | ①Edge 注册集群；②`edgeagent/k8s/metrics.go`、`remote_write_scraper.go`（kube-state-metrics remote_write 直写）；③工作负载/事件查看、升级管理；④K8s 资源镜像进拓扑；⑤`pages/Kubernetes.tsx`、`Clusters.tsx`。价值：K8s 全栈可观测 + 资源拓扑，打通容器层与主机层 |
| 5 | **网络设备纳管** | 网络运维/混合基础设施团队 | ①`biz/device/network_discovery.go` 邻居发现 + `collector/network_discovery.go` 被动 ARP/网关/LLDP；②`edgeagent/biz/snmp_probe.go`（gosnmp v2c/v3：接口状态/流量/错误计数）；③`network_poll_scheduler.go` 30s 轮询；④主机—网络设备拓扑关联（README）+ `pages/DeviceShell.tsx`。价值：网络设备纳入统一监控，主机与设备拓扑一体化 |
| 6 | **自定义监控面板与统一可观测** | 需要自助看板的团队 | ①内置 Prometheus+Loki+Tempo+Grafana，Agent 自动写 query；②`biz/monitor/service.go` 面板 CRUD（PromQL，type=timeseries/stat/gauge）；③`biz/grafana/service.go` 单向同步托管 Grafana dashboard 9001-9010；④`pages/` Monitor/Alerts/AlertRules/Logs/Apm/Traces/Dashboard。价值：零搭建全栈可观测 + 自助沉淀面板 |

---

## 3. 技术栈与部署方式

### 3.1 依赖组件（`go.mod` 取证）

| 分类 | 组件 | 说明 |
| --- | --- | --- |
| HTTP | go-chi/chi/v5 | REST 手写路由；gRPC 库仅用于错误码映射，未起 gRPC 服务 |
| 消息总线 | singchia/geminio + singchia/frontier | manager-edge 隧道；**无 Kafka/RabbitMQ** |
| 数据库 | gorm.io/gorm + mysql driver + glebarez/sqlite | **无 ClickHouse/InfluxDB/Redis 驱动**（go.mod 无此三依赖） |
| 日志 | 标准库 log/slog | Loki/ES 走 HTTP 对接 |
| 可观测 | otel + otlptracehttp；prometheus/client_golang | — |
| 配置 | gopkg.in/yaml.v3 | **无 viper/koanf**，YAML 仅用于 edge scrape/frontier/loki/tempo |
| AI | cloudwego/eino + go-openai + fastembed-go（ONNX embedding） | Agent 框架 |
| 认证 | casbin/v2 + gorm-adapter、golang-jwt/v5 | RBAC |
| 其他 | gopsutil、gosnmp、gorilla/websocket、robfig/cron、larksuite-sdk、dingtalk-stream-sdk | 采集与 IM |
| 前端 | React 18.3 + TS 5.6 + Vite 5 + Tailwind 3 + @base-ui/react + recharts 2 + @xyflow/react（拓扑）+ xterm + zustand 5 + react-router 6 | vitest + playwright 测试 |

### 3.2 部署形态

| 角色 | 形态 | 证据 |
| --- | --- | --- |
| Manager | **仅 Docker Compose**（离线包 tar.xz 不含镜像） | `deploy/install/README.md` 明确；`install.sh/uninstall.sh/upgrade.sh/apply-pending-upgrade.sh` |
| Edge | 三形态：systemd / 容器（distroless 静态）/ K8s Helm | `deploy/install/edge/ongrid-edge.service`、`deploy/Dockerfile.ongrid-edge`、`deploy/kubernetes/ongrid-edge/Chart.yaml` |
| 批量部署 | `deploy-edge-batch/`（probe.py 探测 / deploy.py / remote-install.sh / provision.py） | 批量部署报告：83 IP → 37 online |
| 本地开发 | docker-compose（mysql+ongrid+frontier+nginx+prometheus+grafana） | `deploy/docker-compose.yml` |

### 3.3 配置项机制

唯一入口 `internal/pkg/config/config.go`：**全部 `os.Getenv` + 默认值**（代码注释明言 "no YAML/viper dep yet"），前缀 `ONGRID_`。配置组约 20+：HTTPAddr/MetricsAddr/TunnelAddr、PublicURL、K8sEvent、DB、JWT、OpenAI、LLM(多 provider)、Admin、Edge、FrontierClient、Prom、Grafana、Notification、Alert、Logs、Traces、Profiles、PacketCapture、Skills。

- ✅ 利：12-factor 风格，Edge 三形态（systemd/容器/K8s）共用一套配置逻辑。
- ⚠️ 弊：无环境分层（无 dev/prod 文件）、**无类型校验**（拼写错误静默落到默认值）、无集中配置视图、敏感值明文 env。

### 3.4 扩展机制（5 类扩展点）

| 扩展点 | 接口 | 扩展成本 | 证据 |
| --- | --- | --- | --- |
| 通知渠道 | `notify.Sender`（Name/Send）+ Router | **低** | `pkg/notify/notify.go:34,95`；`webhook.go` 已内置 Generic/Slack/Feishu/DingTalk/WeCom/Telegram 6 个构造器（注意：`NewFromConfig` 仅装配 4 个，WeCom/Telegram 有实现未接入） |
| Edge 插件 | `plugins.Plugin`（Name/Configure/Start/Stop/HealthSnapshot）| 中 | `edgeagent/plugins/plugin.go:26` |
| 技能 | `skill.Executor` + Class + Scope（一文件 + init 注册） | 低 | `internal/skill/types.go`、`builtin/` |
| 采集器 | Collector 三模式（embedded / scrape / composite） | 中 | `edgeagent/collector/embedded.go、scrape.go、composite.go` |
| 自定义页面 | `extensions.pageStore` | 低 | `cmd/ongrid/http.go` pagesHandler |

---

## 4. 功能短板与局限

### 4.1 监控能力短板

| 短板 | 代码证据 | 影响 | 弥补难度 |
| --- | --- | --- | --- |
| **机房动环无采集**（UPS/PDU/温湿度） | Grep `(?i)ups|pdu|temperature|humidity` 无采集器代码；`device/environment.go` 的 environment 只是设备所属集群环境标签 | 机柜级监控完全空白 | 中 |
| **主动拨测/链路探测无**（icmp/traceroute） | 唯一 ping 是 AI 技能 `internal/skill/builtin/probe_ping.go`，非监控数据源 | 机房网络链路状态无法监控 | 低 |
| 中间件监控需手工配置 | `plugins/databasemetrics/custommetrics` 有采集能力但依赖手工配置 target | 中间件纳入成本高 | 中 |
| 机电设备协议单一 | 仅 gosnmp v2c/v3（`tunnel/messages.go:527-542`），**无 Modbus/OPC-UA** | 高速公路机电设备（情报板/车检器/气象站等）多走 Modbus/串口，无法直接接入 | 高 |
| 业务系统监控靠 APM 侧 | `biz/apm/` + `pkg/autoapm/spec.go` 按注解/进程自动发现 | 无独立探活/拨测式业务监控 | 中 |

### 4.2 告警能力短板

| 短板 | 代码证据 | 影响 | 弥补难度 |
| --- | --- | --- | --- |
| 无独立 alertmanager 进程 | 评估器内嵌 `biz/alert/pipeline.go` | 无高可用告警路由/分组 | 中 |
| 抑制规则仅内置 2 条 | `biz/alert/inhibit.go`（edge_offline→host、prom_ingest_fail→scrape_down），注释明示"自定义抑制规则表待未来" | 无法配置告警风暴治理 | 低-中 |
| 无 record rule | 仅 alert rule（`rules.go` 9 种 kind） | 预聚合能力缺失 | 中 |
| 通知渠道缺口 | 6 渠道均为 webhook 系且 `NewFromConfig`（notify.go:75-89）仅装配 4 个；**无 email、无短信** | 渠道覆盖不全；WeCom/Telegram 实现存在但未接线 | 低 |
| 告警表达强依赖 PromQL | `rules.go:29-46` 规则 kind 定义 | 非 Prom 指标源（Modbus 设备）需先转指标 | 中 |

### 4.3 可扩展性短板

| 短板 | 代码证据 | 影响 | 弥补难度 |
| --- | --- | --- | --- |
| **时序数据无多租户隔离** | `biz/metric/repo.go:35-36` 注释："scoping is by edge_id alone; there is no org_id to enforce" | 多租户 SaaS 场景存在越权风险 | 高 |
| 无 API 限流 / 响应缓存 / 数据分片 | `pkg/httpserver` 无 rate limit；唯一限流是 AI 工具级 `biz/aiops/tools/decorators/ratelimit.go` | 大流量 API 无保护；单库无 shard key | 中-高 |
| ClickHouse 仅占位 | `data/metric/clickhouse/doc.go` "Phase 2 placeholder" | 时序扩容路线未落地 | 中（接口已预留，见 7.1） |
| 单体无 HA | main.go 单进程装配，无多副本设计 | Manager 单点故障 | 高 |

### 4.4 性能短板

| 短板 | 代码证据 | 影响 | 弥补难度 |
| --- | --- | --- | --- |
| **时序指标写关系库** | `data/metric/store/writer.go:56/87` GORM CreateInBatches(500) 写 host_metrics_raw/_5m/_1h | 上千主机高频上报时 MySQL 成为瓶颈 | 高 |
| 无 Redis 缓存 | go.mod 无 Redis 驱动 | 热点查询/规则/会话无加速层 | 中 |
| 降采样/保留串行批处理 | `biz/metric/downsample.go` + `retention.go`（raw 7d/5m 90d/1h 365d，每日 03:00 批删 limit=1000） | 大数据量时批处理窗口受限 | 中 |
| 无响应级缓存 | 仅规则快照 30s + promwrite endpoint 5s TTL | 查询延迟随数据量线性增长 | 中 |

---

## 5. 与官方 Roadmap 的差距

> **关键发现：Roadmap 严重滞后于代码实现。** ROADMAP 最后更新 2026-06-06，早于 CHANGELOG v0.9.0（06-27）与当前 VERSION v0.18.2；CHANGELOG 缺 v0.10~v0.18 记录（git tag 已到 v0.18.2）。判断真实能力须以**代码 + CHANGELOG + README** 为准。

### 5.1 Roadmap 13 章对照表（含状态纠正）

| 章 | 已实现（✓） | 进行中/暂缓 | 未实现/规划中 | **状态纠正** |
| --- | --- | --- | --- | --- |
| A 根因 RCA | A.1/A.2：investigator 因果遍历 + `query_change_events` | — | A.3 变更监视 / A.4 有向依赖 / A.5 相似案例 / A.6 置信度 / A.7 因果图 | — |
| B 远程诊断动作 | — | ◐ B.4 网络 Layer-1 cmdpolicy（9 binary 只读，`edgeagent/cmdpolicy/`） | B.1 抓包 / B.2 探测 / B.3 文件内核诊断 / B.5 网络技能 | — |
| C 自然语言查询 | — | — | C.1 chat_to_query / C.2 chat→Grafana / C.3 自动补全 全 □ | — |
| D Agent 内核 | 简化审批/写入闸门（`Approvals.tsx` + fail-safe 写开关 + MCP trusted） | — | D.1 specialist / D.2 critic / D.3 eval / D.4 完整提案中介 / D.5 成本 | **D.4/E.6 已落简化版未更新**；README 已宣称 Coordinator+Specialist，D.1 仍 □ |
| E 用户面助理 | 审批 UI（`pages/Approvals.tsx`） | — | E.1 侧边栏 / E.2 多助理 / E.3 快捷卡 / E.4 知识收藏 / E.5 推理时间线 | **E.6 已实现未更新** |
| F 数据资源接入 | F.4.1 Slack/Telegram/飞书 ✓；F.5.1 Loki ✓（ADR-012）；**K8s**（`k8s/metrics.go`）；**网络设备**（`network_discovery.go`/`snmp_probe.go`）；**钉钉/企微**（`notify.go`）；traces | — | F.1.x K8s / F.2 云资源 / F.3 Ollama·vLLM·Bedrock / F.4.3 Teams / F.5.2 Tempo 方案 / F.5.3 eBPF | **F.1.x、F.4.2、F.4.4 实际已实现，Roadmap 仍 □** |
| G 工程化运维 | G.1.7 卸载脚本 / G.2.1 一键升级+回滚 / G.4.1 /metrics+自告警 / G.13.1 SSH+git 凭证 | ◯ G.3 Prometheus 生产硬化(5 项)、G.6.1/G.6.2 HA | 其余 G.x 全 □ | — |
| H 沙箱隔离 | 现状：bash 沙箱 + cmdpolicy | ◯ H.1 microsandbox、H.2 Python 通道 | H.3–H.8 □ | — |
| I 安全合规 | 现状：Casbin RBAC-with-domains + secrets 加密 + 写开关 | — | I.1 SSO…I.14 全 □ | — |
| J 生态/后置 | — | ◯ J.1 技能市场、J.4 开源生态 | J.2/J.3 □ | — |
| K SOP/Runbook | 工作流编排已落地（CHANGELOG + `Flows.tsx`/`FlowEditor.tsx`） | — | K.1 三层类 / K.2 DSL / K.3 双签名…K.7 全 □ | **编排已实现，可执行 SOP 闭环仍全 □** |
| L 周期性任务 | 统一任务抽象（HLD-022）：定时报告+一次性任务 | — | L.1 巡检 / L.2 周报 / L.3 watch / L.4 容量 / L.5 成本 全 □ | **定时任务抽象已实现，智能作业仍全 □** |

### 5.2 差距解读

1. **Roadmap 管理状态落后于代码节奏**：README 已正式宣称的 K8s 生命周期管理、网络设备管理、MCP 客户端、工作流编排、审批闸门、钉钉/企微通道，在 Roadmap 中对应项（F.1.x、F.4.2、F.4.4、D.4、E.6）仍标 □。
2. **对「统一监控平台」定位最关键的缺口集中在生产化能力**：Prometheus 生产硬化（G.3 全 5 项 ◯）、HA/故障切换（G.6 ◯）、沙箱隔离（H.1/H.2 ◯）均被暂缓；叠加代码层短板（无租户隔离/限流/分片/alertmanager/record rule/email 渠道），当前产品更接近「AI 运维 Agent + 内置可观测」，距多租户高可用的企业级统一监控平台仍有明确距离。
3. **对二次开发信号清晰**：护城河在「能动手」（geminio 通道 + AI agent），入场券是「可观测」；工程化（G 大半）、安全合规（I 全章）、SOP/Runbook（K 全章）、告警治理（去重/抑制/路由）基本空白——这些正是二次开发的切入点。

---

# 第二部分 二次开发可行性分析

**目标**：基于 OnGrid 二次开发一套**统一监控与智能预警平台**，监控对象：①机房机柜（动环）②服务器 ③网络设备等基础设施 ④业务系统运行状态 ⑤中间件运行状态 ⑥机房网络链路状态 ⑦高速公路机电设备运行状态。

## 6. 可行性结论与主要改造点

**结论：可行。** 分层架构 + 防腐层 + 5 类扩展点（Plugin / Sender / Skill / Collector / pageStore）提供了干净的扩展面，但「时序写关系库 + 无租户隔离 + 无 HA + 缺动环/拨测/Modbus 协议」四短板决定这是**中等偏重改造**。

| # | 改造点 | 现状（代码证据） | 改造方式 | 工作量 |
| --- | --- | --- | --- | --- |
| 1 | 时序存储升级 ClickHouse | 关系库 Writer（`metric/store/writer.go:56/87`）；ClickHouse 仅占位（`data/metric/clickhouse/doc.go`） | 实现 ClickHouse Writer/Reader 替换接口（接口已预留，见 7.1） | **L** |
| 2 | 多租户隔离 | `metric/repo.go:35-36` 无 org_id | 时序表加 org_id，写入/查询注入 | **L** |
| 3 | HA / 限流 / 缓存 | 单体无 HA、`httpserver` 无限流、无 Redis | 限流中间件 + Redis 缓存 + Manager 多副本 | **L** |
| 4 | 机房动环采集 | 无采集代码 | 新增 `edgeagent/plugins/dcim` 插件（Modbus/SNMP 接 UPS/PDU/温湿度） | M |
| 5 | 链路拨测 | `probe_ping.go` 非数据源 | 新增 collector 拨测器（ping/丢包/时延/traceroute 转指标） | M |
| 6 | 中间件监控完善 | `plugins/databasemetrics/custommetrics` 需手工配置 | 补 exporter 预设 + 自动发现 | M |
| 7 | 机电设备协议 | 仅 gosnmp（`tunnel/messages.go:527-542`） | 引入 Modbus 库，新增 `plugins/electro` 插件；OPC-UA 视设备定 | M~L |
| 8 | 告警 rule kind 扩展 | `model.go:95-113` 9 种 kind | 新增 dcim_threshold / link_quality 等 kind + evaluator case | M |
| 9 | 通知渠道补全 | 6 webhook 渠道，无 email/短信 | 新 Sender 实现 + `NewFromConfig`/`BuildSenderFromChannel` 加 case | S |
| 10 | 自定义抑制规则表 | `inhibit.go` 仅 2 条内置 | 抑制规则落库可配置 | S~M |
| 11 | 业务系统探活 | `biz/apm/` + `autoapm` 已有 | 复用 APM + 新增探活插件 | S~M |

## 7. 技术实现路径

### 7.1 架构调整（核心三项）

- **时序存储切 ClickHouse**：`biz/metric/repo.go:16-52` 已定义存储无关的 Writer/Reader 接口（原架构预留接缝），仅需新增 `data/metric/clickhouse/store.go` 实现同接口，装配按 `ONGRID_DB_DIALECT` 切换，**biz 层零改动**。
- **租户隔离**：从 `authzmw`/`tenantctx` 注入 org_id，时序写入与查询路径补 org_id 过滤（改造点 #2 是上线前置条件）。
- **HA 与保护**：Manager 多副本 + LB；告警评估/降采样任务多副本串行化（锁/选主）；chi 中间件加限流；Redis 承接缓存与分布式锁。

### 7.2 模块复用与新增（按监控对象）

| 监控对象 | 复用 | 改造/新增 |
| --- | --- | --- |
| 服务器 | `collector/embedded.go`（gopsutil）、procmetrics 插件 | 直接复用 |
| 网络设备 | `biz/device/network_discovery.go`、`snmp_probe.go`、`network_poll_scheduler.go` | 直接复用（已支持 SNMP v2c/v3 轮询） |
| 机房机柜（动环） | Plugin 运行时、告警管线、拓扑模块 | **新增 `edgeagent/plugins/dcim`**：Modbus RTU/TCP 读 UPS/PDU/精密空调/温湿度 → push_prom_samples 上报 → 新增 dcim_threshold 规则 kind |
| 机房网络链路 | collector 框架、告警管线 | **新增 `edgeagent/collector/probe.go` 拨测器**：周期 ping/丢包率/RTT/jitter/traceroute → 指标化 → link_quality 规则 kind |
| 业务系统 | `biz/apm/`、`pkg/autoapm` | 改造复用：URL 探活/进程存活/端口探测插件，按业务系统分组告警 |
| 中间件 | `plugins/databasemetrics/custommetrics` | 补 Redis/Kafka/Nginx/Tomcat 等 exporter 预设 + 进程自动发现 |
| 高速公路机电设备 | `snmp_probe.go`（部分机电支持 SNMP）、edge 零入站架构、edge 批量部署（`deploy-edge-batch/`） | **新增 `edgeagent/plugins/electro`**：引入 goburrow/modbus 支持 Modbus RTU/TCP（情报板、车检器、气象站、照明等），经协议网关转 SNMP 亦可；复用边缘节点的物理就近采集优势 |

### 7.3 数据采集与告警扩展方式（沿现有扩展点的具体步骤）

- **采集扩展**（不改 manager 核心）：实现 `plugins.Plugin` 五方法（Name/Configure/Start/Stop/HealthSnapshot）→ supervisor 注册 → 经 `get_plugin_configs`/`plugin_configs_changed`（`tunnel/messages.go:45-49`）云端下发配置 → 指标经 `push_prom_samples`（`biz/agent.go:699`）上报，自动进入现有入库/降采样/保留链路。
- **告警扩展**：`alert/model` 加 kind 常量 → evaluator 加 case → 复用 `RecordFiring`/`MaybeNotify`，**自动继承静默/冷却/防抖/抑制**能力，无需重写告警管线。
- **通知扩展**：实现 `notify.Sender` 接口 → `NewFromConfig` 加 case → `BuildSenderFromChannel` 加映射（注意顺带把已有实现未接线的 WeCom/Telegram 补上）。

## 8. 关键风险

| 风险 | 来源代码证据 | 应对 |
| --- | --- | --- |
| 时序关系库成为容量瓶颈 | `writer.go:56/87` GORM 批量写关系库 + ClickHouse 仅占位 | 分阶段：先缩保留期扛过渡，再切 ClickHouse（接口已预留） |
| 多租户数据泄漏 | `metric/repo.go:35-36` 无 org_id | **上线前必须完成 org_id 隔离**，作为硬性前置条件 |
| 机电设备协议盲区 | go.mod 仅 gosnmp | 引入 goburrow/modbus；OPC-UA 等协议先走网关转换；按设备型号实测协议栈 |
| Manager 单点故障 | `main.go` 单体装配无 HA 设计 | 多副本 + LB + 任务选主；告警评估与降采样串行化 |
| AGPLv3 合规 | LICENSE AGPLv3 | 二次开发若对外提供 SaaS 服务需评估开源义务与法务风险 |
| 主分支演进快、文档滞后 | CHANGELOG 缺 v0.10~v0.18、Roadmap 状态滞后 | 二开基于固定 fork 基线，建立定期 rebase 机制与变更跟踪 |

## 9. 工作量评估

| 级别 | 改造项 | 估算 |
| --- | --- | --- |
| S（3 项） | 通知渠道补全、抑制规则表、业务探活 | ≈ 1.5 人月 |
| M（4 项） | 动环插件、链路拨测、中间件预设、rule kind 扩展 | ≈ 4 人月 |
| L（3 项） | ClickHouse 迁移、租户隔离、HA/限流/缓存 | ≈ 7 人月 |
| M~L（1 项） | 机电 Modbus 插件（含协议实测） | ≈ 2 人月 |
| **总计** | | **≈ 12–15 人月**（不含测试与运维投入） |

**建议阶段划分**：
1. **一阶段·打地基（2–3 月）**：租户隔离、限流/缓存、告警规则扩展、通知渠道——先解决正确性与安全底座；
2. **二阶段·补采集（2–3 月）**：动环插件、链路拨测、中间件预设、机电 Modbus——按监控对象逐个接入；
3. **三阶段·上规模（2–3 月）**：ClickHouse 迁移 + Manager HA + 性能压测调优。

---

## 附录：核心证据文件索引

| 主题 | 文件 |
| --- | --- |
| 装配与入口 | `cmd/ongrid/main.go`、`cmd/ongrid/bootstrap_storage.go:45`、`cmd/ongrid/workers.go:21-128`、`cmd/ongrid-edge/main.go` |
| 依赖规则 | `.go-arch-lint.yml`（:22-36 BC 隔离、:114-117 pkg 方向、:79,91 service→data 禁令） |
| 隧道协议 | `internal/pkg/tunnel/messages.go`（:25-26 方法常量、:45-49 插件配置下发、:527-542 SNMP 设备消息） |
| 采集 | `internal/edgeagent/collector/embedded.go:54`、`scrape.go:197`、`composite.go`、`k8s/metrics.go`、`k8s/remote_write_scraper.go` |
| 插件运行时 | `internal/edgeagent/plugins/plugin.go:26`、`plugins/`（hostmetrics/procmetrics/databasemetrics/custommetrics/logs/traces/profiles/autoapm） |
| 上报与接收 | `internal/edgeagent/biz/agent.go:623-699`、`internal/manager/service/frontierbound/handlers.go:127,432,478`、`Wiring :71` |
| 时序存储 | `internal/manager/biz/metric/repo.go:16-52`（接口）、`:35-36`（无 org_id 注释）、`data/metric/store/writer.go:56,87`、`biz/metric/downsample.go`、`retention.go`、`data/metric/clickhouse/doc.go`（占位） |
| 告警引擎 | `internal/manager/biz/alert/rules.go`（9 种 kind）、`pipeline.go`、`evaluators_phaseA.go`、`usecase.go:1349-1573`、`inhibit.go`（2 条内置）、`model/model.go:95-113` |
| 通知 | `internal/pkg/notify/notify.go:34,75-95`、`webhook.go`（6 渠道构造器） |
| 网络设备 | `internal/manager/biz/device/network_discovery.go`、`network_poll_scheduler.go`、`internal/edgeagent/biz/snmp_probe.go`、`collector/network_discovery.go` |
| 面板与 Grafana | `internal/manager/biz/monitor/service.go`、`biz/grafana/service.go`、`biz/grafana/dashboards/*.json` |
| 配置 | `internal/pkg/config/config.go`（env-only，前缀 ONGRID_） |
| 多租户 | `internal/iam/`（Casbin RBAC-with-domains）、`internal/pkg/tenantctx` |
| 部署 | `deploy/install/README.md`、`deploy/install/edge/ongrid-edge.service`、`deploy/kubernetes/ongrid-edge/Chart.yaml`、`deploy-edge-batch/` |
| Roadmap/文档 | `ROADMAP.md`、`VERSION`（v0.18.2）、`CHANGELOG.md`（至 v0.9.0）、`docs/requirements/`（13 PRD）、`docs/rfc/`（9 RFC）、`docs/design/`（HLD-001/002 等） |
| 架构评审附件 | `docs/architecture_review.md`、`docs/class-diagram.mermaid`、`docs/sequence-diagram.mermaid` |
