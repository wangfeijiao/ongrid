# 容器网络

## 五种网络驱动

| 驱动 | 说明 | 适用 |
|---|---|---|
| `bridge` | 默认，NAT 网络，容器各自有 IP，宿主通过端口映射访问 | 单机多容器，最常用 |
| `host` | 容器直接共享宿主机网络栈，无隔离、无 NAT | 追求极限性能；端口冲突风险 |
| `none` | 只有 lo，无网络 | 完全隔离的计算任务 |
| `overlay` | 跨主机虚拟网络（VXLAN） | Swarm/多主机集群 |
| `macvlan` | 容器获得宿主机物理网段的真实 MAC/IP | 需要容器在局域网中“像物理机一样” |

```bash
docker network ls
docker network create --subnet 172.20.0.0/16 mynet
docker network inspect mynet          # 看已接入容器及 IP
docker run -d --network mynet --name api myapi:1.0
docker network connect mynet <容器>    # 运行中动态接入
docker network prune                  # 清理未使用网络
```

## 服务发现（内置 DNS）

**自定义网络上的容器可以用容器名/服务名互相访问**，Docker 内置 DNS（127.0.0.11）负责解析：

```bash
docker network create appnet
docker run -d --network appnet --name db postgres:16-alpine
docker run -d --network appnet --name api -e DB_HOST=db myapi:1.0
# api 容器内：ping db / nc -zv db 5432 → 通
```

⚠️ **默认 bridge 网络不支持按名字解析**（除 legacy link），所以生产务必用自定义网络。

## 端口映射细节

```bash
-p 8080:80                 # 宿主所有网卡 8080 → 容器 80
-p 127.0.0.1:8080:80       # 仅本机可访问（配合反代更安全）
-p 8080:80/udp             # UDP
-p 8080                    # 随机映射宿主端口，用 docker port 查看
-P                         # 映射镜像 EXPOSE 声明的所有端口
```

映射不生效的排查顺序见 @references/container-lifecycle.md（端口写反 → 容器未监听 0.0.0.0 → 宿主机防火墙 → 云安全组）。

## 容器网络排障命令

```bash
ip a                                  # 看 docker0 网桥
docker exec <容器> ip a                # 容器内网卡（eth0 + 172.x.x.x）
docker exec <容器> ip r                # 容器内路由（默认网关是网桥）
docker exec <容器> cat /etc/resolv.conf # DNS 配置（127.0.0.11 = Docker 内置 DNS）
docker exec <容器> ss -tlnp            # 容器内监听端口 ← 判断服务是否真在监听
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' <容器>
```

从宿主机进入容器网络命名空间抓包（高级排障）：

```bash
PID=$(docker inspect -f '{{.State.Pid}}' <容器>)
sudo nsenter -t $PID -n tcpdump -i eth0 -nn port 8080
# 或：sudo ip netns exec <容器netns> ...
```

## 容器访问外部 / 外部访问容器

- **往外**：默认走宿主 NAT（docker0 masquerade），通常开箱可用；不通就查宿主机 `ip_forward`（`sysctl net.ipv4.ip_forward`）和 iptables 的 `DOCKER` 链
- **往里**：必须端口映射（除 host 网络模式）。云服务器还要放行**安全组**——这是最常见的“映射对了但还是连不上”

## 常见问题速判

| 症状 | 原因 |
|---|---|
| 容器内 `ping google.com` 不通但 IP 能通 | DNS 问题，检查 daemon.json 的 dns 配置、宿主机 resolv.conf |
| 两容器互相连不上 | 不在同一网络；或用了 `localhost` 而非服务名 |
| 容器重启后 IP 变了 | bridge 网络动态分配 IP，**永远用名字而非 IP** |
| `address already in use` | 宿主端口被占：`ss -tlnp \| grep <端口>` |
| 映射后外网不通、本机通 | 云安全组未放行 / 端口绑定成了 127.0.0.1 |
| 容器间需要固定 IP | 自定义网络 + `--ip` 指定（仅自定义网络支持） |
