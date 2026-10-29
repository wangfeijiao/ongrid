# CI/CD 排障与日志分析：只读命令清单与失败模式速查

> 全部命令以只读为原则。工具未安装或未登录时跳过该项并标注，不尝试安装。
> 排障第一动作：先确定失败发生的时间窗（失败时刻前后各取 5-10 分钟），所有日志读取限定在该时间窗内，避免被无关噪音干扰。

## 1. 流水线日志获取

| 平台 | 命令 / 方式 |
| --- | --- |
| GitHub Actions | gh run view --log-failed <run-id>；gh run list --workflow <name> |
| GitLab CI | 流水线 Job 日志页；glab ci status --live（如已装 glab） |
| Jenkins | 控制台日志 URL / 工作区 consoleText 文件 |
| 通用 | 让用户提供失败 Job 的日志链接或粘贴报错尾部 100 行 |

## 2. 应用与系统日志（只读）

| 目标 | 命令 |
| --- | --- |
| 系统日志尾部 | journalctl -n 100 --no-pager |
| 指定时间窗 | journalctl --since "10 min ago" --until now --no-pager |
| 错误级别过滤 | journalctl -p err -n 50 --no-pager |
| 容器日志 | docker logs --tail 100 <container> |
| K8s Pod 日志 | kubectl logs --tail=100 <pod> -n <ns> |
| K8s 前一个实例日志（CrashLoop 必查） | kubectl logs <pod> -p --tail=100 -n <ns> |
| Pod 事件（按时间排序） | kubectl get events --sort-by=.lastTimestamp -n <ns> |
| Pod 详情（状态与探针） | kubectl describe pod <pod> -n <ns> |
| Deployment 回滚历史 | kubectl rollout history deployment/<name> -n <ns> |
| Deployment 当前状态 | kubectl rollout status deployment/<name> -n <ns> |
| Trace ID / 请求 ID 串联 | grep <trace_id> /var/log/app.log；journalctl | grep <trace_id>；kubectl logs <pod> -n <ns> | grep <trace_id> |
| 追踪实时输出（需用户同意） | tail -f /var/log/app.log |

## 3. 资源与进程（定位 OOM / 资源不足）

| 目标 | 命令 |
| --- | --- |
| 内存与 swap | free -h |
| 磁盘 | df -hT |
| CPU 负载 | uptime |
| 进程状态 | ps aux --sort=-%mem | head -n 8 |
| OOM 记录 | journalctl -k | grep -i oom | tail -n 20 |

## 4. 常见失败模式速查表

| 失败阶段 | 典型报错关键字 | 常见根因 |
| --- | --- | --- |
| checkout | could not resolve host / Authentication failed | 仓库地址错误、网络、凭据失效 |
| 依赖安装 | E: Unable to locate package / No matching version | 源不可达、版本号错误、锁文件过期 |
| 依赖安装 | npm ERR! code EINTEGRITY / checksum | 缓存损坏，清缓存重装 |
| 编译构建 | error: failed to compile / build failed | 语法错误、依赖版本不兼容、环境差异 |
| 测试 | FAILED / AssertionError / Timeout | 断言失败、flaky、资源不足 |
| 镜像构建 | failed to solve / no space left | Dockerfile 错误、磁盘满、基础镜像不可达 |
| 镜像推送 | denied / unauthorized / manifest unknown | 仓库认证、权限、镜像名错误 |
| 部署 | ImagePullBackOff / ErrImagePull | 镜像不存在、tag 错误、私有仓库未配置 secret |
| 部署 | CrashLoopBackOff | 启动命令错误、配置缺失、依赖服务未就绪 |
| 部署 | forbidden / Operation not allowed | RBAC 权限不足 |
| 部署 | revision changed / rollout paused | 发布被中断或策略卡住，查 rollout history |
| 通知 | webhook failed / 401 | webhook 地址或 secret 失效 |

## 5. 日志时间线还原法（核心方法论）

1. 确定时间窗：取失败发生时刻，前后各 5-10 分钟。
2. 收集该时间窗内全部日志（应用、系统、容器、事件）。
3. 按时间顺序映射事件，标出首个异常点（不是最后一个报错，后续报错常是连锁反应）。
4. 有 Trace ID / 请求 ID 时，用它串联跨服务日志，还原完整调用链。
5. 检查日志时区与服务器时区是否一致，不一致时先换算再对齐时间线。

## 6. 判断原则

- 以第一个报错点为起点，而不是最后一个报错（后续报错常是连锁反应）。
- 同一报错反复出现且时间规律，优先怀疑缓存或幂等问题。
- 本地能过、流水线失败，优先怀疑环境差异（版本、时区、语言环境、权限）。
- 测试时好时坏：同一 job 重跑对比输出差异，差异大优先怀疑 flaky 与资源争用。
- CrashLoopBackOff：必查 `kubectl logs -p`（前一个实例的退出日志），describe 看探针与事件。
- 部署回滚：查 rollout history 对比最近两次镜像/配置变更，确认触发回滚的提交。
- 日志中涉及密钥、token、内网地址的片段默认脱敏后再输出。
