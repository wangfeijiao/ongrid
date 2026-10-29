# Dockerfile 与镜像构建

## 一份生产级 Dockerfile 范例（Node.js 多阶段）

```dockerfile
# ---------- 构建阶段 ----------
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev                    # 先拷依赖清单，利用缓存
COPY . .
RUN npm run build

# ---------- 运行阶段 ----------
FROM node:20-alpine
RUN apk add --no-cache tini              # tini 做 PID 1，正确转发信号
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
RUN addgroup -S app && adduser -S app -G app && chown -R app:app /app
USER app                                 # 非 root 运行
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:3000/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "dist/main.js"]             # exec 形式，信号才传得到
```

## 指令详解与坑点

| 指令 | 作用 | 关键坑 |
|---|---|---|
| `FROM` | 基础镜像 | 用 `xxx:slim`/`alpine` 减小体积；alpine 用 musl libc，二进制依赖可能不兼容，可用 `debian-slim` 折中 |
| `WORKDIR` | 设置工作目录 | 会自动创建；不要用 `RUN cd xxx` 代替 |
| `COPY` | 复制文件 | 优先 `COPY` 而非 `ADD`（ADD 会解压和拉远程，行为隐晦） |
| `RUN` | 构建时执行 | **多条 shell 命令用 `&&` 合并成一条**，否则每行一层 |
| `ENV` / `ARG` | 环境变量/构建参数 | `ARG` 构建后不保留（不要用它放密码）；`ENV` 会留在镜像里 |
| `EXPOSE` | 声明端口 | 仅文档作用，**不会真的映射端口** |
| `VOLUME` | 声明卷 | 小心：此后层对该目录的写入不落镜像 |
| `USER` | 切换用户 | 必须是已存在的用户，否则构建失败 |
| `ENTRYPOINT` + `CMD` | 启动入口 | ENTRYPOINT 固定命令，CMD 给默认参数；**用 exec 数组形式**才能收到信号 |
| `HEALTHCHECK` | 健康检查 | K8s 不用它（用 probe），但 docker run/compose 依赖它 |

**CMD vs ENTRYPOINT 组合规则**：

```dockerfile
ENTRYPOINT ["nginx"]     # 固定入口
CMD ["-g","daemon off;"] # 默认参数，docker run 时传参会覆盖 CMD
# docker run mynginx -v  → 最终执行 nginx -v
```

## 镜像瘦身

1. **多阶段构建**：构建工具链不进最终镜像（Go/Rust/前端收益最大，可从 GB 级降到几十 MB）
2. **合并 RUN 层**：`RUN a && b && c` 一条命令，并记得清缓存

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*        # 清 apt 缓存，能省几十 MB
```

3. **`.dockerignore`**（放在构建上下文根目录）：

```
.git
node_modules
*.log
.env
dist
Dockerfile
README.md
```

4. 用 distroless/scratch 做最终基础镜像（无 shell，攻击面最小，但调试困难）
5. 检查体积构成：`docker history --no-trunc myapp:1.0`、`dive myapp:1.0`（分层可视化工具）

## 构建缓存机制

Docker 逐层比对，**某层内容或上层变化 → 该层及之后所有层缓存失效**。所以顺序至关重要：

```dockerfile
COPY package.json package-lock.json ./   # 变动少 → 放前面
RUN npm ci
COPY . .                                 # 代码变动频繁 → 放后面
```

BuildKit 新特性（现代 docker build 默认启用）：

```bash
docker build --cache-from registry/myapp:cache --cache-to type=inline -t myapp:1.0 .
DOCKER_BUILDKIT=1 docker build --secret id=npmrc,src=$HOME/.npmrc -t myapp:1.0 .
# 挂载缓存目录，加速依赖安装且不进入镜像层
docker build --mount=type=cache,target=/root/.npm -t myapp:1.0 .
```

## 构建与推送

```bash
docker build -t myapp:1.0 .                       # 注意最后的 .（构建上下文）
docker build -f docker/Dockerfile.prod -t myapp:1.0 .
docker buildx build --platform linux/amd64,linux/arm64 -t reg/myapp:1.0 --push .
# 多架构镜像：一次构建，两种 CPU 架构都能跑（M 系 Mac 部署到 x86 服务器必备）
```

## 常见构建失败原因

| 报错 | 原因与解法 |
|---|---|
| `Cannot connect to the Docker daemon` | 服务没启动或用户不在 docker 组 |
| `failed to solve: not found` | 构建上下文不对，检查 `.` 与 `-f` 路径 |
| `COPY failed: file not found` | 文件被 `.dockerignore` 排除，或路径相对上下文写错 |
| `no space left on device` | `docker system df` 查看，清理构建缓存 `docker builder prune` |
| `exec format error` | 架构不匹配，用 buildx 构建对应架构镜像 |
| 拉基础镜像超时 | 配镜像加速（见 concepts-history.md） |
