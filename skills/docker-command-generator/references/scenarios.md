# 常用 Docker 命令与范例（scenarios）

本文件为离线参考，包含所有常见 `docker` 与 Compose 命令的可执行范例。
约定：`< >` 内为需替换为实际值的占位符；`#` 后为注释。

---

## 1. 运行容器 `docker run`

### 基本后台运行并映射端口
```bash
docker run -d --name nginx -p 8080:80 nginx:latest
# -d            后台运行
# --name nginx  容器命名，便于后续操作
# -p 8080:80    宿主机端口:容器端口
# nginx:latest  镜像:标签（省略标签默认 latest）
```

### 挂载卷（数据持久化）
```bash
docker run -d --name mysql \
  -e MYSQL_ROOT_PASSWORD=secret \
  -v /srv/mysql-data:/var/lib/mysql \
  -p 3306:3306 \
  mysql:8.0
# -e    注入环境变量
# -v    宿主机目录:容器目录（绑定挂载）
```

### 使用命名卷（推荐用于数据库）
```bash
docker volume create mysql-data
docker run -d --name mysql \
  -e MYSQL_ROOT_PASSWORD=secret \
  -v mysql-data:/var/lib/mysql \
  mysql:8.0
```

### 设置重启策略与资源限制
```bash
docker run -d --name web \
  --restart unless-stopped \
  --memory=512m --cpus=1.0 \
  -p 8080:80 nginx:latest
```

### 交互式运行（调试）
```bash
docker run -it --rm ubuntu:22.04 /bin/bash
# -it   分配伪终端
# --rm  退出后自动删除容器
```

---

## 2. 构建镜像 `docker build`

```bash
# 使用当前目录 Dockerfile
docker build -t myapp:1.0 .

# 指定 Dockerfile 路径与构建参数
docker build -t myapp:1.0 -f ./docker/Dockerfile --build-arg ENV=prod .

# 无缓存重建
docker build --no-cache -t myapp:1.0 .
```

---

## 3. 查看镜像与容器

```bash
docker images                 # 列出本地镜像
docker images -a              # 含中间层镜像
docker ps                     # 运行中的容器
docker ps -a                  # 所有容器（含已停止）
docker ps -q                  # 仅输出容器 ID（便于批量操作）
docker inspect <container>    # 查看容器详细配置
docker stats                  # 实时资源占用
```

---

## 4. 进入容器与查看日志

```bash
docker exec -it <container> /bin/bash   # 进入运行中容器
docker exec <container> ls /app         # 执行单条命令
docker logs <container>                 # 查看日志
docker logs -f --tail 100 <container>   # 实时跟踪最后 100 行
docker logs --since 1h <container>      # 最近 1 小时日志
```

---

## 5. 停止 / 启动 / 重启

```bash
docker stop <container>        # 优雅停止（默认 10s 超时）
docker stop -t 30 <container>  # 30s 超时
docker start <container>       # 启动已停止容器
docker restart <container>     # 重启
docker pause <container>       # 暂停（冻结进程）
docker unpause <container>     # 恢复
```

---

## 6. 删除容器与镜像

```bash
docker rm <container>              # 删除已停止容器
docker rm -f <container>           # 强制删除运行中的容器
docker rm $(docker ps -aq)         # 删除所有容器
docker rmi <image>                 # 删除镜像
docker rmi -f <image>              # 强制删除
docker image prune                 # 删除悬空镜像
docker rmi $(docker images -aq)    # 删除所有镜像
```

---

## 7. 卷（volume）

```bash
docker volume create mydata        # 创建命名卷
docker volume ls                   # 列出卷
docker volume inspect mydata       # 查看卷详情
docker volume rm mydata            # 删除卷（数据丢失！）
docker run -v mydata:/data ...     # 挂载使用
```

---

## 8. 网络（network）

```bash
docker network create mynet                # 创建自定义桥接网络
docker network ls                          # 列出网络
docker network connect mynet <container>   # 连接容器到网络
docker network disconnect mynet <container>
docker run --network mynet ...             # 启动时指定网络
docker network rm mynet                    # 删除网络
```

---

## 9. Compose 常用命令

```bash
docker compose up -d            # 后台启动全部服务（新版插件）
docker-compose up -d           # 旧版独立二进制写法

docker compose ps              # 查看 compose 服务状态
docker compose logs -f web     # 跟踪某服务日志
docker compose stop            # 停止但不删
docker compose start
docker compose restart
docker compose down            # 停止并移除容器/网络
docker compose down -v         # 同时删除卷（数据丢失！）
docker compose build           # 重新构建镜像
docker compose pull            # 拉取最新镜像
```

---

## 10. 系统清理

```bash
docker system df                # 查看磁盘占用
docker system prune             # 清理停止容器/悬空镜像/网络
docker system prune -a          # 额外清理所有未引用镜像
docker system prune -a --volumes  # 额外清理未使用卷（危险）
```
