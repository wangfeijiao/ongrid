# 容器生命周期与日常操作

## 生命周期全景

```
镜像 pull/build
   ↓
created ──start──> running ──stop──> exited ──start──> running
   │                  │                  │
   │                  ├──pause──> paused  │
   │                  └──restart 策略决定是否自动拉起
   └──rm（需先停止，或 -f 强制）──> 删除
```

## docker run 关键参数全解

```bash
docker run -d \
  --name myapp \
  -p 8080:80 \
  -v /data/myapp:/app/data \
  -e TZ=Asia/Shanghai -e MODE=prod \
  --restart unless-stopped \
  --memory 512m --cpus 1.5 \
  --health-cmd "curl -f http://localhost/health || exit 1" \
  --health-interval 30s --health-retries 3 \
  --log-opt max-size=100m --log-opt max-file=3 \
  myapp:1.0
```

| 参数 | 含义 | 常见坑 |
|---|---|---|
| `-d` | 后台运行 | 不加就前台占住终端，Ctrl-C 会停容器 |
| `-it` | 交互+TTY | 只在调试用；生产别用 |
| `--name` | 指定名字 | 名字冲突要先 rm 旧的 |
| `-p 8080:80` | 宿主机:容器 端口映射 | **写反是最常见错误**；容器监听 127.0.0.1 时映射不出去 |
| `-v 宿主:容器` | 挂载 | 宿主机目录不存在会被当空目录新建；SELinux 系统要加 `:z` |
| `-e` | 环境变量 | 敏感信息别写在命令行（`ps` 能看到），用 `--env-file` 或 secrets |
| `--restart` | 重启策略 | `no`/`on-failure`/`always`/`unless-stopped`；开发用 no，生产用 unless-stopped |
| `--rm` | 退出即删 | 只适合一次性任务，别和 `--restart` 同用 |
| `--network` | 指定网络 | 默认 bridge，服务发现建议自定义网络 |
| `--memory`/`--cpus` | 资源限制 | 不限制则容器可吃光宿主机资源 |
| `--health-cmd` | 健康检查 | 没有它 `docker ps` 的 healthy 永远是空的 |

### 端口映射为什么“不生效”

按顺序排查：
1. `docker port <容器>` 看映射是否真的建立了
2. 容器内服务是否监听 `0.0.0.0`：`docker exec <容器> ss -tlnp`（若是 `127.0.0.1:80` 则宿主机访问不到）
3. 宿主机防火墙/云安全组是否放行
4. `curl localhost:8080` 在宿主机测；`curl -v ip:port` 从外部测，定位在哪一层断
5. `-p 8080:8080` 端口撞车：`ss -tlnp | grep 8080` 找出占用者

## 常用操作矩阵

```bash
# 状态与调试
docker ps                             # 运行中；-a 含已退出（排障必看）
docker inspect <容器>                 # 完整元数据 JSON（网络/挂载/环境变量）
docker logs -f --since 10m <容器>      # 时间过滤日志
docker top <容器>                     # 容器内进程（宿主机视角 PID）
docker diff <容器>                    # 容器文件系统相对镜像的改动
docker port <容器>                    # 端口映射关系

# 交互
docker exec -it <容器> bash           # 进入运行中容器
docker exec -u root -it <容器> bash   # 以 root 进入（镜像默认非 root 时）
docker attach <容器>                  # 附着到主进程（慎用，Ctrl-C 会杀主进程）

# 复制文件
docker cp ./conf.yml <容器>:/app/conf.yml
docker cp <容器>:/app/logs ./logs

# 提交为镜像（临时手段，长期仍应写 Dockerfile）
docker commit <容器> myimage:snapshot

# 停止与删除
docker stop <容器>                    # SIGTERM，10s 后 SIGKILL
docker stop -t 60 <容器>              # 给 60s 优雅退出时间
docker restart <容器>
docker rm <容器>                      # 只能删已停止的；-f 强制
docker rm $(docker ps -aq)           # 批量删所有容器（危险操作，先确认）
```

## 重启策略详解

| 策略 | 行为 | 适用 |
|---|---|---|
| `no` | 不自动重启 | 开发调试、一次性任务 |
| `on-failure[:N]` | 非 0 退出才重启，最多 N 次 | 批处理任务 |
| `always` | 总是重启（含手动 stop 后 daemon 重启也拉起） | 常驻服务 |
| `unless-stopped` | 总是重启，除非被手动 stop | **生产最常用** |

## 数据持久化三选

```bash
-v myvolume:/data          # 命名卷：Docker 管理，性能好、跨平台，生产首选
-v /host/path:/data        # 绑定挂载：直接映射宿主机路径，改配置方便，开发首选
--mount type=tmpfs,destination=/tmp   # 内存盘：不落盘，适合临时/敏感数据
```

判断：**需要备份迁移的持久数据用命名卷；需要频繁改的配置用绑定挂载；不落盘的临时数据用 tmpfs。**

## 一次性任务容器

```bash
docker run --rm -it -v $(pwd):/work -w /work node:20 npm install
# --rm 退出即删，-w 设置工作目录，适合跑工具链而不污染本机环境
```
