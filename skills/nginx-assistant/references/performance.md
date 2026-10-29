# 性能调优

> Nginx 默认配置能扛几千并发；到几万、几十万连接要系统+内核+Nginx 三层配合。

## Nginx 层配置

```nginx
# ===== main 上下文 =====
worker_processes auto;            # 自动匹配 CPU 核数（别写死，容器里也正确识别）
worker_rlimit_nofile 100000;      # worker 进程文件句柄上限（配合系统 ulimit）

# ===== events 上下文 =====
events {
    use epoll;                    # Linux 自动选择（写不写都行，macOS 是 kqueue）
    worker_connections 8192;      # 单 worker 最大连接数（默认 1024——高并发必调）
    multi_accept on;              # 一次 accept 多个连接（突发流量友好）
}

# ===== http 上下文 =====
http {
    sendfile on;                  # 静态文件零拷贝（默认 on）
    tcp_nopush on;                # sendfile 时攒包（默认 on）
    tcp_nodelay on;               # keepalive 连接立即发送（默认 on）

    # keepalive 超时（浏览器长连接保持时间）
    keepalive_timeout 65s;        # 默认 75s；纯 API 网关可降到 30s 释放连接
    keepalive_requests 1000;      # 单连接最多处理 1000 个请求后关（默认 1000）
                                  # 防 conntrack 老化与内存碎片

    # 客户端缓冲
    client_body_buffer_size 128k;      # 请求体内存缓冲（超过才写临时文件）
    client_header_buffer_size 4k;
    large_client_header_buffers 8 16k;

    # 输出缓冲
    output_buffers 2 32k;
}
```

**连接数上限计算**：`worker_processes × worker_connections`——但注意每个客户端连接可能消耗 2 个连接（客户端侧 + upstream 侧），保守按 1.5 倍估。

## 系统层（Linux sysctl，配套必改）

```bash
# /etc/sysctl.d/99-nginx.conf
# 文件句柄（worker_rlimit_nofile 要求系统允许）
fs.file-max = 2097152

# 连接队列（高并发必调，默认 128 太小）
net.core.somaxconn = 65535

# TIME_WAIT 复用（Nginx 作为反向代理时，自己也是客户端）
net.ipv4.tcp_tw_reuse = 1

# 本机发起连接的端口范围（后端数量多时）
net.ipv4.ip_local_port_range = 1024 65535

# 读缓冲按需增长（内存足够时）
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# 生效：sysctl --system

# systemd 服务的 LimitNOFILE 也要改（Nginx 的 rlimit 不能超过它）
# /etc/systemd/system/nginx.service.d/limits.conf:
# [Service]
# LimitNOFILE=100000
```

## CPU 绑核（极端优化，一般不需要）

```nginx
worker_cpu_affinity auto;         # 1.10+ 自动绑核（减少缓存失效）
```

## 压测方法（调优前后必须量化）

```bash
# autocannon（Node 生态，HTTP/1.1 表现好）
npx autocannon -c 500 -d 30 http://target/

# wrk（经典，支持 Lua 脚本）
wrk -t8 -c500 -d30s http://target/

# wrk2（恒定吞吐，测长尾延迟）
# 观察指标：RPS、P99（别只看平均！）、错误数、CPU、内存
```

**压测注意**：
1. 宎客户端机器先排除自身瓶颈（CPU/端口/文件句柄）
2. `Cannot create socket: Too many open files` → 压测机 `ulimit -n 65535`
3. 本机压本机（loopback）数字虚高，仅供相对比较

## 性能瓶颈分层定位

```
QPS 上不去，看哪里：
1. worker CPU 打满？ → CPU 瓶颈：Gzip 压缩级别调低/开 SSL 硬件加速/上更多机器
2. CPU 闲但 QPS 低？ → 等待型瓶颈：后端慢（看 urt）/连接数不够（worker_connections）
3. 内存涨？ → 连接数 × 缓冲区大小；access 日志缓冲
4. error.log 大量 "too many open files" → ulimit/rlimit
5. TIME_WAIT 巨量 → upstream keepalive 没开
```

## 常见场景参数速查

| 场景 | 关键配置 |
|---|---|
| 纯静态资源站 | sendfile on + expires 长缓存 + access_log off |
| API 网关（长连接后端） | upstream keepalive + worker_connections 8192 + keepalive_timeout 30s |
| 大文件下载 | sendfile + tcp_nopush + limit_rate 限速 |
| 高并发短连接 | somaxconn + multi_accept + reuseport（多 listener 分片） |
| SSL 大流量 | ssl_session_cache + session tickets + 少 cipher |
| SSE/长轮询 | proxy_buffering off + keepalive_timeout 长 + worker_connections 大 |

```nginx
# reuseport（1.9.1+）：多个 worker 各自 accept，锁竞争消除（大流量收益明显）
listen 80 reuseport;
```
