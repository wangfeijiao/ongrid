# 镜像仓库与 CI/CD 集成

## 镜像命名规范

```
<registry>/<namespace>/<repository>:<tag>@<digest>
registry.example.com/team/api:1.4.2@sha256:abc...
```

- 不写 registry → 默认 Docker Hub（`docker.io/library/`）
- **禁止生产使用 `latest`**：不可追溯、无法回滚、多机可能拉到不同版本
- 推荐 tag 策略：`1.4.2`（语义版本）+ `1.4`、`1.4.2-git-a1b2c3d`（可追溯），必要时 `@digest` 锁死

## Docker Hub 与国内加速

```bash
docker login                                                     # 登录 Docker Hub
docker login registry.cn-hangzhou.aliyuncs.com                   # 登录阿里云 ACR
docker tag myapp:1.0 registry.cn-hangzhou.aliyuncs.com/ns/myapp:1.0
docker push registry.cn-hangzhou.aliyuncs.com/ns/myapp:1.0
docker pull registry.cn-hangzhou.aliyuncs.com/ns/myapp:1.0
docker logout
```

拉取慢/失败时配 `registry-mirrors`（见 @references/concepts-history.md）。

## 私有仓库

### 快速起一个（registry:2）

```bash
docker run -d -p 5000:5000 --name registry \
  -v /data/registry:/var/lib/registry \
  -e REGISTRY_STORAGE_DELETE_ENABLED=true \
  registry:2

# 因默认走 HTTPS，本机 HTTP 需在 /etc/docker/daemon.json 加：
# { "insecure-registries": ["192.168.1.10:5000"] }
docker tag myapp:1.0 192.168.1.10:5000/myapp:1.0
docker push 192.168.1.10:5000/myapp:1.0
curl http://192.168.1.10:5000/v2/_catalog        # 列出仓库中的镜像
```

### Harbor（企业级，推荐）

提供 UI、RBAC、镜像扫描（Trivy）、复制、保留策略、GC。安装要点：

```bash
# 下载 offline 安装包，改 harbor.yml 设置 hostname/密码/证书，然后：
sudo ./install.sh
docker compose -f docker-compose.yml down && sudo ./install.sh   # 改配置后重装
```

## CI/CD 流水线集成

```bash
# 登录（CI 中用 secret 变量，别明文）
echo "$REGISTRY_PASSWORD" | docker login $REGISTRY -u $REGISTRY_USER --password-stdin

# 构建多架构并直接推送（Mac 本地构建部署到 x86 服务器必备）
docker buildx create --use --name multiarch
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --label org.opencontainers.image.revision=$GIT_SHA \
  --cache-from type=registry,ref=$REGISTRY/ns/myapp:buildcache \
  --cache-to   type=registry,ref=$REGISTRY/ns/myapp:buildcache,mode=max \
  -t $REGISTRY/ns/myapp:$GIT_SHA -t $REGISTRY/ns/myapp:latest \
  --push .
```

GitLab CI 片段示例：

```yaml
build:
  image: docker:27-cli
  services: [docker:27-dind]
  variables:
    DOCKER_TLS_CERTDIR: "/certs"
  script:
    - echo "$CI_REGISTRY_PASSWORD" | docker login -u "$CI_REGISTRY_USER" --password-stdin $CI_REGISTRY
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA
```

## 镜像分发优化

| 手段 | 收益 |
|---|---|
| 多阶段构建 + slim 基础镜像 | 体积从 GB 降到几十 MB，拉取更快 |
| 复用同一基础镜像（团队统一 base） | 分层共享，节点上只下层差量 |
| 就近镜像仓库/多地域复制（Harbor 复制） | 跨地域拉取提速 |
| 预拉取（节点初始化时 pull 常用镜像） | 部署时无需等待 |
| 按分层推送（BuildKit 并发 + registry cache） | CI 构建时间大幅下降 |
| 可选：lazy pulling（Nydus/eStargz/stargz） | 启动时按需拉取，冷启动更快 |

## 安全与治理

```bash
# 扫描漏洞（Trivy 最常用）
trivy image myapp:1.0
trivy image --severity HIGH,CRITICAL --exit-code 1 myapp:1.0    # CI 中卡住不合格镜像

# 用 docker scout（Docker 官方）
docker scout cves myapp:1.0
docker scout recommendations myapp:1.0

# 校验镜像来源与签名
docker trust inspect --pretty reg/ns/myapp:1.0
cosign verify --key cosign.pub reg/ns/myapp:1.0
```

治理清单：基础镜像定期重建更新 CVE、最小化基础镜像（distroless/alpine）、非 root 运行、只从可信 registry 拉取、给镜像打 SBOM（`docker buildx build --sbom=true`）。
