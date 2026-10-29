# 性能与内核原理

面向“大佬”的深度内容：讲清容器隔离的内核机制，以及生产调优的实测依据。

## 内核三大基石

### 1. Namespace —— 隔离“看得见什么”

| namespace | 隔离对象 | 引入版本 |
|---|---|---|
| mnt | 挂载点 | 2.4.19 |
| pid | 进程号（容器内 PID 1 是独立视图） | 2.6.24 |
| net | 网络栈（网卡/路由/iptables/端口） | 2.6.29 |
| uts | 主机名/域名 | 2.6.19 |
| ipc | 信号量/消息队列/共享内存 | 2.6.19 |
| user | 用户/UID 映射（rootless 的基础） | 3.8 |
| cgroup | cgroup 视图隔离 | 4.6 |
| time | 时钟偏移 | 5.6 |

```bash
# 查看进程的 namespace（面试/排障常问）
ls -l /proc/$(docker inspect -f '{{.State.Pid}}' <容器>)/ns/
# 进入容器的网络/挂载命名空间（无需进容器即可操作）
sudo nsenter -t $PID -n -m -u -i -p -- /bin/sh
```

### 2. cgroup —— 限制“能用多少”

cgroup v1（多层级树，各子系统独立）vs v2（统一层级，`/sys/fs/cgroup/`）。现代发行版（Ubuntu 22.04+、RHEL 9+）默认 v2，Docker 20.10+ 完整支持。

```bash
# 查容器实际 cgroup 限制（v2 路径形态）
cat /sys/fs/cgroup/system.slice/docker-<容器ID>.scope/memory.max
cat /sys/fs/cgroup/system.slice/docker-<容器ID>.scope/cpu.max
docker stats --no-stream                      # 快速看 CPU/MEM/NET/IO
docker inspect -f '{{.HostConfig.Memory}}' <容器>   # 字节数
```

关键机制：**memory 超限触发 cgroup OOM Killer**，杀掉容器内占用最大的进程 → 退出码 137。`--oom-kill-disable` 危险（会卡死），生产上应正确设限并结合告警。

### 3. UnionFS —— 分层文件系统

overlay2 结构：`lowerdir`（只读镜像层）+ `upperdir`（可写层）+ `merged`（统一视图）。文件修改走 Copy-up：修改只读层大文件时先整份复制到 upperdir —— **这是容器内写大文件慢的根本原因**，也是数据库必须挂卷的原因。

```bash
mount | grep overlay2 | head -3      # 查看某容器挂载参数（含各层路径）
```

## 资源限制的正确姿势

```bash
docker run -d \
  --memory 1g --memory-swap 1g \      # swap 与 memory 相等 = 禁用 swap；不设则默认 2x
  --memory-reservation 512m \         # 软限制，内存压力时才强制
  --cpus 1.5 \                        # = --cpu-period 100000 --cpu-quota 150000
  --cpu-shares 512 \                  # 相对权重，仅在 CPU 竞争时生效（默认 1024）
  --blkio-weight 300 \                # IO 相对权重
  --pids-limit 200 \                  # 防 fork 炸弹
  --ulimit nofile=65535:65535 \       # 句柄数，高并发服务必调
  myapp:1.0
```

**内存限制的经验值**：JVM 容器需显式设 `-XX:MaxRAMPercentage=75`（旧版 JVM 读不到 cgroup 限制会按宿主机内存分配堆 → 被 OOM Kill）；Go/Node 注意 GC 与堆外内存，`--memory` 要留 20% 余量给页缓存/线程栈。

## 性能对比参考（同机基准，量级参考）

| 项目 | 相对开销 | 说明 |
|---|---|---|
| 容器启动 | 毫秒级 | 只是 fork+exec + 挂载，无内核启动 |
| CPU 计算 | ≈ 裸机 | host 网络+无限制下差异 <2% |
| 网络（bridge+NAT） | 有损耗 | 小包场景吞吐可降 10~30%，**host 网络可基本消除** |
| 网络（host） | ≈ 裸机 | 无 NAT/网桥转发，适合高吞吐场景 |
| 磁盘（容器可写层） | 差 | COW 复制开销，写大文件可能慢数倍 → **用卷** |
| 磁盘（卷/bind） | ≈ 裸机 | 绕过 unionfs，接近原生 |
| 内存 | 无额外损耗 | 与普通进程一致 |

**结论**：容器慢，90% 是配置问题（NAT 网络、可用写层写数据、未限制资源导致互相干扰），不是容器本身。

## 调优清单（生产）

1. 网络：高吞吐用 `--network host`，或调大 `net.core.somaxconn`、`net.ipv4.tcp_tw_reuse`
2. IO：数据目录一律挂卷；数据库用宿主机本地 SSD/NVMe + 数据卷
3. 日志：务必设 `max-size/max-file`，json-file 大日志会拖慢 IO 并吃满磁盘
4. 限制：所有容器设 `--memory`/`--cpus`，避免单容器拖垮整机
5. 镜像：非 root、精简基础镜像、多阶段构建（减少层数与体积 → 拉起更快）
6. 密度：结合 `docker stats`、`cAdvisor`+Prometheus 观测，按 P95 而非均值定容量
7. 内核：`sysctl` 调 `fs.file-max`、`vm.swappiness=1`（数据库场景）、`vm.overcommit_memory`
8. 冷启动：用 stargz/Nydus 懒加载，或节点预拉镜像

## rootless 与 daemon 架构（进阶）

- **dockerd（root）** → **containerd** → **runc**（创建容器）→ 容器进程。`docker info` 里可见 `runc` 版本
- 用户 `docker ps` 走的是 `/var/run/docker.sock`（root 权限的 Unix socket）→ 挂载该 socket 进容器等价于把 root 交给容器，**切勿在生产把 socket 挂进业务容器**
- **rootless**：dockerd/containerd 以普通用户运行，靠 user namespace + slirp4netns（网络）+ fuse-overlayfs（存储），牺牲部分性能换安全，多租户/CI 场景推荐
- K8s 场景：可直接用 containerd（dockershim 已移除），镜像格式与 Docker 兼容，命令换成 `crictl`/`nerdctl`
