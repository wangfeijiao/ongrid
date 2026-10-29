---
name: docker-container-ops
description: Docker 容器与镜像运维：容器生命周期（启停/日志/exec）、镜像构建清理、 卷与网络检查、磁盘占用分析与三级安全清理建议。Use when the user mentions docker, 容器, 镜像, container, image, volume, 容器起不来, 磁盘满了, "看看哪些容器在跑", "清一下 docker 空间", docker logs, docker exec, prune 清理, 或要求查看/重启/排查任何容器。Also use for 容器退出码排查、 卷占用盘点、镜像瘦身前盘点。Do NOT use for docker compose 多服务编排 （用 docker-compose-dev）、修改 Dockerfile 或构建逻辑、K8s 内容器 （用 kubectl-cluster-ops）、无确认的批量删除。Prefer this over 裸跑 docker system prune for 任何清理诉求。
license: MIT
allowed-tools: Bash(docker:*), Bash(python:*), Read
metadata:
  version: 0.1.0
  complexity: C2
  requires:
    bins:
    - docker
display_name: Docker 容器管家
display_name_en: Docker Container Ops
description_zh: Docker 容器生命周期管理、磁盘占用分析与分级安全清理，卷删除需确认。
description_en: Manage Docker container lifecycle, analyze disk usage and perform tiered safe cleanup with confirmation for volume deletion.
version: 1.0.0
author: 梁灼铭
---

规格卡: dev-004 [P0·C2]

# Docker 容器与镜像运维（docker-container-ops）

容器生命周期、日志排查、磁盘占用分析与三级安全清理。清理类操作一律
"报告 → 建议命令 → 用户确认后执行"；本 skill 的脚本只读不删。

## 何时不用

- **docker compose 多服务编排调试**（up/健康检查/单服务重建）：用 docker-compose-dev。
- **修改 Dockerfile / 构建逻辑**：属于代码编辑，不归本 skill。
- **Kubernetes 里的容器**：用 kubectl-cluster-ops。
- **registry/镜像仓库管理**（push/pull 凭据、配额）：超出本卡范围。

## 前置检查（Preflight）

1. `bash scripts/docker_preflight.sh --json` — 覆盖 CLI 存在、daemon 可达、
   权限、compose 插件四项；成功判据 `.ok == true`。
2. 失败处置按 `.code`：
   - `TOOL_MISSING` → 给安装指引（Docker Desktop / get.docker.com），征得同意再装；
   - `DAEMON_UNREACHABLE` → 引导启动 Docker Desktop（Windows/macOS）或
     `sudo systemctl start docker`（Linux）后重试；
   - `PERMISSION_DENIED` → `sudo usermod -aG docker $USER` 后重新登录。

## 核心工作流（按意图路由）

| 意图 | 80/20 命令 | 确认门 |
|------|-----------|--------|
| 查容器 | `docker ps -a --format "{{json .}}"` | 无 |
| 查日志 | `docker logs --tail 100 <c>`（加 `-f` 跟踪） | 无 |
| 进容器 | `docker exec -it <c> sh` | 无（只读命令） |
| 启/停/重启 | `docker start\|stop\|restart <c>` | stop/restart 复述后执行 |
| 磁盘分析 | `python scripts/space_report.py --json` | 无（只读） |
| 清理 | 按 space_report 的 levels 建议，从 L1 开始 | 见下，逐级确认 |
| 检查元数据 | `docker inspect <c\|i>` | 无 |
| 卷/网络盘点 | `docker volume ls` / `docker network ls` | 无 |

排障链：`docker ps -a` 找容器 → 看 State 与退出码（137=OOM/SIGKILL、
126/127=命令问题）→ `docker logs --tail 100` → `docker inspect` 看
`.State.OOMKilled` 与资源限制 → 给结论。
清理链：`space_report.py --json` 拿三级清单 → 与用户逐级确认 →
执行 → `docker system df` 复核回收效果。

## 按需加载路由表

| 任务 | 先读 |
|------|------|
| 决定清理深度/卷数据安全性 | references/cleanup-levels.md |
| 退出码 137/126/127 等语义 | `docker logs` + `docker inspect .State` 组合判断，必要时查 docker docs |

## 输出契约

- `space_report.py --json`：成功 `{"ok":true,"data":{...}}`，失败
  `{"ok":false,"error":{"code","message","hints"}}`；成功判据 `.ok == true`。
- 关键字段：`data.df[]`（Type/Size/Reclaimable/reclaimable_bytes）、
  `data.total_reclaimable_bytes`、`data.levels.level1/2/3`（scope/commands/gate）、
  在线模式另有 `data.details`（dangling_images/unused_images/stopped_containers/
  dangling_volumes）。
- 退出码：0 成功 / 1 可重试（daemon 不可达等） / 2 需人工（工具缺失）。
- 脚本只读：所有 prune/rm 建议只出现在输出里，脚本自身永不执行删除。
- help 即真相：docker 参数全表以 `docker <cmd> --help` 为准。

## 错误处理

- daemon 不可达 → `DAEMON_UNREACHABLE`：给启动指引，不要盲目重试超过 1 次。
- 权限拒绝（Linux）→ `PERMISSION_DENIED`：docker 组指引。
- `docker system df` 输出解析为空 → 报 CMD_FAILED，附预检命令，不猜原因。
- 空间无可回收 → 明确说"没有可回收空间"，不编造清理收益。

## 安全红线

- **三级确认门**：L1 悬空镜像/停止容器=复述范围后可执行；L2 `image prune -a`
  =列出将删清单并获用户明确同意；L3 卷删除=逐卷点名硬确认，数据不可恢复。
- **绝对禁止**：`docker system prune -a --volumes` 不经逐卷确认直接执行。
- **先备份原则**：删除卷之前提示用户确认数据已备份或确认无用。
- 构建缓存清理（builder prune）、停掉在跑容器同样需要确认。
- 不修改 daemon.json/系统服务配置；不回显任何 registry 凭据。
