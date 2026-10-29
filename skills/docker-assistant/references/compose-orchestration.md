# Compose 编排与生产部署

## 版本说明

- **v1**（`docker-compose`，Python 实现）已停止维护
- **v2**（`docker compose`，Go 插件）是当前标准，随 Docker 一起安装，用空格不用连字符

```bash
docker compose version          # v2 确认
```

## 完整 Compose 示例（Web + DB + 反代）

```yaml
# compose.yaml（v2 推荐文件名，docker-compose.yml 亦可）
name: myapp                        # 项目名，决定容器/网络前缀

services:
  web:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        NODE_ENV: production
    image: myapp:1.0               # 同时指定 build 和 image：构建后打这个 tag
    ports:
      - "127.0.0.1:8080:3000"      # 只绑定本机，安全（由 nginx 对外）
    environment:
      - TZ=Asia/Shanghai
      - DB_HOST=db                 # 用服务名当主机名（compose 内置 DNS）
    env_file:
      - .env                       # 敏感变量放这里，并加入 .gitignore
    volumes:
      - app-data:/app/data
      - ./conf:/app/conf:ro        # :ro 只读挂载
    depends_on:
      db:
        condition: service_healthy # 等 db 健康再启动（v2 特性，v1 的 depends_on 只保证顺序）
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    restart: unless-stopped
    deploy:
      resources:
        limits: { cpus: "1.5", memory: 512M }
    logging:
      driver: json-file
      options: { max-size: "100m", max-file: "3" }

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD:?必须设置DB_PASSWORD}   # :? 未设则报错退出
    volumes:
      - db-data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      retries: 5
    restart: unless-stopped

volumes:
  app-data:
  db-data:

networks:
  default:
    name: myapp-net
```

## 常用命令

```bash
docker compose up -d                 # 构建并后台启动
docker compose up -d --build         # 强制重新构建
docker compose ps                    # 服务状态（含健康状态）
docker compose logs -f web           # 跟踪某服务日志
docker compose exec web sh           # 进入服务容器
docker compose restart web           # 重启单个服务
docker compose down                  # 停止并删除容器/网络（保留卷）
docker compose down -v               # ⚠️ 连数据卷一起删，数据不可恢复！
docker compose config                # 校验并展开配置（排错必用）
docker compose up -d --scale web=3   # 横向扩到 3 个实例
```

## 关键坑点

1. **容器内不能用 `localhost` 访问其他服务**，要用 `服务名`（如 `db:5432`）——这是最常见的连接失败原因
2. `depends_on` 只管启动顺序，**不代表依赖已就绪**，必须配 `condition: service_healthy` + 服务自身健康检查
3. 改完 compose 文件要 `docker compose up -d` 才会生效，`restart` 不会重新读取配置
4. 环境变量优先级：`environment` > `env_file` > shell 环境；变量插值 `${VAR}` 取自 shell 与 `.env` 文件
5. 卷名会在前面加项目名前缀（`myapp_db-data`），`docker volume ls` 里看不到裸名字
6. 生产别用 `--scale` 做负载均衡（端口会冲突），需要横向扩展应上 K8s/Swarm 或在前面挂 nginx

## 生产部署建议

- **一个容器一个进程**，不要塞 supervisord 跑多服务（重启粒度、日志、扩缩容都会变糟）
- 镜像 tag 用**具体版本或 git commit**，禁止 `latest`（无法回滚、不可复现）
- 日志用 `json-file` + `max-size/max-file` 轮转，或直接输出 stdout 交给外部收集（Loki/ELK）
- 时间统一：`-e TZ=Asia/Shanghai` + 宿主机 NTP 同步
- 用 `deploy.resources.limits` 或 `--memory/--cpus` 限制资源，防止单容器拖垮宿主机
- 密钥不入镜像不入 Git：用 `.env`（加 .gitignore）、`--env-file` 或 Docker secrets
- 上线前验证：`docker compose config` → `docker compose up -d` → `curl localhost:8080/health`
