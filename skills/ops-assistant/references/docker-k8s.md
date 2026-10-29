# 容器与 K8s 运维

> 本文聚焦**运维视角**的容器排障与日常操作。Docker 原理与编排细节见 docker-assistant 技能。

## Docker 日常运维

### 磁盘清理（容器机器最常做的事）

```bash
# ✅ 看空间占用
docker system df

# ⚠️ 清理三板斧（按激进程度排序）
docker container prune          # 清已停止容器
docker image prune -a           # 清无引用镜像（含未打标签的中间层）
docker system prune -a --volumes  # 🚫 全清：先确认没有"重要的临时容器"

# 日志失控（容器 json 日志不轮转能吃光磁盘）
du -sh /var/lib/docker/containers/*/*.log | sort -rh | head
# 根治：/etc/docker/daemon.json
# { "log-driver": "json-file", "log-opts": { "max-size": "100m", "max-file": "3" } }
```

### 容器排障速查

```bash
docker ps -a                          # Exited 状态的容器
docker logs --tail 100 -f <c>         # 看日志（--since 10m 也常用）
docker inspect <c> | grep -A5 OOM     # 是不是被 OOM 杀的
docker exec -it <c> sh                # 进去看（镜像没 sh 就用 debug 镜像）
docker stats --no-stream              # 资源占用一览
docker diff <c>                       # 容器内文件改了什么
```

## K8s 排障（按故障频率排序）

### Pod Pending（起不来）

```bash
kubectl describe pod <pod> -n <ns>    # Events 部分就是答案
```

| Events 关键词 | 根因 | 解法 |
|---|---|---|
| `Insufficient cpu/memory` | 节点资源不够 | 加节点/降 requests/驱逐闲 Pod |
| `node(s) had taint` | 节点有污点没容忍 | 检查 taint/tolerations |
| `FailedScheduling` + pvc | 存储卷没就绪 | `kubectl describe pvc` |
| `nodes are available` 但不调度 | nodeSelector/亲和性不满足 | 检查标签 |

### CrashLoopBackOff（反复重启）

```bash
# ① 看上一次死亡的日志（关键：--previous）
kubectl logs <pod> --previous -n <ns>

# ② 看退出码
kubectl describe pod <pod> | grep -A3 "Last State"
# Exit 1   → 应用自己报错退出（看日志）
# Exit 137 → SIGKILL，多半是 OOMKilled（看 limits）
# Exit 143 → SIGTERM，被正常终止（查谁在删它）
```

### OOMKilled

```bash
kubectl describe pod <pod> | grep -i oom
# 解法：
# 1. limits.memory 调大（治标）
# 2. Java 应用：-Xmx 必须 < limits（留堆外内存），容器感知参数 -XX:MaxRAMPercentage=75.0
# 3. 真正的泄漏 → 抓 heap dump 分析（治本）
```

### ImagePullBackOff

```bash
kubectl describe pod <pod> | grep -A5 Events
# 常见：镜像名/tag 打错、私有仓库没配 imagePullSecrets、仓库证书问题、网络不通
# 验证：到节点上手动 docker pull 试
```

### 服务不通（Service 层排障链）

```bash
# 自内向外四层验证（✅ 每层独立确认）
# 1. Pod 本身通不通
kubectl exec -it <pod> -- curl localhost:8080/healthz

# 2. Service 有没有选中 Pod（最常见坑：selector 和 Pod 标签不匹配）
kubectl get endpoints <svc> -n <ns>     # 空列表 = 没选中任何 Pod
kubectl get pod --show-labels           # 对比 selector

# 3. Service 层通不通（集群内）
kubectl run tmp --rm -it --image=busybox -- wget -qO- <svc>:<port>

# 4. Ingress 层
kubectl describe ingress <ing> -n <ns>
curl -v -H "Host: api.example.com" http://<ingress-ip>/healthz
```

## 资源配额（不配的集群迟早爆）

```yaml
resources:
  requests:           # 调度依据：保证能拿到这么多
    cpu: 250m         # 0.25 核
    memory: 512Mi
  limits:             # 上限：超了 CPU 被节流，内存被 OOMKill
    cpu: 500m
    memory: 1Gi
```

**原则**：
- requests 按**平常用量**定（影响调度密度），limits 按**峰值**定
- CPU 超 limits 是节流（throttle，变慢不死）；内存超了直接 OOMKill——**内存 limits 留足余量**
- Java 应用内存 = 堆(-Xmx) + 元空间 + 线程栈 + 直接内存 + JVM 自身，Xmx 设 limits 的 50-70%

```bash
# 看真实用量（需 metrics-server）
kubectl top pod -n <ns> --sort-by=memory
kubectl top node

# 看 CPU 节流（被限了多少次）
kubectl exec <pod> -- cat /sys/fs/cgroup/cpu.stat 2>/dev/null | grep throttled
```

## 日常操作速查

```bash
# 进容器
kubectl exec -it <pod> -n <ns> -- sh
# 多容器 Pod 指定容器：-c <container-name>

# 拷文件
kubectl cp <ns>/<pod>:/tmp/heap.dump ./heap.dump

# 端口转发（本地直连集群内服务，调试神器）
kubectl port-forward svc/myapp 8080:80 -n <ns>

# 滚动更新与回滚
kubectl set image deploy/myapp myapp=myapp:v2.1 -n <ns>
kubectl rollout status deploy/myapp -n <ns>
kubectl rollout history deploy/myapp -n <ns>
kubectl rollout undo deploy/myapp -n <ns> --to-revision=3

# 节点维护（先驱逐再干活，干完恢复）
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
kubectl uncordon <node>

# 强制删卡死的 Pod（🚫 最后手段：可能是存储卷没释放，强删会双挂）
kubectl delete pod <pod> --grace-period=0 --force -n <ns>
```

## K8s 运维三原则

1. **永远先 `describe` 再动手**——Events 就是 K8s 的"遗言"
2. **Deployment 管无状态，StatefulSet 管有状态，别混**
3. **生产必配**：requests/limits + readinessProbe + livenessProbe + PodDisruptionBudget
