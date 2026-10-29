# 故障排查手册

## 通用排障流程

```
1. docker ps -a                → 容器是什么状态？（Up / Exited / Restarting / Created）
2. docker logs --tail 200      → 应用为什么退出？
3. docker inspect 的 State     → 退出码 / OOMKilled / Error 字段
4. docker exec -it sh          → 进容器实测网络、文件、进程
5. 宿主机侧：df -h / free -h / dmesg -T / journalctl -u docker
```

## 症状 → 原因速查表

| 症状 | 常见原因 | 处理 |
|---|---|---|
| 容器立刻退出（Exited 0/1） | 主进程不是前台运行；应用配置/依赖错误 | 看 `docker logs`；Dockerfile 中用 exec 形式的 ENTRYPOINT |
| 退出码 137 | 被 SIGKILL，**多为内存超限 OOM** | `docker inspect ... .State.OOMKilled`；调大 `--memory` 或查内存泄漏 |
| 退出码 139 | 段错误（glibc/musl 不兼容、native 库问题） | 换 `debian-slim` 基础镜像或重建 native 依赖 |
| 退出码 126/127 | 无执行权限 / 命令不存在 | 检查 ENTRYPOINT 路径、脚本可执行位与 shebang |
| 容器不断重启 | 应用崩溃 + `--restart always` | 先 `docker update --restart=no` 止血，再查日志 |
| `docker: command not found` | 未安装或 PATH 问题 | 见 @references/concepts-history.md |
| `Cannot connect to the Docker daemon` | 服务未启动/用户不在 docker 组 | `systemctl start docker`；`usermod -aG docker $USER` |
| `port is already allocated` | 宿主端口被占 | `ss -tlnp \| grep <端口>`，改映射或停占用进程 |
| 映射端口访问不通 | 绑定 127.0.0.1 / 服务未监听 0.0.0.0 / 防火墙 / 安全组 | 见 @references/networking.md 排查顺序 |
| 容器内 `no space left on device` | 宿主分区满 或 容器 tmpfs/可写层满 | `df -h` + `docker system df -v` → 清理 |
| 容器 DNS 解析失败 | 宿主 resolv.conf / Docker DNS 配置 | `docker exec ... cat /etc/resolv.conf`；daemon.json 配 dns |
| 构建时拉基础镜像超时 | 网络/镜像源 | 配 registry-mirrors |
| 构建失败 `failed to compute cache key` | `.dockerignore` 排除或上下文路径错 | `docker compose config` / 检查上下文 |
| 容器时间不对 | 容器时区 UTC | `-e TZ=Asia/Shanghai` 并装 tzdata |
| `permission denied` 访问挂载目录 | SELinux / UID 不匹配 | 挂载加 `:z`；或 `--user $(id -u):$(id -g)` |

## OOM 排查（137 最多的原因）

```bash
docker inspect <容器> --format '{{.State.OOMKilled}} {{.State.ExitCode}}'
dmesg -T | grep -i -A5 "oom"                     # 宿主内核 OOM 记录
docker stats --no-stream <容器>                   # 对照限制与实际使用
cat /sys/fs/cgroup/system.slice/docker-<ID>.scope/memory.max
```

方向：① 限制给小了 → 调大；② 应用内存泄漏 → 看应用日志/堆快照（JVM 加 `-XX:MaxRAMPercentage=75`）；③ 页缓存/线程栈占用 → 限制值留 20% 余量。

## 磁盘爆满处理（安全顺序）

```bash
df -h && docker system df -v            # 1. 确认占用来源
docker container prune                  # 2. 删已停止容器（安全）
docker image prune                      # 3. 删悬空镜像（安全）
docker builder prune                    # 4. 删构建缓存（回收最大，安全）
docker image prune -a                   # 5. 删所有未使用镜像（确认后执行）
docker volume prune                     # 6. ⚠️ 删未使用卷，**可能丢数据**，先核对
truncate -s 0 $(docker inspect --format='{{.LogPath}}' <容器>)   # 7. 单容器日志过大时清空
```

⚠️ 第 5、6 步不可恢复，执行前先和用户确认；第 6 步建议先 `docker volume ls -f dangling=true` 列出来给用户核对。

## 容器内无法上网 / 服务不通

```bash
docker exec <容器> ping -c2 8.8.8.8          # 有 IP 无 DNS → DNS 问题
docker exec <容器> nslookup baidu.com        # DNS 解析测试
docker exec <容器> ss -tlnp                  # 服务真在监听？绑的是 0.0.0.0？
sysctl net.ipv4.ip_forward                   # 必须为 1（宿主 NAT 依赖）
sudo iptables -t nat -L DOCKER -n --line-numbers | head
```

## daemon 层问题

```bash
sudo journalctl -u docker -n 200 --no-pager     # daemon 日志
docker info                                     # 检查驱动/存储/警告
sudo systemctl restart docker                   # 卡死时重启（会停容器，先告知用户）
# daemon 起不来常见：daemon.json 语法错误 → 用 json 校验；或 data-root 磁盘满
```

## 恢复类操作

```bash
# 容器配置被改坏了 —— 从运行中容器反推
docker inspect <容器> > container-backup.json
docker run --rm -v <容器>:/old alpine tar cf - . | ...   # 导出可写层（需停机）

# 用镜像重开一个（无状态服务最优解）
docker run -d --name myapp-new <原参数> myapp:1.0
docker stop myapp && docker rm myapp && docker rename myapp-new myapp

# 批量清理停滞的容器
docker ps -a --filter status=exited --filter status=dead -q | xargs -r docker rm
```

## 向用户输出排障结论的模板

1. **现象**：一句话复述（哪个容器、什么状态、退出码）
2. **定位**：指明证据（`docker logs` 哪几行、inspect 哪个字段）
3. **修复**：给出可直接执行的命令，并说明副作用
4. **验证**：`docker ps` 看 Up+healthy、`curl` 测接口、`docker logs` 无新错误
5. **防复发**：加健康检查、资源限制、日志轮转、监控告警
