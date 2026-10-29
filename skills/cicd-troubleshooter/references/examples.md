# 输入输出示例

## 示例 1：GitHub Actions 构建失败

用户输入：
GitHub Actions 的 release workflow 又挂了，报 npm 相关错误，帮我看看

技能输出：

结论：失败发生在依赖安装阶段，根因是 package-lock.json 与 package.json 版本不一致导致依赖解析冲突，与代码无关。

时间线：

| 时间 | 事件 | 级别 | 来源 |
| --- | --- | --- | --- |
| 10:02:11 | checkout 完成 | info | run log |
| 10:02:14 | npm ci 开始 | info | run log |
| 10:02:17 | npm ERR! code ERESOLVE（首个异常点） | error | run log |

失败阶段与证据：

- 阶段：依赖安装（npm ci）
- 首个报错点（已脱敏）：
  > npm ERR! code ERESOLVE
  > npm ERR! Could not resolve dependency: peer vue@^3.4 required, found vue@3.3

根因：

1. package.json 声明了 vue ^3.4，但 lock 文件仍锁定 3.3，npm ci 校验失败（置信度：高）。
2. 缓存中旧 lock 未被失效（置信度：低）。

建议：

1. 在本地执行 npm install 更新 lock 文件并提交（低风险，改动仅依赖锁定）。
2. 如确认是缓存问题，在流水线中对该 job 关闭缓存或 bump 缓存 key（低风险）。
3. 修复后重跑该 workflow 验证（由用户确认后执行）。

待确认项：lock 文件最近是否被手动编辑过。

## 示例 2：应用日志定位 OOM

用户输入：
服务每隔两小时就重启一次，日志里看不出问题，帮我分析

技能输出：

结论：服务重启由内存耗尽触发，日志中 OOM Killer 记录与重启时间点吻合，根因指向 JVM 堆配置超出容器内存上限。

时间线：

| 时间 | 事件 | 级别 | 来源 |
| --- | --- | --- | --- |
| 12:00:00 | 容器启动 | info | docker logs |
| 13:58:12 | java 进程内存持续增长 | warn | 监控 |
| 13:59:40 | Out of memory: Killed process 1234 (java)（首个异常点） | error | journalctl -k |
| 13:59:41 | 容器退出（exit code 137） | error | docker logs |

失败阶段与证据：

- 阶段：运行期资源
- 关键日志（已脱敏）：
  > journalctl -k: Out of memory: Killed process 1234 (java) total-vm:4.2GB
  > docker logs: Container restarting (exit code 137)

根因：

1. JVM 最大堆 -Xmx 超过容器 memory limit，触发内核 OOM（置信度：高）。
2. 应用内存泄漏，随时间累积触顶（置信度：中）。

建议：

1. 对照容器 limit 调低 -Xmx，并设置 -XX:MaxRAMPercentage 让 JVM 自适应（低风险，需重启生效，由用户确认）。
2. 增加堆外内存与线程栈预估，预留 25% 余量。
3. 观察重启周期是否仍为两小时，用于区分泄漏与配置问题。

待确认项：容器 memory limit 具体数值；应用是否近期改过 JVM 参数。

## 示例 3：CrashLoopBackOff（K8s 部署）

用户输入：
K8s 里 myapp 一直 CrashLoopBackOff，describe 看不出明显问题，帮我查

技能输出：

结论：Pod 崩溃由启动阶段连接配置中心超时导致，前一个实例日志显示依赖服务未就绪即启动；回滚历史确认该现象始于最近一次镜像升级。

时间线：

| 时间 | 事件 | 级别 | 来源 |
| --- | --- | --- | --- |
| 14:00:05 | 新镜像 v1.2.0 发布，滚动更新开始 | info | rollout status |
| 14:00:11 | 新 Pod 启动（首个异常点） | warn | kubectl get events |
| 14:00:12 | Liveness 探针首次失败 | warn | kubectl describe |
| 14:00:15 | 容器重启（第 3 次） | error | kubectl logs -p |

失败阶段与证据：

- 阶段：部署（CrashLoopBackOff）
- 前一个实例日志（已脱敏）：
  > kubectl logs myapp-7d8f9 -p: Failed to connect to config-center:5432 timeout after 10s (context deadline exceeded)
- 事件：
  > kubectl get events --sort-by=.lastTimestamp: Back-off restarting failed container
- 回滚历史：
  > kubectl rollout history deployment/myapp: REVISION 7 = v1.2.0（当前），REVISION 6 = v1.1.9（上次正常）

根因：

1. v1.2.0 启动即连接配置中心超时，探针失败触发重启，与配置中心地址或初始化顺序变更有关（置信度：高）。
2. 依赖服务（config-center）当时未就绪或网络策略变更（置信度：中）。

建议：

1. 回滚到 REVISION 6（v1.1.9）恢复服务（中风险，由用户确认后执行 kubectl rollout undo）。
2. 对比 v1.1.9 与 v1.2.0 的配置中心地址与启动参数差异（低风险只读）。
3. 若确认是启动顺序问题，增加 initContainer 等待依赖就绪。

待确认项：v1.2.0 的镜像变更内容；config-center 在 14:00 前后的可用状态。
