# OnGrid 架构评审与「统一监控与智能预警平台」二次开发可行性分析

> 评审对象：`D:/code/AI/ongrid`（Go 1.25 + React 18，AGPLv3）。本文所有结论均附代码证据（文件路径/接口名）。

## 第一章：核心模块与架构设计

### 1.1 模块划分表

| 模块 | 职责 | 分层与代表文件 |
|------|------|----------------|
| `internal/iam` | 身份访问管理（RBAC-with-domains，org 即租户） | `biz/authz`（Casbin 授权）、`biz/org`、`biz/membership`、`data`、`model`、`server`、`service`；依赖 `casbin/v2`+`gorm-adapter`、`golang-jwt/v5` |
| `internal/manager` | 云端管控中心（唯一有 `data` 持久化层的 BC） | 五层：`server`（约 30 个 handler 包：edge/alert/aiops/apm/logs/metric/prometheus/webshell…）、`service`（16 个）、`biz`（27 个：edge/alert/metric/promwrite/logs/apm/aiops…）、`data`（23 个 store）、`model`（21 个） |
| `internal/edgeagent` | 边缘代理（无 `data` 层，持久化在云端） | `biz`/`service`/`model` 三层 + `runtime` 子包（`collector`、`plugins`、`k8s`、`operator`、`webshell`、`streamrouter`…） + 配置/密钥/升级（`config`、`dpapi`、`enrollment`、`upgradebundle`、`upgrademachine`） |
| `internal/pkg` | 共享基础设施（业务无关） | `config/config.go`、`dbx`、`auth/jwt.go`、`authzmw`、`tunnel`、`prom`、`notify`、`promwrite`/`promquery`/`logquery`/`tracequery`、`llm`、`embedding`、`qdrantx`、`secretbox`、`passwd/argon2.go` 等 |
| `internal/skill` | 共享技能注册表（L2 设备直连能力框架） | `types.go`（Executor/Class/Scope）、`registry.go`、`loader.go`、`subprocess.go`、`builtin/` |

**层间职责（以 manager 为例）**：`server` = HTTP/tunnel 入口与参数校验 → `service` = 用例编排/事务边界 → `biz` = 领域逻辑（唯一能调用 `data` 的业务层）→ `data` = GORM 存储实现 → `model` = 纯领域类型（GORM tag）。

### 1.2 职责边界

依赖规则由 `.go-arch-lint.yml` 强制（`make arch-lint`）：

1. **三大 BC 两两禁止互 import**：`.go-arch-lint.yml:22-36` 声明 `iam_*`/`manager_*`/`edgeagent_*` 组件，但 `deps` 白名单中三 BC 之间无任何交叉项；实测 `internal/manager` 无 `internal/iam` import。
2. **`internal/pkg` 不得 import 任何 BC**：`.go-arch-lint.yml:114-117` `shared_pkg.mayDependOn: [shared_pkg]`。
3. **同 BC 内 service 禁止直接 import data（必须经 biz）**：`.go-arch-lint.yml:79,91`，如 `manager_service.mayDependOn` 不含 `manager_data`。
4. **两个入口各自装配**：`cmd_manager` 仅可依赖 `iam+manager+shared`（`:122-123`）；`cmd_edge` 仅可依赖 `edgeagent+shared`（`:124-125`）。

**跨 BC 通信机制**：不靠 import，靠 `internal/pkg/tunnel` 共享 wire 类型 + `singchia/geminio` 消息总线。`internal/pkg/tunnel/messages.go:22-100` 以字符串常量定义方法名（`register_edge`/`push_host_metrics`/`push_prom_samples`/`execute_skill`…），`:10-18` 注释明确 wire 格式为 JSON（MVP 不生成 protobuf 类型，保持 pkg 无 protobuf 依赖）。manager 侧反向调用统一由 `frontierbound.Install` 注册（`internal/manager/service/frontierbound/handlers.go:127`）。

### 1.3 关键代码流程（两条端到端）

**流程一：指标采集上报链（edge → manager 存储）**

