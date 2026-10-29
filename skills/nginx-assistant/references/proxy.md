# 反向代理与 proxy_pass

> proxy_pass 是 Nginx 的灵魂指令，而它的最大坑是**带不带 URI 的行为差异**。

## proxy_pass 带不带 URI：巨大差异（实测验证）

```nginx
# 情形 1：不带 URI（只有 host:port）——location 前缀被保留传给后端
location /api/ {
    proxy_pass http://backend;         # /api/users → 后端收到 /api/users
}

# 情形 2：带 URI（哪怕一个 /）——location 匹配部分被替换
location /api/ {
    proxy_pass http://backend/;         # /api/users → 后端收到 /users
}

# 情形 3：带路径前缀——替换
location /api/ {
    proxy_pass http://backend/v2/;      # /api/users → 后端收到 /v2/users
}
# ⚠️ ⚠️ 规则：proxy_pass 带 URI 时，请求 URI 中与 location 前缀匹配的部分被 URI 替换
```

**实测对照表**：

| location | proxy_pass | 请求 | 后端收到 |
|---|---|---|---|
| `/api/` | `http://be` | `/api/users` | `/api/users` |
| `/api/` | `http://be/` | `/api/users` | `/users` |
| `/api/` | `http://be/v2/` | `/api/users` | `/v2/users` |
| `/` | `http://be` | `/api/users` | `/api/users` |
| 正则 location | 🚫 不能带 URI | — | `nginx -t` 直接报错 |

## 生产级代理配置（完整模板）

```nginx
upstream backend {
    server 10.0.1.10:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.11:8080 max_fails=3 fail_timeout=30s;
    keepalive 64;                        # 到后端的长连接池
}

server {
    listen 80;
    location / {
        proxy_pass http://backend;      # 注意：upstream 名不带 URI（默认情形 1）

        proxy_http_version 1.1;         # keepalive 必须 1.1
        proxy_set_header Connection ""; # 清空头才能复用连接

        # ---- 客户端信息传递（后端拿真实 IP 全靠这三行）----
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ---- 超时三兄弟（504 排障就看它们）----
        proxy_connect_timeout 5s;         # 连后端（含 DNS）
        proxy_send_timeout    60s;        # 请求发给后端
        proxy_read_timeout    60s;        # 等后端响应——最常调的这个

        # ---- 故障转移 ----
        proxy_next_upstream error timeout http_502 http_503;
        proxy_next_upstream_tries 2;      # 最多试 2 台
    }
}
```

## 后端拿不到真实 IP？

```
Nginx 侧（✅ 上面三行 set_header 已做）

⚠️ 实测发现（易误解）：不带 proxy_set_header Host 时，后端收到的 Host 头是
   upstream 组名（如 "echo_be"），不是客户端请求的域名！
   所以后端按 Host 做虚拟路由/校验时，必须显式 proxy_set_header Host $host。

后端侧要自己解析 XFF 头的第一个 IP：
- Java：server.forward-headers-strategy=framework（Spring Boot）
- Express：app.set('trust proxy', 1)
- PHP：$_SERVER['HTTP_X_FORWARDED_FOR']
⚠️ XFF 可被伪造——只有可信代理链追加的才可信，第一跳之外的用户输入别信
```

## WebSocket 代理（多两行头）

```nginx
location /ws/ {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;     # WebSocket 握手头
    proxy_set_header Connection "upgrade";        # ⚠️ 与普通代理的清空写法冲突，分开 location
    proxy_read_timeout 300s;                       # 长连接空闲超时要拉长
}
```

## proxy_pass 到变量（动态路由）

```nginx
# 变量形式不会自动解析 DNS——必须配 resolver
resolver 10.0.0.2 valid=30s;

location / {
    set $target https://api.upstream.com;
    proxy_pass $target;              # 动态目标（蓝绿切换/按参数路由）
}

# 带变量时 URI 不会自动追加，需要显式拼：
proxy_pass http://$backend_host$uri$is_args$args;
```

## 大文件上传

```nginx
# 上传打不上去/413：检查两处
client_max_body_size 100m;         # ① Nginx 层（默认只有 1m！）
# ② 后端也要改（如 PHP 的 upload_max_filesize、Tomcat 的 maxPostSize）

# 大响应体慢：关掉缓冲直接流式
proxy_buffering off;                # 实时性优先（SSE 必须关）
proxy_request_buffering off;        # 上传流式透传
```

## 代理到 Unix socket（本机后端）

```nginx
upstream php-fpm-sock {
    server unix:/run/php/php8.2-fpm.sock;
    # fastcgi 场景用 fastcgi_pass，普通 http 用 proxy_pass
}
```

## 验证后端收到了什么（排障利器）

```bash
# 后端不确定收到的路径/头？起个回显服务：
# npx http-echo-server 或一行 Node：
node -e "require('http').createServer((q,s)=>{console.log(q.method,q.url,JSON.stringify(q.headers));s.end('ok')}).listen(9000)"

# 然后从 Nginx 打请求，直接看后端日志里的真实 URL 和 headers
curl http://nginx-host/api/users
```
