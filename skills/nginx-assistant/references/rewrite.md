# rewrite 与变量

> rewrite 是 Nginx 里最容易写炸的部分——它改变的是**请求处理流程本身**。

## rewrite 指令详解

```nginx
# 语法：rewrite regex replacement [flag];
# 只能出现在 server、location、if 上下文

server {
    # 把 /old/xxx 重写到 /new/xxx
    rewrite ^/old/(.*)$ /new/$1 last;

    # 带查询参数保留（rewrite 默认自动附加原 query string）
    # /old?page=2 → /new?page=2（自动带上，除非 replacement 里有 ?）
    rewrite ^/search$ /new-search? last;
}

location /new/ { ... }
```

### 四个 flag 的生死区别（实测验证）

| flag | 行为 | 使用场景 |
|---|---|---|
| `last` | 停止当前 rewrite，**重新匹配所有 location** | 常用默认选择 |
| `break` | 停止 rewrite，**留在当前 location 继续处理** | content 处理器内改路径 |
| `redirect` | 302 临时跳转（浏览器 URL 变） | 临时跳转 |
| `permanent` | 301 永久跳转（SEO 权重转移） | 旧域名/旧路径迁移 |

**last vs break 的实测差异（重要，多数教程讲错）**：

```nginx
server {
    rewrite ^/a$ /b last;      # 改写为 /b → 重新走 location 匹配 → 命中 location /b ✅
    rewrite ^/a2$ /b break;    # 改写为 /b → 停止 rewrite。实测：**server 级 break 之后
                               # 依然进入 location 匹配**（也命中 location /b）！
    location /b  { return 200 "hit /b"; }
}
```

- **server 上下文里**：`last` 与 `break` 最终都以改写后的 URI 进入 location 匹配（行为几乎一样——server 级 rewrite 本来就在 location 匹配之前执行）
- **location 上下文里**才是真正的分水岭：`last` 跳出去重新匹配 location（可能进别的 location，甚至循环）；`break` 留在当前 location，用改写后的 URI 走 content 阶段（找文件/进 proxy_pass）

```nginx
location /download/ {
    rewrite ^/download/(.*)$ /files/$1 break;   # 留在本 location，以 /files/xxx 找文件
    root /data;
    # ⚠️ 实测注意：break 会跳过同 location 中【后续所有 rewrite 阶段指令】——
    # 包括它后面的 return / set / 再一个 rewrite 都不执行！
    # （实测：rewrite...break 后跟 return 200，return 不生效，直接进 content 阶段）
}
```

**无限循环**：`rewrite ^/b$ /b last;`（location 内自转）→ internal redirection cycle → 500。

## return（比 rewrite 更简单直接）

```nginx
# return 能做的就别用 rewrite
return 301 https://$host$request_uri;          # 整站跳 HTTPS（唯一正确姿势）
return 302 /maintenance.html;                  # 临时维护页
return 403;                                    # 直接拒绝
return 200 "ok";                               # 健康检查端点
return 444;                                    # 不回应直接断连（恶意请求）

# ⚠️ return 在 server 级会先于 location 执行；带响应体的 return 只能在 location
```

## if 的著名坑（官方都说"if is evil"）

```nginx
# if 在 location 内 rewrite 阶段执行，行为不总是直觉的：

# ✅ if 里可以安全做的事（官方认可的白名单）
if ($request_method = POST) { return 405; }
if ($http_user_agent ~* "bot") { return 403; }
if ($request_uri = /healthz) { return 200 "ok"; }

# 🚫 if 里千万别做的事：
# 1. if 里嵌 if（语法直接报错）
# 2. if 里用 rewrite + 期望后续正常执行（rewrite 阶段会重入）
# 3. if 里写 try_files（部分版本行为不一致，改用 map）
```

### 用 map 替代 if（正确姿势）

```nginx
# 需求：内网直通、外网跳登录
map $remote_addr $is_internal {
    default         0;
    10.0.0.0/8     1;
    192.168.0.0/16 1;
}
server {
    if ($is_internal) { return 200 "internal ok"; }
}
# map 在配置解析时算好，运行期零开销，且行为可预期
```

## 常用内置变量速查

| 变量 | 含义 | 示例值 |
|---|---|---|
| `$host` | 请求的 Host 头（无端口） | api.example.com |
| `$http_host` | Host 头（带端口） | api.example.com:8080 |
| `$remote_addr` | 客户端 IP（直连者！过了 LB 就是 LB 的 IP） | 10.0.0.5 |
| `$request_uri` | **原始**完整 URI（含 query） | /a/b?x=1 |
| `$uri` | **当前**（可能已被 rewrite 改写）的 URI，不含 query | /a/b |
| `$args` / `$arg_name` | query string / 单个参数 | x=1 / 1 |
| `$scheme` | http / https | https |
| `$request_method` | GET/POST... | GET |
| `$http_xxx` | 任意请求头（小写下划线） | $http_authorization |
| `$proxy_add_x_forwarded_for` | 追加式 XFF 头 | 10.0.0.5, 1.2.3.4 |
| `$upstream_addr` | 实际打到的后端 | 10.0.1.10:8080 |
| `$upstream_response_time` | 后端耗时（多后端空格分隔） | 0.012 |
| `$request_time` | 请求总耗时（含客户端网络） | 0.500 |

**`$request_uri` vs `$uri` 的坑**（rewrite 排障必看）：rewrite 后 `$uri` 变了，`$request_uri` 始终是原始值——做日志审计/防重放要 `$request_uri`。

## 正则捕获与复用

```nginx
# rewrite 的捕获组后向引用 $1 $2...
rewrite ^/user/(\d+)/(profile|settings)$ /u/$1/$2? last;

# 正则 location 里 $1..$9 同样可用
location ~ ^/img/(\d{4})/(\d{2})/(.+)\.(jpg|png)$ {
    root /data;
    # /img/2026/09/cover.jpg → /data/img/2026/09/cover.jpg
}
```

## 常见 rewrite 需求配方

```nginx
# 1. 强制去 www
if ($host ~* ^www\.(.+)$) { return 301 https://$1$request_uri; }

# 2. http → https
if ($scheme = http) { return 301 https://$host$request_uri; }

# 3. 去掉 .php 后缀访问
rewrite ^/(\w+)$ /$1.php last;

# 4. 尾斜杠统一（/dir/ → /dir）
if ($request_uri ~ ^(.+)/+$) { return 301 $1; }

# 5. 整个子目录迁移
rewrite ^/blog/(.*)$ https://new.example.com/$1 permanent;
```
