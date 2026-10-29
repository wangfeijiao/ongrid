# 🚨 故障排查（502/504 专题）

> Nginx 是流量的必经之路——它坏了全站坏，它报错时要快速分清是"它的锅"还是"后端的锅"。

## 502 Bad Gateway（Nginx 连不上后端）

```
502 = Nginx 尝试连后端失败了（拒连/断连/后端崩溃）
```

**排查链（按 error.log 关键词分诊）**：

```bash
tail -100 /var/log/nginx/error.log
```

| error.log 关键词 | 根因 | 处理 |
|---|---|---|
| `connect() failed (111: Connection refused)` | 后端进程没跑/端口错 | `ss -tlnp \| grep 8080` 确认监听 |
| `connect() failed (110: Connection timed out)` | 网络不通/防火墙 | 到后端机器 `telnet ip port` |
| `upstream prematurely closed connection` | 后端接受连接后崩溃（OOM/段错误） | 查后端日志 |
| `no live upstreams` | 所有节点被 max_fails 摘除 | 查 upstream 健康状态 |
| `connect() failed (99: Cannot assign requested address)` | 本机端口耗尽（TIME_WAIT 海量） | 后端开 keepalive / 调内核参数 |
| `SSL_do_handshake() failed` | HTTPS 后端握手失败（协议/证书不匹配） | 检查 proxy_ssl 配置 |

## 504 Gateway Timeout（后端太慢）

```
504 = 连上了后端，但它没在 proxy_read_timeout 内给出响应
```

```bash
# 三步定位：
# 1. 后端真的慢还是超时设太短？
#    日志里看 $upstream_response_time：
#    60.001 → 死等到超时上限；0.5 → 别的问题
# 2. 直连后端测（跳过 Nginx）：
curl -o /dev/null -s -w "%{time_total}s\n" http://10.0.1.10:8080/slow-api
# 3. 后端慢的常见根因：慢查询/线程池满/下游依赖超时/连接池耗尽
```

**特殊 504**：`504 upstream timed out (110: Connection timed out) while reading response header`——读响应头超时，几乎都是后端卡死。

## 503 Service Unavailable

```
三种来源：
1. limit_req 触发（改成 limit_req_status 429 更语义化）
2. upstream 全挂（no live upstreams 也可能报 503）
3. Nginx 主动维护页：return 503 + error_page
```

## 400/413/431：请求头/体超限

```nginx
# 400 Bad Request：请求头太大（长 cookie/大 URL）
client_header_buffer_size 8k;       # 默认 1k，某些大 cookie 场景要调
large_client_header_buffers 4 16k;

# 413 Payload Too Large：上传超限
client_max_body_size 100m;          # 默认 1m！上传接口必配

# 431 Request Header Fields Too Large：cookie 太多
# 还是 large_client_header_buffers 的事
```

## 经典报错速查

### `rewrite or internal redirection cycle`
```nginx
# rewrite 自己转回自己，10 次后 500
# 场景1：rewrite ^/b$ /b last;（自转）
# 场景2：try_files $uri /index.html + /index.html 又被 rewrite
# 排查：error.log 会显示完整的重写链条，顺着找循环点
```

### `host not found in upstream`
```nginx
# 启动时 DNS 解析失败（proxy_pass 直接写域名时，Nginx 启动期就解析并缓存）
server api.example.com:8080;      # DNS 挂了 nginx 起不来
# 解法：变量 + resolver 延迟解析
resolver 10.0.0.2 valid=30s;
set $backend api.example.com:8080;
proxy_pass http://$backend;
```

### `client intended to send too large body`（error.log 里的 413）
上传被 Nginx 拦截——`client_max_body_size`，同时后端的上传限制也要查。

### `too many open files`
```bash
# worker 进程句柄耗尽（高并发+日志文件多）
ulimit -n                          # 当前限制
# nginx.conf 主上下文：worker_rlimit_nofile 65535;
# systemd 的 LimitNOFILE 也要一起改（取两者较小值生效）
```

### `could not build optimal types_hash`（告警）
`types_hash_max_size` 太小——加 `types_hash_max_size 4096;`（新版本默认已够）。

## 排障工具箱

```bash
nginx -T                        # 看完整生效配置（include 链展开——排查"改了没生效"神器）
nginx -t                        # 语法检查
nginx -s reload                 # 平滑重载
curl -v -H "Host: api.example.com" http://127.0.0.1/path   # 绕过 DNS 直测某个 server 块

# 日志里挖金（error.log 定位）
tail -f /var/log/nginx/error.log | grep --line-buffered upstream

# access.log 看上游耗时分布（log_format 加 urt=$upstream_response_time 后）
awk '{print $NF}' access.log | sort -rn | head -20
```

## "改了没生效"五连查

```
1. nginx -t 通过了吗？（没 reload 一切白搭）
2. reload 了吗？（改完不 reload 是新手第一坑）
3. nginx -T 里能搜到你改的行吗？（include 路径错了，改的文件根本没被加载）
4. 有没有多个 server 都能接这个请求？（default_server 抢走了）
5. 浏览器缓存？（curl -H 'Cache-Control: no-cache' 或加随机参数 ?_=123）
```
