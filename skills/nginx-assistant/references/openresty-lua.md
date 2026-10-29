# OpenResty 与 Lua 扩展

> 原生 Nginx 配置是静态的；OpenResty = Nginx + LuaJIT，让配置"活"起来。

## OpenResty 是什么

```
OpenResty = Nginx（核心）+ LuaJIT + 一堆 lua-resty-* 库
本质：把 Lua 嵌入 Nginx 的各处理阶段，请求处理过程中可以跑 Lua 代码
用途：API 网关（Kong/APISIX 都基于它）、WAF、动态路由、业务逻辑前置
```

**选型**：
- 只是代理/静态服务 → 原生 Nginx 足够（更轻、升级更简单）
- 需要动态逻辑（按请求内容查 Redis 决策、复杂鉴权、灰度规则）→ OpenResty
- 需要完整网关生态（插件/控制面）→ 直接用 Kong 或 APISIX

## 执行阶段（Lua 挂载点）

```
请求进来 →
  rewrite 阶段（access_by_lua 之前）  rewrite_by_lua：改 URL/重定向
  access 阶段                        access_by_lua：鉴权/限流/黑白名单（最常用）
  content 阶段                       content_by_lua：直接生成响应（写 API）
  header/filter 阶段                 header_filter_by_lua：改响应头
  log 阶段                           log_by_lua：异步统计上报
```

## 实战片段

### 动态鉴权（access_by_lua）

```nginx
location /api/ {
    access_by_lua_block {
        local token = ngx.var.http_authorization
        if not token then
            ngx.exit(401)
        end
        -- 查共享内存缓存（毫秒级）
        local user_cache = ngx.shared.user_cache
        local user = user_cache:get(token)
        if not user then
            -- 缓存未命中查 Redis（lua-resty-redis）
            local redis = require "resty.redis"
            local red = redis:new()
            red:set_timeout(100)          -- ⚠️ 必须设超时！否则卡死 worker
            local ok, err = red:connect("127.0.0.1", 6379)
            if not ok then
                ngx.log(ngx.ERR, "redis: ", err)
                ngx.exit(503)             -- 依赖挂了降级，而不是 500
            end
            user = red:get("token:" .. token)
            red:set_keepalive(10000, 100) -- 连接池复用（必做）
            if not user then
                ngx.exit(401)
            end
            user_cache:set(token, user, 60)   -- 回填缓存 60s
        end
        ngx.var.x_user = user               -- 传给后续阶段/后端
    }
    proxy_pass http://backend;
    proxy_set_header X-User $x_user;
}
```

### 动态 upstream 切换（蓝绿/熔断）

```nginx
upstream blue  { server 10.0.1.10:8080; }
upstream green { server 10.0.2.10:8080; }

server {
    # 开关放共享内存，API 热更新（reload 都不用）
    lua_shared_dict config 1m;

    location / {
        set $target "";
        access_by_lua_block {
            local conf = ngx.shared.config
            local pool = conf:get("pool") or "blue"    -- 默认蓝
            ngx.var.target = pool
        }
        proxy_pass http://$target;    -- ⚠️ 变量 upstream，upstream 名需匹配
    }
}
```

### 简易 WAF 思路

```nginx
access_by_lua_block {
    local ngx_re = require "ngx.re"
    -- 规则1：URL 里的 SQL 注入特征
    local uri = ngx.var.request_uri
    if ngx_re.find(uri, [[(union\s+select|drop\s+table|or\s+1=1)]], "ijo") then
        ngx.exit(403)
    end
    -- 规则2：body 超限
    if tonumber(ngx.var.http_content_length or 0) > 1048576 then
        ngx.exit(413)
    end
    -- 生产别手搓：用 OpenWAF / Coraza / APISIX 的 waf 插件（规则集维护是持续成本）
}
```

## Lua 里的性能红线

```lua
-- 1. 禁止阻塞操作：Lua 里只能用 cosocket（ngx.socket.tcp）和非阻塞库
--    require "socket" / os.execute("...") / io.open() 都会阻塞整个 worker！
--
-- 2. 必设超时：red:set_timeout(100)，cosocket 默认 60s（太长）
--
-- 3. 连接复用：red:set_keepalive(10000, 100)（用完归还池子）
--
-- 4. 共享内存 vs worker 本地：
--    ngx.shared.* 跨 worker 共享（有限流 API：add/incr 原子操作）
--    所有 Lua 模块变量：每个 worker 一份（初始化一次）
--
-- 5. init_by_lua 里做全局初始化（连配置中心/预热规则）
```

## 日志与调试

```nginx
error_log /var/log/nginx/error.log debug;   # Lua 的 ngx.log 输出到这里

-- Lua 里：
ngx.log(ngx.ERR, "something wrong: ", err)   -- error 级
ngx.log(ngx.WARN, "slow query: ", took)      -- warn 级
```

## 原生 Nginx 能否嵌入 Lua？

不能——OpenResty 是独立发行版（魔改的 Nginx 核心 + LuaJIT）。在原生 Nginx 上编译 njs（JavaScript）模块是另一个选择：`--with-http_js_module`，用 JS 写动态逻辑（能力弱于 Lua 生态但够轻）。
