---
name: docker-command-generator
display_name: Docker 命令生成器
display_name_en: Docker Command Generator
description: 将容器/镜像/编排操作意图转化为正确的 Docker 与 Compose 命令并提示破坏性风险
description_zh: 将容器/镜像/编排操作意图转化为正确的 Docker 与 Compose 命令并提示破坏性风险
description_en: "Translate container/image/orchestration intents into correct Docker and Compose commands with destructive-risk warnings"
version: 1.0.0
author: AI_工具开发者
category: coding
user_invocable: true
---

# Docker 命令生成器

本技能完全离线运行、零外部依赖。它将用户用自然语言描述的容器、镜像、网络、卷以及 Compose 编排意图，转化为**正确、可直接执行**的 `docker` / `docker-compose`（及新版 `docker compose`）命令，并对破坏性操作主动给出数据丢失风险告警。

## ① 触发条件

当用户表达以下任一意图时启用本技能：

- 启动 / 停止 / 重启 / 删除容器（如"跑一个 nginx 容器并映射 8080"）
- 构建 / 查看 / 删除镜像（如"基于 Dockerfile 构建镜像""清理无用镜像"）
- 查看日志、进入容器执行命令（如"看下 web 容器日志""进容器改配置"）
- 创建 / 挂载卷、配置网络（如"建一个数据卷给 mysql 用"）
- 编写或操作 `docker-compose.yml`（如"写个 compose 起 redis + web"）
- 清理环境（如"删掉所有停止的容器""释放磁盘空间"）

## ② 工作流

1. **解析意图**：识别操作类型（run / build / exec / logs / stop / rm / prune / compose ...）与目标对象（镜像名、容器名、端口、卷、网络）。
2. **补全缺失参数**：若出现歧义，向用户澄清关键信息，至少包括：
   - 镜像名与标签（默认 `:latest` 需明确提示）
   - 宿主机与容器端口映射关系
   - 挂载路径（宿主机目录 ↔ 容器目录）
   - 是否为后台运行（`-d`）
3. **生成命令**：依据 @references 中的范例拼装命令，优先给出最简洁、安全的写法。
4. **风险判定**：若命令属破坏性操作（rm / prune / down -v / rmi / volume rm），按 `dangerous.md` 附加告警与确认步骤。
5. **版本提示**：区分新版 `docker compose`（无横线，Docker v20.10+ 插件）与旧版 `docker-compose`（独立二进制），并提示 sudo / daemon 权限问题。

## ③ 输出格式

对每一条意图，按以下结构输出：

```
【命令】
docker run -d --name nginx -p 8080:80 nginx:latest

【解释】
- -d            后台运行（detached）
- --name nginx  指定容器名为 nginx
- -p 8080:80    宿主机 8080 端口映射到容器 80 端口
- nginx:latest  使用的镜像及标签

【风险】
⚠️ 无破坏性风险。如端口 8080 被占用请更换宿主机端口。
```

破坏性操作示例：

```
【命令】
docker system prune -a

【解释】
- -a  同时删除所有未被容器引用的镜像（不仅是 dangling 镜像）
- 默认不删除卷；如需删卷加 --volumes

【风险】
⚠️ 破坏性：将删除所有已停止容器、未被使用的网络、构建缓存，
   以及（加 -a 后）所有未被任何容器引用的镜像。
   执行前请确认没有需要保留的镜像 / 构建缓存。
   建议先运行 docker system df 查看占用，再确认。
```

## @references

本技能所有知识已内置于以下离线参考文件，使用时按需引用：

- [scenarios.md](references/scenarios.md) —— 常用 `docker` 命令与可执行范例（run/build/images/ps/exec/logs/stop/start/restart/rm/rmi/volume/network/compose）。
- [dockerfile.md](references/dockerfile.md) —— 常见 Dockerfile 指令（FROM/RUN/COPY/ADD/ENV/EXPOSE/CMD/ENTRYPOINT）与最佳实践。
- [compose.md](references/compose.md) —— `docker-compose.yml` 结构范例（services/image/ports/volumes/environment/depends_on/networks/restart）。
- [dangerous.md](references/dangerous.md) —— 破坏性操作告警与确认步骤。

## ④ 边界与兜底

- **破坏性操作主动告警**：凡涉及 `prune` / `rm` / `rmi` / `volume rm` / `down -v` 等，必须在输出中标注数据丢失风险并给出确认步骤，不得静默执行。
- **意图模糊澄清**：缺少镜像名、端口或挂载路径时，先向用户追问关键参数，再生成命令；不臆造路径。
- **Compose 命令选择**：提示用户本机是 Docker v20.10+ 插件（用 `docker compose`，无横线）还是旧版独立二进制（用 `docker-compose`，有横线），并给出兼容写法。
- **权限提示**：若用户在 Linux 非 root 环境，提示可能需要 `sudo` 或将用户加入 `docker` 组；Docker daemon 未启动时提示 `systemctl start docker`。
- **版本差异**：新版 `docker compose` 与旧版 `docker-compose` 子命令基本一致，但旧版依赖 Python 环境、已逐步弃用；优先推荐新版插件写法。
- **不覆盖未知场景**：遇到本技能 references 未覆盖的高级场景（如 swarm、buildx、多阶段跨平台构建），说明限制并给出可继续探索的方向，不编造命令。
