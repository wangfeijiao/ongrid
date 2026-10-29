# OnGrid vs NVIDIA Minerva AIOps 平台差距对比分析报告

> **分析日期**：2026-10-26
> **OnGrid 依据**：v0.18.2 代码取证（`D:/code/AI/ongrid`，详见《OnGrid-代码分析与二次开发可行性报告.md》）
> **Minerva 依据**：公开资料检索（NVIDIA 官方博客/产品页/FAQ + 第三方分析，截至 2026-10-26）
> **重要声明**：Minerva 于 **2026-10-22 GTC 柏林期间发布**（发布仅 4 天）；所有量化指标（MTBI/MTTR/告警降噪等）均为 NVIDIA 自述或内部测试转述，**无独立可复现基准**；Morpheus/NIM/NeMo/RAG/向量库等组件官方均未证实与 Minerva 有关。
> **主要来源**：https://blogs.nvidia.com/blog/minerva-platform-infrastructure/ 、https://www.nvidia.com/en-us/networking/products/software/minerva/ 、https://www.bitdeer.ai/en/blog/nvidia-minerva 、https://www.datastudios.org/post/nvidia-minerva-ai-factory-observability-architecture-partners

---

## 1. 定位与目标场景对比

| 维度 | OnGrid | Minerva | 差距判断 |
| --- | --- | --- | --- |
| 产品定位 | 通用基础设施运维 AI Agent：「understand / find root cause / fix，从 Slack/Telegram 发起」 | 「AI 工厂的 AI-native 端到端 AIOps 平台」，NVIDIA AI 工厂主要可观测性解决方案 | 根本差异：通用异构主机运维 vs 垂直 GPU AI 工厂 |
| 核心价值指标 | 未定义量化业务指标；护城河=双向通道+真动手 | **AI factory yield**（有效 GPU 小时 / goodput / 集群 uptime / Token 产出） | Minerva 有业务化北极星指标，OnGrid 缺 |
| 目标用户 | 中小团队/个人自托管，通用基础设施 | 大型 GPU 数据中心 / AI 工厂运营方 | 市场分层明显 |
| 关键差异化 | 零入站、全开源、真动手修复 | 跨层关联产品化、GPU 生态深度 | 各有所长 |

## 2. 架构与技术路线对比

| 维度 | OnGrid（代码事实） | Minerva（公开事实） | 差距判断 |
| --- | --- | --- | --- |
| 分层架构 | manager+edge 双进程；iam/manager/edgeagent 三 BC 五层；arch-lint 强制 BC 隔离 | 四层：Collector / **动态知识图谱** / purpose-built ML / 多智能体 Sherlock+MCP+Grafana | 架构级差距：Minerva 有独立知识图谱层+ML 分析层，OnGrid 无 |
| 跨组件通信 | tunnel wire(JSON)+geminio 总线；edge 主动外联（零入站） | Collector→图谱→ML→交互（细节未披露） | OnGrid 零入站是独特安全优势 |
| 时序/存储 | GORM MySQL/SQLite；时序写关系库 host_metrics_raw/_5m/_1h；无 ClickHouse/MQ/Redis | 未披露存储引擎（每小时数十亿数据点需专用时序栈） | 架构级差距：时序写关系库是明确短板 |
| AI 引擎 | eino agent 框架 + 5 模型热路由 + ONNX embedding + qdrantx RAG | purpose-built ML（监督+无监督）+ Sherlock；官方未提 Morpheus/NIM/NeMo/RAG（未证实） | OnGrid 开源可验证；Minerva 黑盒未披露 |
| 知识图谱 | 无（仅拓扑 blast-radius 影响面） | 动态知识图谱：实时映射 GPU/SuperNIC/交换机/线缆 ↔ 运行作业 | 架构级差距：图谱是 Minerva 核心差异化 |
| 租户隔离 | 无 org_id（`metric/repo.go:35-36` 注释） | 未披露 | OnGrid 明确缺失 |
| HA/扩展 | 单体 manager 无 HA；无 API 限流/缓存/分片 | 官方称可扩展数十万 GPU（自述） | 架构级差距：OnGrid 单点 |