```
edgeagent/collector/embedded.go:54 EmbeddedCollector.CollectAll (gopsutil)
 或 collector/scrape.go:197 ScrapeCollector.CollectAll (抓 Prom 端点)
→ edgeagent/biz/agent.go:623 metricsLoop (MetricsInterval tick)
→ :651 pushSnapshot → :670 pushOne
→ client.Call(MethodPushHostMetrics / MethodPushPromSamples, tunnel/messages.go:25-26)
→ manager/service/frontierbound/handlers.go:432 push_host_metrics → MetricIngester.Push
   (:478 push_prom_samples → PromIngester.Push)
→ manager/biz/metric/ingester.go:166 (主机指标写 host_metrics_raw)
   / manager/biz/promwrite/ingester.go:103 Push → :196 write
→ manager/data/metric/store/writer.go:56/87 CreateInBatches(rows, 500)
```

**流程二：告警评估通知链（manager 内部）**

```
manager/biz/alert/pipeline.go PipelineEvaluator.Loop (30s tick)
→ evaluateMetricAnomaly / evaluateMetricForecast / runVectorRule (phaseA.go)
→ :381 RecordFiring → :417 notify
→ usecase.go:1349 MaybeNotify (静默/cooldown/:1363、dampening/:1377、抑制/:1398 三道门)
→ :1573 resolveChannels → :1536 BuildSenderFromChannel
→ internal/pkg/notify/notify.go:95 Router.Send → webhook.go Sender.Send (Slack/Feishu/DingTalk/WeCom/Telegram/GenericWebhook)
```

### 1.4 架构风格评价

- **防腐层 Wiring（优点）**：`frontierbound/handlers.go:71-101` 的 `Wiring` 结构体以窄接口（`EdgeAuthenticator`、`MetricIngester`、`PromIngester`…）注入 biz 依赖，装配与业务解耦、可替换、可 mock（`e2e harness` 用 `NewDisabled` 走 `c.svc==nil` 分支 `:137`）。这是本项目中架构最成熟的抽象。
- **无 DI 框架（优点）**：`cmd/ongrid/main.go:29-94` 纯构造函数 + 显式参数传递，装配顺序清晰可读、无魔法、启动顺序即依赖顺序。
- **单体 manager（优点/缺点）**：优点——部署简单（单容器）、无分布式一致性负担、迭代快；缺点——`main.go:94` `runWorkers` 与 HTTP 同进程 errgroup（`cmd/ongrid/workers.go`），告警评估、降采样、通知、AI 推理共用一个进程内存与 CPU，**无法水平扩展**，单点故障、无 HA（详见第四章）。

---

## 第三章：技术栈与部署方式

### 3.1 依赖组件表

| 分类 | 组件 | 用途 | 证据（go.mod） |
|------|------|------|----------------|
| HTTP 路由 | go-chi/chi/v5 | REST 路由（手写，无 grpc-gateway） | `go-chi/chi/v5`；gRPC 库 `google.golang.org/grpc` 仅用于错误码映射 |
| 隧道/总线 | singchia/geminio + frontier | manager-edge 消息总线（无 Kafka/RabbitMQ） | `singchia/geminio` |
| 数据库 | gorm.io/gorm + mysql driver + glebarez/sqlite | 业务库 MySQL/SQLite AutoMigrate | `gorm.io/driver/mysql`、`glebarez/sqlite` |
| 时序存储 | （无专用驱动） | **无 ClickHouse/InfluxDB/Redis 驱动** | go.mod 全文无 clickhouse/influx/redis 依赖 |
| 日志 | 标准库 log/slog | 结构化日志 | `log/slog` |
| 可观测 | otel + otlptracehttp | Trace 导出 | `go.opentelemetry.io/otel` |
| 指标 | prometheus/client_golang | 自身指标 + remote_write | `prometheus/client_golang` |
| 配置 | gopkg.in/yaml.v3 | 仅用于 edge scrape 目标等 | **无 viper/koanf** |
| AI | cloudwego/eino + go-openai + fastembed-go | LLM 编排 + ONNX embedding | `cloudwego/eino`、`sashabaranov/go-openai`、`anush008/fastembed-go` |
| 认证 | casbin/v2 + gorm-adapter、golang-jwt/v5 | RBAC-with-domains + JWT | 对应 require 项 |
| 采集/协议 | gopsutil、gosnmp、gorilla/websocket、robfig/cron | 主机/SNMP/WebSSH/定时 | 对应 require 项 |
| IM 通知 | larksuite-oapi-sdk-go、open-dingtalk/dingtalk-stream-sdk-go | 飞书/钉钉 SDK | 对应 require 项 |

