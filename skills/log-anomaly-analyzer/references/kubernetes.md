# Kubernetes 领域知识

分析 Kubernetes 相关日志（kubectl events、Pod 日志、kubelet 日志）时对照本文件。识别特征：`OOMKilled`、`CrashLoopBackOff`、`ImagePullBackOff`、`FailedScheduling` 等事件关键字，或 Pod 名带随机后缀（如 `nginx-7f8c9d-abc12`）。

## 1. 常见 Pod 事件与根因

| 事件/状态 | 含义 | 根因 | 修复方案 |
|-----------|------|------|----------|
| **OOMKilled** / Exit Code 137 | 容器内存超限被杀 | `limits.memory` 配太小 / 内存泄漏 | 调大 memory limit，或定位并修复内存泄漏 |
| **CrashLoopBackOff** | 容器反复崩溃重启 | 启动即报错（配置/依赖缺失） | 看容器启动日志，修复启动失败原因 |
| **ImagePullBackOff** / ErrImagePull | 镜像拉取失败 | 镜像名错 / 仓库权限 / 网络不通 | 核对镜像名与 tag、配置 imagePullSecret |
| **FailedScheduling** | 无法调度 | 资源不足 / 污点 / 亲和性冲突 | 检查节点资源、污点容忍、亲和规则 |
| **Pending** | 长期等待调度 | 同上，或 PVC 未绑定 | 查 `kubectl describe pod` 的事件 |
| **Evicted** | 被驱逐 | 节点内存/磁盘压力 | 扩容节点或降低资源占用 |
| **Liveness probe failed** | 存活探针失败 | 服务假死 / 探针配置过严 | 检查探针路径与阈值、排查服务假死 |
| **Readiness probe failed** | 就绪探针失败 | 依赖未就绪 / 初始化慢 | 检查依赖、放宽 initialDelaySeconds |
| **Back-off restarting failed container** | 重启失败回退 | 反复崩溃 | 看崩溃日志定位根因 |

## 2. Exit Code 速查

| Exit Code | 含义 | 根因 |
|-----------|------|------|
| **1** | 应用主动退出（错误） | 看应用日志 |
| **137** | SIGKILL（OOM 或手动杀） | 内存超限 |
| **139** | SIGSEGV 段错误 | 空指针/内存访问越界 |
| **143** | SIGTERM（正常终止信号） | 正常停止/优雅关闭 |

## 3. 常见日志模式

- `ERROR: liveness probe failed` 反复出现 → 服务假死或探针过严；
- `Resource exhausted` / `Out of memory` → 内存问题；
- `connection refused` 出现在 Pod 间调用 → 目标 Service/Pod 未就绪；
- `context deadline exceeded`（gRPC）→ 下游超时。

## 4. 噪音日志（可忽略）

- 周期性 `Readiness probe` / `Liveness probe` 的探活记录（正常）；
- kubelet 的常规心跳/同步日志；
- `GET /healthz` 类探活请求。

## 5. 因果链提示

- `FailedScheduling` → `Pending` → 长时间无 Pod 日志 → 根因「资源不足/调度失败」；
- `OOMKilled` → `CrashLoopBackOff` 循环 → 根因「内存超限，启动即 OOM」；
- `ImagePullBackOff` 反复 → 根因「镜像仓库认证或镜像不存在」。
