# Nginx 与负载均衡

> Nginx 是运维接触最多的组件：80% 的入口问题能在 Nginx 层定位和解决。

## 核心配置模板

### 反向代理 + 负载均衡（生产基线）

```nginx
upstream backend {
    # 负载均衡算法（默认轮询）
    # least_conn;                  # 最少连接——长连接/请求耗时不均时用
    # ip_hash;                     # 同 IP 固定到一台——会话粘性（不推荐，用 Redis 存会话更好）
    # hash $request_uri consistent; # URL 一致性哈希——缓存服务用

    server 10.0.1.10:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.11:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.12:8080 backup;      # 备用节点：主力全挂才启用
    # slow_start=10s;                  # 恢复节点缓慢放量（商业版）

    keepalive 32;                      # 到后端的长连接池（大幅降低 TIME_WAIT）
}

server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://backend;
        proxy_http_version 1.1;              # 配合 keepalive 必须 1.1
        proxy_set_header Connection "";      # 清空 Connection 头才能复用

        # 传递真实客户端信息（后端拿不到这些就是一肚子委屈）
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # 超时三兄弟（504 排障就看它们）
        proxy_connect_timeout 5s;    # 连后端超时
        proxy_send_timeout 60s;      # 发请求超时
        proxy_read_timeout 60s;      # 等响应超时

        proxy_next_upstream error timeout http_502 http_503;  # 故障自动换下
        proxy_next_upstream_tries 2;
    }
}
```

### HTTPS（2026 年基线配置）

```nginx
server {
    listen 443 ssl;
    server_name example.com;

    ssl_certificate     /etc/nginx/ssl/example.com.pem;
    ssl_certificate_key /etc/nginx/ssl/example.com.key;

    ssl_protocols TLSv1.2 TLSv1.3;            # TLS 1.0/1.1 已淘汰
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;            # TLS 1.3 下由客户端优先
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # HSTS：强制 HTTPS（⚠️ 开启前确认全站 HTTPS 可用，加了 Max-Age 就回不去）
    add_header Strict-Transport-Security "max-age=31536000" always;
}

# HTTP 全跳 HTTPS
server {
    listen 80;
    server_name example.com;
    return 301 https://$host$request_uri;
}
```

证书自动化：`certbot --nginx -d example.com`（Let's Encrypt，90 天自动续期）。

### 限流（防刷/防雪崩的第一道闸门）

```nginx
# ① 按 IP 限请求速率：每秒 10 个，允许突发 20
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

server {
    location /api/ {
        limit_req zone=api burst=20 nodelay;
        # nodelay：突发立即处理但占令牌；不配则排队平滑
        limit_req_status 429;                 # 默认 503，改成更语义化的 429
    }
}

# ② 按连接数限
limit_conn_zone $binary_remote_addr zone=conn:10m;
location /download/ {
    limit_conn conn 10;                       # 单 IP 最多 10 并发（防下载打爆带宽）
    limit_rate_after 10m;                     # 前 10MB 全速
    limit_rate 1m;                            # 之后限速 1MB/s
}
```

## 502/504 排障（最高频问题）

```
502 Bad Gateway = Nginx 连不上后端或后端直接拒绝/崩溃
504 Gateway Timeout = 后端接了但迟迟不响应
```

```bash
# 502 排查（✅）
tail -100 /var/log/nginx/error.log
# 典型错误：
# "connect() failed (111: Connection refused)"   → 后端进程挂了/没监听：ss -tlnp | grep 8080
# "upstream prematurely closed connection"        → 后端崩溃了（查后端日志/是否 OOM）
# "no live upstreams"                             → max_fails 触发，所有节点被标记下线

# 504 排查
# 1. 后端真的慢：查慢查询/线程池/下游依赖
# 2. 超时设置太短：proxy_read_timeout 调大
# 3. 后端队列堆积：keepalive 太小 + 突发流量
```

## 常用运维操作

```bash
# 配置检查（✅ 改配置后必做，再 reload）
nginx -t

# 平滑重载（✅ 不掉连接）
nginx -s reload   # 或 systemctl reload nginx

# 查看真实生效的完整配置
nginx -T

# 日志字段自定义（排障增强版：记录 upstream 耗时）
log_format main '$remote_addr [$time_local] "$request" $status '
                'rt=$request_time urt=$upstream_response_time '
                'ua="$http_user_agent"';
# rt vs urt：rt 含客户端网络时间，urt 是纯后端耗时——rt 高 urt 低 = 客户端网络慢
```

## 动静分离与缓存

```nginx
# 静态资源缓存（减轻后端 + 提速）
location ~* \.(js|css|png|jpg|woff2)$ {
    root /var/www/static;
    expires 30d;
    add_header Cache-Control "public, immutable";
    access_log off;                  # 静态资源不记日志，省 IO
}

# 反向代理缓存（后端扛不住时的救命稻草）
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=mycache:100m max_size=10g inactive=60m;

location /api/hot {
    proxy_pass http://backend;
    proxy_cache mycache;
    proxy_cache_valid 200 1m;        # 200 响应缓存 1 分钟
    proxy_cache_key "$request_uri";
    proxy_cache_use_stale error timeout updating;  # 后端挂了吐旧缓存（stale 兜底）
    add_header X-Cache $upstream_cache_status;     # HIT/MISS 可见
}
```

## 安全基线

```nginx
server_tokens off;                            # 不暴露版本号
add_header X-Content-Type-Options nosniff;
add_header X-Frame-Options SAMEORIGIN;        # 防点击劫持
client_max_body_size 20m;                     # 防超大上传打爆磁盘

# 敏感路径禁止外网
location /actuator {
    allow 10.0.0.0/8;
    deny all;
}
```