### 3.2 部署形态

| 形态 | 方式 | 证据 |
|------|------|------|
| Manager | 仅 Docker Compose（无 K8s/裸机） | `deploy/install/README.md` |
| Edge | ① systemd（`deploy/install/edge/ongrid-edge.service` + `install-edge.sh`）② 容器（`deploy/Dockerfile.ongrid-edge` distroless）③ K8s Helm（`deploy/kubernetes/ongrid-edge/Chart.yaml`） | 目录存在 |
| 本地开发 | `deploy/docker-compose.yml`（mysql+ongrid+frontier+nginx+prometheus+grafana） | 目录存在 |
| 升级 | `install.sh`/`uninstall.sh`/`upgrade.sh`/`apply-pending-upgrade.sh`；离线发布包 tar.xz（不含镜像） | `deploy/install/` |

### 3.3 配置机制（env-only）

唯一入口 `internal/pkg/config/config.go`：全部 `os.Getenv` + 默认值，注释明言 "no YAML/viper dep yet"（`:3`），前缀 `ONGRID_`。

- **优点**：无配置文件解析依赖、12-factor 友好、容器/K8s/systemd 三形态统一用环境变量注入。
- **缺点**：① 无分层/环境切换（dev/staging/prod 只能靠部署侧拼 env）；② 无校验与类型安全（手写 `getEnvInt`/`getEnvBool`，拼写错误静默落到默认值）；③ 配置项多（`Config` 含 20+ 组 `:21-65`），无集中文档视图；④ 敏感值（DB 密码、JWT 密钥、edge secret）以明文 env 存在，无 secretbox 统一接入点。

### 3.4 扩展机制清单（5 类）

| 扩展点 | 接口 | 扩展成本 |
|--------|------|----------|
| ① 通知渠道 | `notify.Sender` 接口（`notify.go:34`：`Name()/Send()`）+ `Router`（`:95`）；`webhook.go` 已内置 6 个 Sender 构造器（Generic/Slack/Feishu/DingTalk/WeCom/Telegram，`webhook.go:29/41/142/158/171/185`） | **低**：实现 2 方法 + `NewFromConfig` 加 case + config 加字段 |
| ② Edge 插件 | `plugins.Plugin` 接口（`plugin.go:26`：`Name/Configure/Start/Stop/HealthSnapshot`）+ `Supervisor` 生命周期管理 | **中**：实现 5 方法 + 注册进 supervisor + 配置快照 |
| ③ Skill 技能 | `skill.Executor` 接口 + `Class`（safe/mutating/dangerous）+ `Scope`（host/manager），一文件 + `init()` 注册（`skill/types.go:8-11`） | **低** |
| ④ 采集器 | `collector` 三模式：`embedded.go`（gopsutil 内置）、`scrape.go`（主动抓 Prom 端点）、`composite.go`（组合） | **中**：实现 `Collector` 接口接入 `CollectAll` |
| ⑤ 自定义页面 | `extensions.pageStore`（`cmd/ongrid/http.go` pagesHandler） | **低**：注册页面 key + 渲染函数 |

---

## 第四章：功能短板与局限

### 4.1 监控能力短板

