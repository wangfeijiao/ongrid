# 限流与防刷

> 限流是"防雪崩"的第一道闸门——在攻击/突发流量打死后端之前先挡在 Nginx。

## limit_req：请求速率限制（实测验证）

```nginx
# http 级声明限流区
# rate=10r/s 意为"每 100ms 漏出一个令牌"
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
#                             key     共享内存     速率
# $binary_remote_addr：二进制 IPv4 只占 4 字节（文本占 7-15），10MB 约存 16 万 IP
# 相同 key 共享一个桶 → 同一 IP 共用配额

server {
    location /api/ {
        # burst=20：允许瞬时排队 20 个（桶容量）
        # nodelay：瞬时的 20 个立即处理不排队（占令牌但立刻放行）
        limit_req zone=api burst=20 nodelay;
        limit_req_status 429;          # 默认 503，改成 429（Too Many Requests）
        proxy_pass http://backend;
    }
}
```

### burst 与 nodelay 的行为差异（实测确认）

| 配置 | 超额请求的行为 |
|---|---|
| 只有 rate | 严格匀速：多出来的**直接 503** |
| rate + burst=20 | 排队：挤进桶的等着按速率放行（响应变慢但不拒） |
| rate + burst=20 nodelay | 突发立即处理（前 20 个），第 21 个开始拒 |

**生产建议**：一般用 `burst + nodelay`（突发友好 + 有硬上限）。

### 验证限流生效

```bash
# 快速打 50 个请求看拒绝率
for i in $(seq 1 50); do curl -s -o /dev/null -w "%{http_code} " http://localhost/api/; done; echo
# rate=10r/s burst=20 nodelay → 约 30 个 200，20 个 429
```

## limit_conn：并发连接数限制

```nginx
limit_conn_zone $binary_remote_addr zone=conn:10m;

location /download/ {
    limit_conn conn 10;         # 单 IP 最多 10 个并发连接
    limit_rate_after 10m;       # 前 10MB 全速
    limit_rate 1m;              # 之后限 1MB/s（带宽限制是响应级别的）
}
```

## 白名单放行（内网/自家监控不受限）

```nginx
# geo 声明变量：默认 1，命中内网为 0
geo $limit_key {
    default        1;
    10.0.0.0/8     0;          # 内网不限
    192.168.0.0/16 0;
}

# 空 key 不限流（limit_req 对空 key 不生效）
map $limit_key $limit_zone_key {
    0     "";
    1     $binary_remote_addr;
}
limit_req_zone $limit_zone_key zone=api2:10m rate=10r/s;

location /api/ {
    limit_req zone=api2 burst=20 nodelay;
}
```

## 防 CC 攻击实战（分层防御）

```nginx
# 第 1 层：全局限速（粗）
limit_req_zone $binary_remote_addr zone=general:10m rate=50r/s;

# 第 2 层：登录等敏感接口单独严限
limit_req_zone $binary_remote_addr zone=login:10m rate=1r/s;
location /api/login {
    limit_req zone=login burst=5 nodelay;
    limit_req_status 429;
}

# 第 3 层：按业务维度限（如按 UID）
limit_req_zone $arg_uid zone=peruser:10m rate=5r/s;

# 第 4 层：连接层（慢速攻击 Slowloris 防护）
client_body_timeout 10s;        # 请求体超过 10s 没发完就断
client_header_timeout 10s;
```

## 请求体与头限制（防滥用）

```nginx
client_max_body_size 2m;             # 默认 1m——API 一般 2-10m，上传接口单独放大
client_header_timeout 10s;
client_body_timeout 10s;

# UA 黑名单（简单爬虫）
map $http_user_agent $bad_bot {
    default 0;
    ~*scrapy   1;
    ~*python-requests 1;
    ~*curl     1;         # ⚠️ 谨慎——自家脚本/健康检查也用 curl
}
server {
    if ($bad_bot) { return 403; }
}
```

## 限流的注意点

1. **key 选谁**：`$binary_remote_addr` 在多层代理下是上一跳 LB 的 IP——所有人共享一个桶，必须改用真实 IP：

```nginx
# 从 XFF 取第一跳（仅当 Nginx 在可信 LB 后面）
# real_ip 模块（--with-http_realip_module）
set_real_ip_from 10.0.0.0/8;         # 信任的内网 LB
real_ip_header X-Forwarded-For;
# 之后 $remote_addr 就是真实客户端 IP 了
```

2. **limit_req 是 per-worker 还是全局？**——zone 是共享内存，**全 worker 全局共享**（放心）。
3. **内存满了会怎样**——LRU 淘汰最久没用的 key，不报错；但 key 被淘汰意味着该 IP 配额重置。
4. 限流对 `return` 指令的 location 生效吗？——**不生效**（limit_req 在 preaccess 阶段，return 在 rewrite 阶段就结束了）。
