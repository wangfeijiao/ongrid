# Docker Compose 编排范例（compose）

本文件为离线参考，说明 `docker-compose.yml`（及新版 `compose.yaml`）的常见结构。
注：新版 Docker 推荐文件名 `compose.yaml`，旧版常用 `docker-compose.yml`，二者语法一致。

---

## 1. 最小结构

```yaml
services:
  web:
    image: nginx:latest
    ports:
      - "8080:80"
```

---

## 2. 完整范例：redis + web

```yaml
version: "3.9"

services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis-data:/data
    networks:
      - app-net

  web:
    build: ./web
    image: myweb:1.0
    ports:
      - "8080:3000"
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    depends_on:
      - redis
    restart: unless-stopped
    networks:
      - app-net

volumes:
  redis-data:

networks:
  app-net:
    driver: bridge
```

### 字段说明
- `services`：定义每个服务（容器）。
- `image`：直接使用现成镜像。
- `build`：基于本地 Dockerfile 构建（可写 `build: ./dir` 或 `build: { context: ., dockerfile: Dockerfile }`）。
- `ports`：宿主机端口:容器端口 列表。
- `volumes`：挂载命名卷或绑定路径（如 `- ./data:/app/data`）。
- `environment`：注入环境变量（列表或 `KEY: VALUE` 映射）。
- `depends_on`：声明启动/停止依赖顺序（注意：默认不等待依赖"就绪"，仅等待"启动"）。
- `networks`：将服务接入自定义网络，实现服务间按名称互访。
- `restart`：重启策略（`no` / `on-failure` / `unless-stopped` / `always`）。

---

## 3. 常用命令

```bash
docker compose up -d           # 后台启动（新版插件）
docker-compose up -d          # 旧版写法
docker compose up -d --build  # 先构建再启动
docker compose ps
docker compose logs -f web
docker compose stop
docker compose down           # 停并移除容器/网络
docker compose down -v        # 同时删卷（数据丢失！）
docker compose restart web
```

---

## 4. 进阶提示

- **多文件合并**：可用 `-f base.yml -f prod.yml` 叠加配置。
- **环境变量文件**：`env_file: .env` 批量注入；`.env` 中 `KEY=VALUE`。
- **健康检查**：
  ```yaml
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
    interval: 30s
    timeout: 5s
    retries: 3
  ```
- **资源限制**：
  ```yaml
  deploy:
    resources:
      limits:
        memory: 512M
  ```
  注意：`deploy.resources` 在 `docker compose up` 下生效（旧 `docker-compose` 需 v3+ swarm 才支持，单机用 `mem_limit` 等字段兼容）。
- **版本字段弃用**：Compose Spec 已不再要求 `version` 字段，可省略。

---

## 5. 版本差异提示

- 新版 `docker compose`（无横线，Go 插件，Docker v20.10+）：推荐。
- 旧版 `docker-compose`（有横线，Python 编写，已逐步弃用）：命令基本一致，但需单独安装。
- 输出命令时建议同时给出兼容写法，并提示用户按本机环境选择。