| 短板点 | 代码证据 | 影响 | 弥补难度 |
|--------|----------|------|----------|
| 机房动环缺失（UPS/PDU/温湿度） | 全库无 UPS/PDU/温湿度采集代码；采集仅主机 gopsutil（`embedded.go`）+ 网络 SNMP（`edgeagent/biz/snmp_probe.go`） | 无法监控机房基础设施 | 中（需新增插件） |
| 主动拨测/链路探测缺失 | 无 icmp/traceroute 采集；唯一 ping 是 `internal/skill/builtin/probe_ping.go`，属 AI 技能非监控数据源 | 无法做链路质量/连通性监控 | 低（新增采集器/插件） |
| 中间件监控不完整 | `plugins/databasemetrics`、`plugins/custommetrics` 存在但需手工配置目标与凭据 | MySQL/Redis/MQ 需人工接线 | 中 |
| 机电设备协议接入受限 | `gosnmp` 仅 v2c/v3（`tunnel/messages.go:527-542`），**无 Modbus/OPC-UA** | 高速公路机电设备（PLC/传感器）无法直采 | 高（需引入新协议库） |
| 网络设备（已有） | `manager/biz/device/network_discovery.go`（邻居发现/SNMP 校验/30s 轮询）、`edgeagent/biz/snmp_probe.go`、`collector/network_discovery.go` | 交换机/路由器可覆盖 | —（复用） |

### 4.2 告警能力短板

| 短板点 | 代码证据 | 影响 | 弥补难度 |
|--------|----------|------|----------|
| 无独立 alertmanager | 无 alertmanager 组件；评估内嵌 `pipeline.go` PipelineEvaluator | 无联邦/静默分组高级路由 | 中 |
| 抑制仅 2 条内置 | 注释"自定义抑制规则表待未来"（仅 edge_offline→host、prom_ingest_fail→scrape_down） | 告警风暴难抑制 | 低（加规则表） |
| 无 record rule | 无 recording rule 实现 | 无法预聚合派生指标 | 中 |
| 通知无 email | 6 渠道均 webhook 系（`webhook.go`）；**且 `notify.NewFromConfig`（`notify.go:75-89`）仅装配 Webhook/Slack/Feishu/DingTalk，WeCom/Telegram 有 Sender 实现但未接入配置装配** | 工单/邮件出口缺失 | 低 |
| 告警评估依赖 PromQL | `metric_raw` 等 rule 编译成 PromQL（`rules.go:29-46`），依赖 Prometheus 后端 | 无 Prom 则指标告警失效 | 中 |

### 4.3 可扩展性短板

| 短板点 | 代码证据 | 影响 | 弥补难度 |
|--------|----------|------|----------|
| 时序数据无租户隔离 | `manager/biz/metric/repo.go:35-36` 注释 "scoping is by edge_id alone; there is no org_id to enforce" | 多租户数据泄漏风险 | 高（需改表结构+写入路径） |
| 无 API 限流/响应缓存/数据分片 | 全库无 rate-limit middleware、无 cache 层、无 sharding | 大并发易被打垮 | 中 |
| ClickHouse 仅占位 | `manager/data/metric/clickhouse/doc.go` "Phase 2 placeholder" | 时序扩展无落地 | 高（需新实现 Writer/Reader） |
| 单体无 HA | `main.go` 单进程；无 leader election | 单点故障 | 高 |

### 4.4 性能短板

| 短板点 | 代码证据 | 影响 | 弥补难度 |
|--------|----------|------|----------|
| 时序写关系库 | `data/metric/store/writer.go:56/87` CreateInBatches(500) 写 host_metrics_raw/_5m/_1h | 高基数/高频写入成为瓶颈 | 高（换 ClickHouse） |
| 无 Redis 缓存 | 无 redis 驱动 | 热查询/规则缓存缺失 | 低 |
| 降采样在 manager 内串行 | `biz/metric/downsample.go` raw 7d/5m 90d/1h 365d 每日批删 | 数据量大时 CPU/IO 压力 | 中 |
| 无响应缓存 | 分页有但无缓存 | 大盘重复查询打库 | 低 |

---

## 第二大部分：二次开发可行性分析

### 1) 可行性结论与改造点清单

**结论**：可行——OnGrid 的分层架构、防腐层与 5 类扩展点（Plugin/Collector/Sender/Skill/Page）为二次开发提供了干净的扩展面，但其「时序写关系库 + 无租户隔离 + 无 HA + 无动环/拨测/Modbus 采集」四块短板，决定了这是一次**中等偏重的改造**，而非浅层定制。

