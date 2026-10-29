# Nginx 领域知识

分析 Nginx 日志（access.log / error.log）时，对照本文件快速定位根因。识别特征：日志含 `upstream`、`worker_connections`、`connect() failed`、`GET /xxx` 等。

## 1. 错误日志（error.log）常见报错

| 报错特征 | 含义 | 根因 | 修复方案 |
|----------|------|------|----------|
| `connect() failed (111: Connection refused) while connecting to upstream` | 连不上上游 | 上游服务没启动 / 端口不对 / 防火墙 | 启动上游服务、核对 upstream 配置端口、放行防火墙 |
| `upstream timed out (110: Connection timed out)` | 上游响应超时 | 上游处理慢 / `proxy_read_timeout` 太短 | 调大 `proxy_read_timeout`、优化上游响应速度 |
| `no live upstreams while connecting to upstream` | 所有上游都不可用 | 上游全部宕机 / 健康检查全失败 | 检查上游集群状态、排查为何全部下线 |
| `worker_connections are not enough` | 连接数超限 | `worker_connections` 配太小 | 调大 `worker_connections`，并核对 `worker_processes` |
| `open() ... failed (13: Permission denied)` | 文件权限不足 | nginx 用户无读取权限 | 修正文件/目录属主与权限 |
| `bind() to 0.0.0.0:80 failed (98: Address already in use)` | 端口占用 | 其他进程占用了端口 | 找到占用进程（`ss -tlnp`）并处理 |
| `too many open files` | 句柄耗尽 | 系统/进程 fd 限制过小 | 调大 `ulimit -n` 与 `worker_rlimit_nofile` |

## 2. 作为反向代理时的 HTTP 状态码

| 状态码 | 含义 | 根因 | 修复方案 |
|--------|------|------|----------|
| **502 Bad Gateway** | 上游返回无效响应 | 上游崩溃 / 返回了无法解析的响应 | 查上游进程与日志，确认其正常返回 |
| **504 Gateway Timeout** | 上游超时 | 上游慢 / `proxy_read_timeout` 太短 | 调大超时或优化上游 |
| **499** | 客户端主动断开 | 客户端等待超时（接口太慢） | 优化接口响应速度 |
| 大量 **4xx**（400/404/403） | 请求本身有问题 | 客户端错误 / 路由未配置 | 查请求 URL 与路由规则 |
| 大量 **5xx** | 服务端问题 | 上游故障 / 配置错误 | 优先看 502/504 对应的上游 |

## 3. access.log 分析要点

- 默认格式：`$remote_addr - [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"`；
- **慢请求定位**：`$request_time`（若开启）高的请求是性能瓶颈；
- **高频 5xx 的接口**：按 `$request` 分组统计，找出最常出错的接口；
- **异常来源 IP**：`$remote_addr` 高频且 4xx/5xx 的 IP 可能是攻击或爬虫。

## 4. 噪音日志（可忽略，不算异常）

- 健康检查请求：`GET /health`、`GET /ping`、`HEAD /`（常见于 LB/监控探测）；
- 静态资源 304：`GET /static/xxx 304`（缓存未变，正常）；
- 常见爬虫 UA：`Googlebot`、`bingbot` 等抓取（低频时忽略）。

## 5. 因果链提示

- `connect() refused` 常与上游的 `no live upstreams` 先后出现 → 同一根因「上游宕机」；
- `upstream timed out` 与客户端侧 504/499 同时出现 → 根因「上游慢」；
- 大量 502 突然出现 → 优先查「是否刚发布/重启了上游」。
