# 安全加固

## 安全模型前提

**容器隔离强度低于虚拟机**：共享内核，内核漏洞可能逃逸；`docker` 组成员等价 root。因此安全策略不是“信任容器”，而是**纵深防御 + 最小权限**。

## 加固清单（按收益排序）

### 1. 非 root 运行

```dockerfile
RUN addgroup -S app && adduser -S app -G app
USER app
```

```bash
docker run --user 1000:1000 myapp:1.0      # 运行时指定（镜像没设 USER 时兜底）
```

### 2. 只读根文件系统 + 最小可写

```bash
docker run --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v appdata:/app/data \
  myapp:1.0
```

### 3. 收紧 capabilities（默认给 14 个，多数应用用不到）

```bash
docker run --cap-drop=ALL --cap-add=NET_BIND_SERVICE myapp:1.0
```

常用需显式添加的：`NET_BIND_SERVICE`（绑定 1024 以下端口）、`SYS_TIME`、`SYS_PTRACE`（调试用，生产移除）。

### 4. 禁用权限提升

```bash
docker run --security-opt=no-new-privileges:true myapp:1.0
```

阻断 setuid/setgid 提权路径。

### 5. seccomp / AppArmor 限制系统调用

Docker 默认启用 seccomp 白名单（禁用约 44 个危险调用）。自定义更严策略：

```bash
docker run --security-opt seccomp=/path/to/profile.json myapp:1.0
docker run --security-opt seccomp=unconfined myapp:1.0     # ⚠️ 关闭沙箱，勿用于生产
docker run --security-opt apparmor=docker-default myapp:1.0
```

### 6. 资源限制（防 DoS）

```bash
--memory 512m --cpus 1 --pids-limit 100 --ulimit nofile=1024:1024
```

### 7. 网络最小暴露

- 只映射必要端口，尽量绑 `127.0.0.1:8080:80` 由反向代理对外
- 用自定义网络隔离，不同应用不共用网络
- 不需要网络的容器用 `--network none`

## 镜像安全

```bash
trivy image --severity HIGH,CRITICAL myapp:1.0          # 漏洞扫描
docker scout cves myapp:1.0
docker history --no-trunc myapp:1.0                     # 检查有没有把密钥写进层里
```

要点：
- **绝不把密码/密钥 COPY 或 ENV 进镜像**：即使后续 `RUN rm` 删除，历史层里依然可提取。用运行时注入（env-file/secrets/vault）
- 基础镜像选固定 digest：`FROM node:20-alpine@sha256:...`（防 tag 漂移与投毒）
- 定期重建（每月至少一次）以吸收上游 CVE 修复
- 开 BuildKit SBOM/attestation 便于审计追溯

## 宿主与 daemon 加固

`/etc/docker/daemon.json` 关键项：

```json
{
  "icc": false,                                  // 禁止容器间默认互通
  "userns-remap": "default",                     // 用户命名空间隔离（注意与卷权限的配合）
  "no-new-privileges": true,
  "live-restore": true,                          // daemon 重启容器不中断
  "log-driver": "json-file",
  "log-opts": { "max-size": "100m", "max-file": "3" },
  "tls": true,
  "tlscacert": "/etc/docker/ca.pem",             // 远程 API 必须配 TLS
  "tlscert": "/etc/docker/server-cert.pem",
  "tlskey": "/etc/docker/server-key.pem",
  "hosts": ["unix:///var/run/docker.sock", "tcp://0.0.0.0:2376"]
}
```

**红线**：

1. `/var/run/docker.sock` 是 root 等价物，**不要挂进任何业务容器**；需要容器管理能力用受限代理（如 socket-proxy）
2. 不要暴露 `tcp://0.0.0.0:2375`（无 TLS 的远程 API = 裸奔的 root shell）
3. 不要把宿主机 `/`、`/etc`、`/proc` 挂进容器
4. `--privileged` 几乎等于关闭所有隔离，能不用就不用；需要特定能力用 `--cap-add`

## rootless 模式

```bash
# Ubuntu 依赖
sudo apt install -y uidmap dbus-user-session
curl -fsSL https://get.docker.com/rootless | sh
export PATH=/home/$USER/bin:$PATH
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
systemctl --user enable --now docker
```

特点：daemon 以普通用户运行，`unix:///run/user/UID/docker.sock`；网络走 slirp4netns（性能略降，端口 <1024 不可绑定）；存储走 fuse-overlayfs。**适合 CI runner、多租户构建、不信任的工作负载。**

## 运行时安全监控

- `docker events` 订阅容器生命周期事件；`--filter type=container,event=die`
- 接入 Falco / Tetragon 做运行时威胁检测（异常 exec、敏感文件读取、反弹 shell）
- 定期审计：`docker ps` 有无异常容器、`docker inspect` 检查 `Privileged`/`CapAdd`/挂载源
- 所有镜像来源白名单化（私有 Harbor + 复制策略），禁止直接拉公网镜像部署生产
