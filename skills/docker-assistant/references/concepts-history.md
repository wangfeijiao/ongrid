# 容器概念、演进史与安装

## 一句话理解容器

**容器 = 被内核“圈起来”的普通进程**。它没有虚拟硬件，只是用 Linux 内核的三个能力把进程隔离起来：namespace（看不见别的进程/网络/文件）、cgroup（限制能用多少 CPU/内存）、unionfs（叠加出一个独立文件系统）。所以容器启动是毫秒级，而虚拟机要启动整个操作系统，是分钟级。

生活类比：虚拟机是**独立别墅**（有自己地基水电），容器是**公寓隔间**（共用大楼水电管道，但各有门牌互不干扰）。

## 与虚拟机的对比

| 维度 | 容器 | 虚拟机 |
|---|---|---|
| 隔离级别 | 进程级（共享内核） | 硬件级（独立内核） |
| 启动速度 | 毫秒~秒 | 数十秒~分钟 |
| 资源开销 | MB 级、几乎无损耗 | GB 级、有虚拟化损耗 |
| 隔离强度 | 较弱（内核漏洞可逃逸） | 强 |
| 密度 | 单机可跑上千 | 单机几十 |
| 适用 | 微服务、CI/CD、弹性扩缩 | 强隔离、异构 OS |

结论：**多租户强隔离选虚拟机，服务交付与弹性选容器**；生产上常见组合是“虚拟机/裸金属上跑容器”。

## 演进史（为什么是现在这个样子）

1. **1979 chroot**：最早的文件系统隔离，只换了根目录，无进程/网络隔离
2. **2000s Namespaces + cgroups**：Linux 内核逐步补齐隔离（2002 mount ns、2008 PID/net ns、cgroup v1）
3. **2008 LXC**：第一个较完整的“系统容器”，但难用、生态弱
4. **2013 Docker**：dotCloud 开源。杀手锏不是隔离技术，而是**镜像分层 + 分发 + 一行命令跑起来**的开发者体验
5. **2015 OCI 成立**：制定镜像格式（image-spec）与运行时（runtime-spec）标准，避免生态碎片化
6. **2017 containerd / Moby 拆分**：Docker 把运行时抽成 containerd 捐给 CNCF，Docker 公司聚焦 Moby 项目与开发者工具
7. **现在**：K8s 默认用 containerd/CRI-O，Docker 的 CLI 与构建（BuildKit）仍是最流行的开发工具

要点：**Docker ≠ 容器技术本身**。容器是内核能力，Docker 是一套好用的产品化封装。理解这点就能明白为什么 K8s 可以不用 Docker 也照样跑容器。

## 安装

### Ubuntu/Debian（官方源，别用 apt 里的旧版 docker.io）

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

### RHEL 系

```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### 免 sudo 与开机自启

```bash
sudo usermod -aG docker $USER    # 加入 docker 组（需重新登录生效）
newgrp docker                    # 不想重新登录可临时生效
sudo systemctl enable --now docker
docker run hello-world           # 验证安装
```

⚠️ 安全提醒：加入 `docker` 组等价于获得 root 权限（可挂载宿主机根目录）。生产环境更推荐 **rootless 模式**，见 @references/security-hardening.md。

### 国内镜像加速（拉不动镜像时）

`/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": ["https://docker.m.daocloud.io"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "100m", "max-file": "3" }
}
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart docker
```

## 核心概念速查

| 概念 | 类比 | 说明 |
|---|---|---|
| 镜像 Image | 光盘/安装包（只读） | 分层只读模板，由 Dockerfile 构建 |
| 容器 Container | 用光盘开机的系统 | 镜像 + 一层可写层 + 运行时进程 |
| 仓库 Registry | 应用商店 | 存放分发镜像（Docker Hub/Harbor/云厂商） |
| 数据卷 Volume | 外接硬盘 | 绕过 unionfs，持久化且高性能 |
| 网络 Network | 插线板 | 容器间通信的虚拟网络 |
| Dockerfile | 菜谱 | 声明式描述如何构建镜像 |
