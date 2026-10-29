# Dockerfile 指令与最佳实践（dockerfile）

本文件为离线参考，列出常见 Dockerfile 指令语义与编写最佳实践。

---

## 1. 常见指令

### FROM
指定基础镜像，每个 Dockerfile 的第一个指令。
```dockerfile
FROM node:20-alpine
FROM python:3.11-slim AS builder
```
- 推荐使用带具体版本标签与较小基础镜像（alpine / slim / distroless）以减少体积。
- 多阶段构建时使用 `AS <name>` 命名阶段。

### RUN
在镜像构建期执行命令并提交为新层。
```dockerfile
RUN apt-get update && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/*
```
- 将多条命令用 `&&` 合并为一条 `RUN`，减少镜像层数。
- 安装后清理缓存（如上例 `rm -rf /var/lib/apt/lists/*`）。

### COPY / ADD
```dockerfile
COPY package*.json /app/
COPY . /app/
```
- 优先使用 `COPY`（语义清晰、不自动解压）。
- `ADD` 仅在需要自动解压 tar 或拉取远程 URL 时使用。
- 区分"先拷贝依赖清单再安装"，以利用构建缓存。

### ENV
设置环境变量（写入镜像，运行时可见）。
```dockerfile
ENV NODE_ENV=production \
    APP_PORT=8080
```

### EXPOSE
声明容器监听的端口（仅为文档与 `docker run -P` 映射使用，不真正发布）。
```dockerfile
EXPOSE 8080
```

### CMD
容器启动时默认执行的命令，**可被 `docker run` 末尾参数覆盖**。
```dockerfile
CMD ["node", "server.js"]      # exec 形式（推荐）
CMD node server.js             # shell 形式
```

### ENTRYPOINT
配置容器为可执行程序，参数追加到其后。
```dockerfile
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["--help"]
```
- 组合 `ENTRYPOINT` + `CMD`：ENTRYPOINT 固定程序，CMD 作为默认参数。
- 仅用 CMD 时，用户传入的命令会完全替换 CMD。

---

## 2. 多阶段构建示例

```dockerfile
# 构建阶段
FROM golang:1.22 AS build
WORKDIR /src
COPY . .
RUN go build -o /app/server .

# 运行阶段
FROM gcr.io/distroless/static-debian12
COPY --from=build /app/server /server
EXPOSE 8080
ENTRYPOINT ["/server"]
```
优势：最终镜像仅含二进制，体积小、攻击面小。

---

## 3. 最佳实践

1. **合并层**：用 `&&` 连接相关命令并清理缓存，减少层数。
2. **用 `.dockerignore`**：排除 `node_modules`、`.git`、本地构建产物，避免上下文过大与泄露。
   ```
   .git
   node_modules
   dist
   *.log
   .env
   ```
3. **非 root 用户运行**：降低容器逃逸风险。
   ```dockerfile
   RUN addgroup -S app && adduser -S app -G app
   USER app
   ```
4. **固定基础镜像版本**：避免 `latest` 漂移导致构建不可复现。
5. **利用缓存**：将变化频率低的指令（如 `COPY package.json` + `RUN install`）放在前面。
6. **最小化安装**：仅安装运行时必要依赖，用 `-y --no-install-recommends` 等选项。
7. **声明元数据**：可加 `LABEL maintainer=...` 便于追溯。

---

## 4. 构建命令对应

```bash
docker build -t myapp:1.0 -f Dockerfile .
docker build --no-cache -t myapp:1.0 .
```
详见 scenarios.md 第 2 节。