## 3. 监控对象与数据源覆盖对比

| 监控对象 | OnGrid | Minerva |
| --- | --- | --- |
| 通用主机 | ✅ gopsutil（node_exporter 兼容） | 未明确（聚焦 GPU 节点） |
| 容器/K8s | ✅ kube-state-metrics remote_write | 未明确 |
| Prometheus 指标 | ✅ Prom scrape | ❌（走 OTel/gNMI/DCGM） |
| 网络设备 | ✅ SNMP v2c/v3 + 邻居发现 + 30s 轮询 | 部分（Spectrum-X/NVLink/SuperNIC 交换机，gNMI） |
| APM/链路 | ✅ `biz/apm` + autoapm 自动发现 | 未明确（支持 OTel 协议） |
| 日志 | ✅ plugins/logs | ✅ syslog |
| GPU（DCGM/NVML） | ❌ | ✅ DCGM |
| NIC/DPU | ❌ | ✅ ConnectX SuperNIC、BlueField DPU |
| NVLink scale-up 交换机 | ❌ | ✅ |
| NCCL/集合通信 | ❌ | ✅ |
| 调度器（Slurm/Run:ai） | ❌ | ✅ |
| AI 工作负载/Token | ❌ | ✅ |
| 存储 | ❌（仅 databasemetrics 插件） | ✅ |
| 动环（UPS/PDU/温湿度） | ❌ | 未披露 |
| 主动拨测/链路探测 | ❌（唯一 ping 是 AI 技能非数据源） | 未明确 |
| Modbus/OPC-UA | ❌ | 未披露 |
| InfiniBand | ❌ | ❌（归 UFM 管理） |

**判断**：覆盖几乎互补——OnGrid 覆盖通用主机/网络/APM，Minerva 覆盖 GPU 垂直栈；重叠仅日志（syslog）与网络（协议不同）。

## 4. AIOps 能力差距

| 能力 | OnGrid 现状 | Minerva 宣称（厂商自述，无独立基准） | 差距 |
| --- | --- | --- | --- |
| 告警降噪 | 静默/冷却/防抖/抑制（**仅内置 2 条，不可自定义**）；无独立 alertmanager | 告警降噪 99.9%；81 事件收敛为 1 告警 | **大**：OnGrid 抑制不可自定义 |
| RCA 深度 | Coordinator+Specialist 双层 Agent；investigator 因果遍历+变更查询；定位到源码行 | 拓扑追溯到具体组件（异常接口/线缆）+ 拓扑感知事件关联 | Minerva 硬件级细粒度更深（SM/HBM3e/光模块级为第三方推测） |
| 预测性维护 | forecast/anomaly 规则（单指标预测） | MTBI +20% | 中：OnGrid 有单指标预测，缺系统级预测维护 |
| 自动化修复 | AI 写操作默认关闭 + 审批闸门 + bash 沙箱；真动手 | 人在环或经基础设施 API + Ansible 闭环，MTTR −63% | 各有取舍：OnGrid 强审批安全，Minerva 强自动化闭环 |
| 智能体助手 | 双层 Agent；IM 双向通道（Slack/Telegram/飞书/钉钉/企微） | 多智能体 Sherlock（自然语言+实时遥测）+ MCP server | 相当；OnGrid 通道更多，Minerva 多智能体聚合更强 |
| 知识库 | qdrantx 向量库 RAG（显式可验证） | 未披露 RAG/向量库（未证实） | OnGrid 可验证，Minerva 未知 |

## 5. 部署、规模化与商业模式对比