| # | 改造点 | 现状代码 | 改造方式 | 工作量 |
|---|--------|----------|----------|--------|
| 1 | 时序存储升级 ClickHouse | `clickhouse/doc.go` 占位 + `metric/store` 关系库 Writer | 实现 ClickHouse Writer/Reader，替换 `Writer` 接口实现 | L |
| 2 | 租户隔离（org_id 下钻到时序） | `metric/repo.go:35-36` 无 org_id | 时序表加 org_id 列 + 写入/查询路径注入 | L |
| 3 | HA/限流/缓存 | 无 leader election、无 rate-limit/cache | 加限流中间件 + Redis 缓存 + 多副本 | L |
| 4 | 动环采集（UPS/PDU/温湿度） | 无 | 新增 `edgeagent/plugins/dcim` 插件（SNMP/Modbus-TCP） | M |
| 5 | 链路拨测采集 | `skill/builtin/probe_ping.go` 非数据源 | 新增 `edgeagent/collector` 拨测采集器 + 时序上报 | M |
| 6 | 中间件监控 | `plugins/databasemetrics`/`custommetrics` 需手工配置 | 补齐 exporter 预设 + 自动发现 | M |
| 7 | 机电设备协议（Modbus/OPC-UA） | 仅 gosnmp v2c/v3 | 引入 Modbus/OPC-UA 库，新插件/采集器 | M~L |
| 8 | 告警 rule kind 扩展 | `model/alert/model.go:95-113` 9 种 kind | 新增 kind（如 `link_quality`、`dcim_threshold`）+ evaluator case | M |
| 9 | 通知渠道（email/短信/工单） | `notify.Sender` 接口 + `webhook.go` | 新增 Sender 实现 + `NewFromConfig` case | S |
| 10 | 自定义抑制规则表 | 仅 2 条内置 | 抑制规则落库 | S~M |
| 11 | 业务系统运行状态监控 | `plugins/autoapm` + `biz/apm` 已有 | 复用 + 探活/健康检查插件 | S~M |

### 2) 技术实现路径

**(a) 架构调整建议**

| 调整 | 现有位置 | 改法 |
|------|----------|------|
| 时序上 ClickHouse | `manager/biz/metric/repo.go` 已定义存储无关的 `Writer`/`Reader` 接口（`:16-52`） | 仅需新增 `data/metric/clickhouse/store.go` 实现同接口，装配处按 `ONGRID_DB_DIALECT` 切换，biz 层零改动——**这是原架构预留的正确接缝** |
| 租户隔离 | `metric/repo.go:36` 无 org_id；`authzmw` + `tenantctx` 已有租户上下文 | 时序表加 `org_id` 列，`push_host_metrics`/`push_prom_samples` 入站时从 `tenantctx` 注入；查询按 org_id 过滤 |
| 限流 | HTTP 入口 `internal/pkg/httpserver/server.go`（chi） | 在 chi 中间件链加 token-bucket 限流器 |
| 缓存 | 无 | 引入 Redis，热点（设备列表/规则/仪表盘）缓存；`go.mod` 需加 redis 驱动 |
| HA | `cmd/ongrid/main.go` 单进程 | 多副本 + 外部 LB；告警评估/降采样用 DB 行锁或引入分布式锁串行化 |

**(b) 模块复用与新增（按监控对象）**

| 监控对象 | 复用/改造/新增 | 落点 |
|----------|----------------|------|
| 服务器 | **直接复用** gopsutil 采集 + `push_host_metrics` | `collector/embedded.go` |
| 网络设备 | **直接复用** SNMP v2c/v3 + 邻居发现 | `edgeagent/biz/snmp_probe.go`、`biz/device/network_discovery.go` |
| 业务系统 | **改造复用** autoapm + APM，补探活插件 | `plugins/autoapm`、`biz/apm` |
| 中间件 | **改造复用** databasemetrics/custommetrics，补 exporter 预设 | `plugins/databasemetrics`、`plugins/custommetrics` |
| 机房动环 | **新增** `dcim` 插件（SNMP 读 UPS/PDU、Modbus-TCP 读温湿度） | 新增 `internal/edgeagent/plugins/dcim/` |
| 链路拨测 | **新增** 拨测采集器（icmp/tcp/http/traceroute），结果作 PromSample 上报 | 新增 `internal/edgeagent/collector/probe.go`（或 plugins/probe） |
| 机电设备 | **新增** Modbus/OPC-UA 协议接入，复用 SNMP 通道 | 新增 `internal/edgeagent/plugins/electro/`（modbus 库）+ 复用 `snmp_probe.go` |
| 告警扩展 | **新增** rule kind `dcim_threshold`/`link_quality` | `biz/alert` 新增 evaluator case + `model/alert/model.go` 加常量 |
| 通知扩展 | **新增** email/短信/工单 Sender | `internal/pkg/notify/` 新文件 + `NewFromConfig` case |

