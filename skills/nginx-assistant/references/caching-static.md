# 缓存与静态资源

> 缓存的三个层次：浏览器缓存（Expires）→ Nginx 代理缓存（proxy_cache）→ 后端缓存。分不清层次是缓存 bug 的根源。

## 静态资源 + 浏览器缓存

```nginx
# 静态文件直出（Nginx 处理静态文件比后端快 10 倍）
location ~* \.(?:css|js|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
    root /var/www/static;
    expires 30d;                          # Cache-Control: max-age=2592000
    add_header Cache-Control "public, immutable";   # immutable：带 hash 的文件名可放心
    access_log off;                       # 静态资源不记日志（省 IO）
}

# HTML 不缓存（发版立即生效的关键）
location = /index.html {
    root /var/www/dist;
    add_header Cache-Control "no-cache";  # no-cache=可缓存但要问服务器；no-store=完全不缓存
}

# 缓存策略口诀：
# 带 hash 的文件名（app.a3f5b2.js）→ 长缓存 + immutable
# 入口文件（index.html）→ no-cache
# API 响应 → 一般不设（后端自己控制）
```

## proxy_cache（代理缓存——后端扛不住时的救命稻草）

```nginx
# http 级定义缓存区
proxy_cache_path /var/cache/nginx
    levels=1:2                    # 两级目录（防单目录文件过多）
    keys_zone=mycache:100m        # 共享内存 100MB（存 key 元数据，约 80 万条）
    max_size=10g                  # 磁盘上限
    inactive=60m                  # 60 分钟没人访问就删
    use_temp_path=off;            # 直接写目标路径（少一次拷贝）

server {
    location /api/hot {
        proxy_pass http://backend;
        proxy_cache mycache;
        proxy_cache_key "$request_uri";              # 缓存键（默认含 Host）
        # 更精细："$scheme$request_method$host$request_uri"
        proxy_cache_valid 200 10s;                   # 200 缓存 10 秒
        proxy_cache_valid 404 1m;                    # 404 也缓存（防穿透）
        proxy_cache_valid any 5s;

        # 后端挂了吐旧缓存（降级神器：牺牲新鲜度保可用性）
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;

        # 绕过缓存的条件
        proxy_cache_bypass $http_authorization;      # 带登录态的请求不走缓存
        proxy_no_cache $http_authorization;          # 且不写入缓存

        add_header X-Cache-Status $upstream_cache_status;   # 调试必备！
    }
}
```

**X-Cache-Status 取值**：`MISS`（没命中，去后端）/ `HIT`（命中）/ `EXPIRED`（过期了但用了）/ `STALE`（后端挂，用旧的）/ `BYPASS`（跳过）。

### 缓存失效的四种手段

```nginx
# 1. 被动等 TTL 到期（简单）
# 2. 主动清除（开源版没有 purge！需要第三方模块 ngx_cache_purge，或商业版）
# 3. 变版本：缓存键里加版本号
set $cache_ver "v2";
proxy_cache_key "$cache_ver$request_uri";    # 发版改 v3 全量失效
# 4. POST 等 souvenir：默认只缓存 GET/HEAD（proxy_cache_methods 控制）
```

## Gzip 与 Brotli

```nginx
# Gzip（内置，必开）
gzip on;
gzip_comp_level 5;                    # 5 是甜点（9 慢一倍收益+2%）
gzip_min_length 1k;                   # 小于 1k 不压（压不出收益还费 CPU）
gzip_types text/css application/javascript application/json text/plain image/svg+xml;
# ⚠️ 默认只压 text/html——JSON 不配 gzip_types 就没压缩！

# 验证：
curl -sI -H "Accept-Encoding: gzip" https://example.com/api/data | grep -i content-encoding

# Brotli（需 ngx_brotli 模块，压缩率比 gzip 高 15-25%）
# brotli on; brotli_types text/css application/javascript;
# 注意：Brotli 只在 HTTPS 下协商（HTTP 中间人可降级）
```

## 静态资源大文件（视频/安装包）

```nginx
location /download/ {
    alias /data/files/;
    sendfile on;               # 内核态零拷贝（大文件必开，默认已开）
    tcp_nopush on;             # sendfile 配合，攒满包再发
    # 限速：前 10MB 全速，之后限到 1MB/s
    limit_rate_after 10m;
    limit_rate 1m;

    # 断点续传（Nginx 自动支持 Range，别在中间层剥掉 Accept-Ranges 头）
}
```

## 前端部署经典架构（SPA）

```nginx
server {
    listen 80;
    root /var/www/dist;
    index index.html;

    # 带 hash 的静态资源：长缓存
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # SPA 路由：所有路径回 index.html
    location / {
        try_files $uri /index.html;
        add_header Cache-Control "no-cache";
    }

    # API 转发
    location /api/ {
        proxy_pass http://backend;
    }
}
```