| 维度 | OnGrid | Minerva |
| --- | --- | --- |
| 部署形态 | Manager 仅 Docker Compose；Edge systemd/容器/K8s Helm；一行 install.sh | 本地 on-prem 或 NVIDIA 云 SaaS |
| 批量部署 | deploy-edge-batch（83 IP→37 online） | 未披露（企业级） |
| 规模 | 单 manager，无公开大规模生产部署数据 | 宣称数十万 GPU；IREN 在用，Tesla/IBM/Jio/Lambda 等计划部署 |
| 开源 | AGPLv3 全开源 | 非整体开源，仅 Collector 计划进 DSX OS 开源 |
| 商业模式 | 开源社区 | 三档：DIY / DIY+AIOps 洞察 / 交钥匙；Dell/Lenovo/Supermicro OEM 捆绑 |
| 生态锁定 | 通用异构，无锁定 | 仅 NVIDIA 生态，非 NVIDIA 集群不可用 |

## 6. 差距总结

### (a) OnGrid 能力差距清单（按差距大小排序）

**架构级**：
1. 无动态知识图谱/拓扑关联引擎——Minerva 核心差异化，OnGrid 仅有 blast-radius 静态拓扑；
2. 时序写关系库（`metric/store/writer.go:56/87`）——无法支撑数十亿点/小时，缺 ClickHouse/专用时序栈；
3. 单体 manager 无 HA、无 API 限流/缓存/分片——无法规模化；
4. 无独立 ML 分析层（purpose-built 监督+无监督）——缺系统级预测维护；
5. 告警抑制不可自定义、无独立 alertmanager/record rule——告警收敛能力弱。

**数据/生态级**：
6. 无 GPU/DCGM/NVLink/Spectrum-X/Slurm 数据源——无法进入 AI 工厂场景；
7. 无 org_id 租户隔离——多租户缺失；
8. 无动环/拨测/Modbus/OPC-UA——部分行业覆盖缺口；
9. 无 email 通知渠道；
10. Roadmap 严重滞后于代码（更新 06-06 vs v0.18.2）。

### (b) OnGrid 相对优势

1. 全开源 AGPLv3 + 自托管，无厂商锁定；
2. 异构覆盖：通用主机 + 网络设备（SNMP）+ K8s + APM，非 NVIDIA 集群同样可用；
3. 零入站架构（edge 主动外联）——安全优势，Minerva 未强调；
4. 真动手能力：26+ 只读工具全审计 + 审批闸门 + 浏览器 SSH 反向隧道；
5. 多模型热路由（OpenAI/GLM/DeepSeek/Gemini/Kimi），不绑定单一 LLM；
6. IM 通道更多（Slack/Telegram/飞书/钉钉/企微）；
7. 部署极简（一行 install.sh）。

### (c) 可借鉴方向（对 OnGrid 二次开发最有价值的 5 条）

1. **动态知识图谱/拓扑关联**：将现有 blast-radius 影响面升级为动态资源关系图谱，把 SNMP 邻居发现、K8s 拓扑、APM 调用链统一建模为图；落点：拓扑模块 + geminio 总线已有跨域拓扑基础。
2. **告警收敛指标化 + 可配置抑制**：借鉴 Minerva 99.9% 降噪/事件收敛，将内置 2 条抑制改为可配置规则，引入告警聚合/分组 + 根因告警；落点：`biz/alert/inhibit.go` 与规则 kind。
3. **预测性维护**：在现有 forecast/anomaly 规则上增加 MTBI/寿命预测类指标（磁盘 SMART、电池、温度趋势）；落点：hostmetrics/procmetrics 插件。
4. **时序存储升级 + 独立分析层**：将关系库时序迁移至 ClickHouse（已有 `data/metric/clickhouse/doc.go` 占位 + `repo.go:16-52` 存储无关接口），为 ML 分析打底。
5. **协议生态扩展**：引入 OpenTelemetry/gNMI 采集，向 GPU/DCGM 数据源扩展以切入 AI 场景；落点：plugins 运行时与 Prom scrape。

### (d) 一句话总结

**两者差距的本质**：OnGrid 是「通用异构基础设施的可动手运维 Agent（广度 + 开源 + 安全）」，Minerva 是「GPU AI 工厂的垂直可观测性 + AI 决策平台（深度 + 图谱 + 生态）」——前者缺规模化架构与 AI 工厂深度，后者缺通用性与开放。
