---
name: docker-handler
version: "1.0.0"
display_name: "容器运维（docker-handler）"
display_name_en: "container operations (docker-handler)"
description: "container operations capability in the development and operations domain. Use when the user wants to Docker 容器、网络、镜像的全面管理及系统监控. Triggers: docker-handler, arcane-docker-manager. Not for requests unrelated to container operations. Risk level L3. 中文触发：docker-handler、arcane-docker-manager。"
description_zh: "Docker 容器、网络、镜像的全面管理及系统监控。（类别：三、开发与运维类；风险等级：高（可操作容器与镜像））当用户需要容器运维相关能力、或提到 docker-handler、arcane-docker-manager 时使用；与容器运维无关的请求不要触发。"
description_en: "container operations capability in the development and operations domain. Use when the user wants to Docker 容器、网络、镜像的全面管理及系统监控. Triggers: docker-handler, arcane-docker-manager. Not for requests unrelated to container operations. Risk level L3."
agent_created: true
---

# 容器运维（docker-handler）

## 用途

Docker 容器、网络、镜像的全面管理及系统监控。

> 分类：三、开发与运维类（development and operations）  
> 技能类型：容器运维  
> 风险等级：高（可操作容器与镜像）  
> 门禁档位：**L3** — 涉及资金、交易、凭据或不可逆改动，所有写操作逐项单独授权，生产环境先做沙箱验证。

## 触发条件

- 用户提到 `docker-handler`、需要容器运维能力，或提出对应诉求时触发。
- 典型触发词：docker-handler、Docker 容器、网络、镜像的全面管理及系统监控。
- 关联技能：arcane-docker-manager。

## 何时不要使用

- 与容器运维无关的请求：不要触发，交给更合适的技能或直接回答。
- 前置依赖未就绪（未授权 / 缺 CLI / 缺网络）时：**停下并报告**，不要降级为让用户粘贴明文凭据。
- 超出本技能声明范围的目标（其他系统、其他账号）：不越权操作。

## 功能清单

- 容器生命周期管理
- 网络与镜像管理
- 资源与系统监控

## 标准操作流程

1. 先确认目标环境（本地/容器/远程）与分支/服务范围，避免误伤生产。
2. 优先以只读或 dry-run 方式探查现状（进程、容器、分支状态、依赖）。
3. 执行写操作（构建、部署、改配置、启停服务）前明确影响面并取得确认。
4. 对生产环境、对外服务、花钱或影响他人的动作，逐项单独确认。
5. 输出执行结果、关键日志位置与回滚/止损手段。

## 外部依赖与前置检查

- 依赖：Docker 环境
- 执行前确认依赖已就绪（账号已授权 / CLI 已安装 / 环境变量已设置）。
- 凭据通过环境变量或密钥管理器注入，不落盘、不进版本控制。
- 详细配置见 `references/setup.md`。

## 安全与确认门禁

- 本技能风险等级：**高（可操作容器与镜像）**，对应门禁 **L3**。
- 任何会对外发声、改动账号状态、动资金或不可逆的写操作，**必须逐条取得用户的明确授权**后再执行。
- 生产环境 / 付费 / 影响他人的动作，先做沙箱或单条样本验证。
- 密钥与令牌走环境变量或密钥引用，绝不写入技能文件、命令行参数或对话明文。
- 若前置依赖未就绪（未授权、缺 CLI、缺网络），**停下并报告**，绝不回退到让用户粘贴明文凭据。
- 详细规则见 `references/security.md`。

## 典型使用场景

1. Docker 日常运维
2. 监控容器健康状况

## 使用提示

与 arcane-docker-manager 功能重叠，二选一即可。

## 备注

- 关联技能：arcane-docker-manager。
- 本技能由规划规格 README 自动落地生成，操作细节（具体 API/CLI/连接）应在接入真实运行时补全。
- 通用铁律：先验证现状 → 列影响面 → 取得确认 → 再执行；不可逆或涉钱/涉密的动作用最高门禁。