**(c) 数据采集与告警扩展方式（沿现有接口的步骤）**

1. **采集扩展**：实现 `plugins.Plugin` 接口（`plugin.go:26` 五方法）→ 在 supervisor 注册表登记 Name → 配置经 `get_plugin_configs`/`plugin_configs_changed`（`tunnel/messages.go:45-49`）下发 → 采集结果走 `push_prom_samples`（`agent.go:699`）上报。动环/机电/拨测均可走此通道，**无需改 manager 核心**。
2. **告警扩展**：`model/alert/model.go:95-113` 加新 `RuleKind*` 常量 → `biz/alert` 的 evaluator（`pipeline.go`/`phaseA.go`）加对应 `case` → 复用 `RecordFiring`/`MaybeNotify` 通知链，**抑制/冷却/dampening 自动继承**。
3. **通知扩展**：实现 `notify.Sender`（`notify.go:34`）→ `NewFromConfig`（`notify.go:75`）加装配 case → `BuildSenderFromChannel`（`usecase.go:1536`）加映射。

### 3) 关键风险

| 风险 | 来源代码证据 | 应对 |
|------|--------------|------|
| 时序写关系库成瓶颈，改造 ClickHouse 成本高 | `metric/store/writer.go:56/87` CreateInBatches 500；`clickhouse/doc.go` 空占位 | 分阶段：先沿用关系库 + 缩短保留期，验证规模后切 ClickHouse（接口已就绪） |
| 多租户数据泄漏（无 org_id 时序隔离） | `metric/repo.go:35-36` 注释 | 上线前必须完成 org_id 改造，否则不可用于多租户 |
| 协议盲区（Modbus/OPC-UA 无现成库） | go.mod 仅 `gosnmp` | 引入成熟库（如 `goburrow/modbus`），机电设备先走 SNMP/网关代理过渡 |
| 单体无 HA，故障影响全链路 | `main.go` 单进程 + errgroup workers | 多副本 + 外部 LB + 告警评估/降采样串行化 |
| AGPLv3 许可证合规 | `LICENSE`（AGPLv3） | 若作为 SaaS 对外提供，需评估开源义务或改用独立部署模式 |

### 4) 工作量评估（按改造点 S/M/L/XL 汇总）

- **S（1-2 周/项）**：通知渠道扩展、自定义抑制规则表、业务系统探活插件 —— 共约 3 项，合计 **≈ 1.5 人月**。
- **M（3-6 周/项）**：动环插件、链路拨测、中间件预设、rule kind 扩展 —— 共约 4 项，合计 **≈ 4 人月**。
- **L（2-3 月/项）**：ClickHouse 升级、租户隔离、HA/限流/缓存 —— 共约 3 项，合计 **≈ 7 人月**。
- **M~L（机电设备 Modbus/OPC-UA）**：≈ 2 人月。

**总区间：约 12-15 人月**（不含测试与运维调优）。

**阶段划分建议**：
1. **阶段一（打地基，约 2-3 月）**：限流 + 缓存 + 告警扩展点 + 租户隔离（改造点 2/3/8/10）。
2. **阶段二（补采集，约 2-3 月）**：动环、拨测、中间件、机电设备插件（改造点 4/5/6/7/11）。
3. **阶段三（上规模，约 2-3 月）**：ClickHouse 迁移 + HA（改造点 1/3）。
