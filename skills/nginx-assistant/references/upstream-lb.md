# upstream 与负载均衡

> upstream 是 Nginx 从"静态服务器"升级为"流量调度器"的关键模块。

## 负载均衡算法（五种）

```nginx
upstream backend {
    # ① 轮询（默认）：逐个分配
    server 10.0.1.10:8080;
    server 10.0.1.11:8080;

    # ② 加权轮询：按权重比例分配（后端机器配置不均时）
    # server 10.0.1.10:8080 weight=3;    # 3/4 流量
    # server 10.0.1.11:8080 weight=1;    # 1/4 流量

    # ③ least_conn：最少连接优先（请求耗时不均时比轮询公平）
    # least_conn;

    # ④ ip_hash：同 IP 恒定到同一台（会话粘性——⚠️ 不推荐，破坏均衡且扩容后大洗牌）
    # ip_hash;
    # 会话粘性的正解：应用层共享会话（Redis）

    # ⑤ hash：一致性哈希（缓存服务专用——节点增减只迁移 1/n 的 key）
    # hash $request_uri consistent;
}
```

## server 参数详解

```nginx
upstream backend {
    server 10.0.1.10:8080
        weight=2               # 权重（默认 1）
        max_fails=3            # fail_timeout 内失败 3 次 → 标记不可用
        fail_timeout=30s       # 统计窗口 & 标记不可用的时长（30s 后再试）
        ;

    server 10.0.1.11:8080 backup;      # 备用：主力全挂才启用（灾备机/冷备）
    server 10.0.1.12:8080 down;        # 手动摘除（运维下线单台时用）
    server 10.0.1.13:8080 max_conns=1000;  # 单节点最大并发连接（过载保护，需 zone）

    # 摘除节点发30s —— drain 模式：不接新请求？开源版没有（商业版 slow_start + drain）
    # 开源版下线姿势：改 down → nginx -t → reload（已有长连接会逐步退出）
}
```

## keepalive 长连接池（性能关键，实测显著降 TIME_WAIT）

```nginx
upstream backend {
    server 10.0.1.10:8080;
    keepalive 64;               # 每 worker 预热的连接缓存数
}

location / {
    proxy_pass http://backend;
    # ⚠️ keepalive 生效的三件套（缺一不可）：
    proxy_http_version 1.1;          # ① 1.1
    proxy_set_header Connection "";  # ② 清空 Connection（默认是 close）
    # ③ upstream 里 keepalive N
}
```

不配 keepalive 时：每个请求新建一条到后端的 TCP（用完即关）——高 QPS 下 TIME_WAIT 数十万条，端口耗尽。

## 健康检查

```nginx
# 开源版只有"被动健康检查"（max_fails/fail_timeout，上面已讲）
# 特点：由真实流量探测，空载时不会主动发现问题

# 主动健康检查（商业版 nginx plus 或第三方模块 ngx_upstream_check_module）
# 开源替代方案：
# 1. 用 max_fails=1 fail_timeout=10s 提高敏感度
# 2. 外部探测脚本摘节点：Consul/Nacos + confd 动态改配置
# 3. OpenResty + lua-resty-upstream-healthcheck（主动检查 + 自动摘除）
```

## 动态 upstream（DNS 解析）

```nginx
# 场景：后端是域名（K8s Service / 云 SLB / 容器），IP 会变
resolver 10.96.0.10 valid=10s;      # 内部 DNS，10s 缓存

# 写法①：变量强制运行时解析（proxy_pass 直接写域名只在启动时解析一次！）
set $backend http://api-service.default.svc:8080;
proxy_pass $backend;

# 写法②：upstream + zone（K8s 环境常见）
upstream backend {
    zone backend_dynamic 64k;       # 共享内存放运行时状态
    server api-service:8080 resolve; # resolve 参数：动态解析（1.27+ 需 zone）
}
```

## 灰度/金丝雀分流（按比例）

```nginx
# split_clients 按请求 ID 稳定分流（同一用户永远同一桶）
split_clients "${remote_addr}${http_user_agent}" $backend_pool {
    5%     canary;      # 5% 流量到金丝雀组
    *      stable;      # 其余走稳定组
}

upstream stable { server 10.0.1.10:8080; server 10.0.1.11:8080; }
upstream canary { server 10.0.2.10:8080; }

server {
    location / {
        proxy_pass http://$backend_pool;
        add_header X-Upstream-Pool $backend_pool always;   # 响应头标记（排障用）
    }
}

# 按请求头灰度（测试账号定向）
map $http_x_gray_release $pool_override {
    default "";
    "true"  canary;
}
# location 里：set $final_pool $pool_override$backend_pool（空值回退默认）
```

## 排障：看 upstream 实际状态

```bash
# access_log 加 $upstream_addr：看每个请求真实打到哪台
# 输出如 10.0.1.10:8080 或 10.0.1.10:8080, 10.0.1.11:8080（逗号=重试过）

# error.log 里 upstream 关键词：
# "upstream timed out"          → 超时（504）
# "no live upstreams"           → 全军覆没
# "upstream prematurely closed" → 后端崩了

# 被标记不可用的节点会自动恢复（fail_timeout 到期），无需手动干预
```
