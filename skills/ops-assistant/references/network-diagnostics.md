# 网络诊断

> 网络问题的通用诊断链：**DNS → 路由 → 端口 → 应用**，逐层验证，每层不通就停在该层深挖。

## 诊断链四层（标准动作）

### 第 1 层：DNS 解析

```bash
# ✅
dig example.com                 # 看 ANSWER SECTION 和 Query time
dig @8.8.8.8 example.com        # 指定 DNS 服务器对比（判断是本地 DNS 问题还是权威问题）
nslookup example.com            # 简单场景
cat /etc/resolv.conf            # 本机用的 DNS

# 常见问题：
# - 解析到错误 IP：DNS 缓存（systemd-resolve --flush-caches）/ hosts 文件残留
# - 内网域名解析失败：内网 DNS 没配或优先顺序错
# - 解析慢（Query time 几百 ms）：换就近 DNS
```

### 第 2 层：路由可达性

```bash
# ✅
ping -c 4 <ip>                  # 通不通 + 延迟 + 丢包
mtr <ip>                        # ping+traceroute 合体，看每一跳丢包（装：apt/yum install mtr）
traceroute <ip>                 # 无 mtr 时用

# mtr 读法：
# 某跳丢包但后续跳不丢 = 该路由限速 ICMP，不是问题
# 某跳开始丢包且后续都丢 = 真问题在这一跳之后
```

### 第 3 层：端口连通性

```bash
# ✅ 目标端口开不开
nc -zv <ip> 80                  # 最直接
telnet <ip> 80                  # 老牌工具
curl -v telnet://<ip>:80        # 没装 nc 时用 curl 也行

# 本机端口监听状态
ss -tlnp                        # TCP 监听（谁监听的、绑在哪个地址）
ss -ulnp                        # UDP
# 经典坑：服务绑了 127.0.0.1 而不是 0.0.0.0 —— 本机通、外部不通

# 端口被谁占了（启动报 Address already in use）
ss -tlnp | grep :8080
lsof -i :8080
```

### 第 4 层：应用层（HTTP/TLS）

```bash
# ✅ 全链路计时（慢在哪一目了然）
curl -o /dev/null -s -w \
  "DNS: %{time_namelookup}s | TCP: %{time_connect}s | TLS: %{time_appconnect}s | 首字节: %{time_starttransfer}s | 总: %{time_total}s\n" \
  https://api.example.com/healthz

# 看 TLS 握手和证书
openssl s_client -connect example.com:443 -servername example.com < /dev/null 2>/dev/null | openssl x509 -noout -dates -subject
# -dates：证书有效期——"网站突然打不开"先查证书过期！

# 绕过 DNS 直连 IP（判断是 DNS 问题还是服务问题）
curl -v --resolve example.com:443:1.2.3.4 https://example.com/
```

## 防火墙与安全组排查

```bash
# 本机防火墙（✅ 只读查看）
iptables -L -n -v | head -30        # 规则链（看 DROP/REJECT）
firewall-cmd --list-all             # firewalld（CentOS）
ufw status verbose                  # Ubuntu

# 云上四层检查（由外到内）：
# 1. 安全组（云平台控制台）——出/入方向都要看
# 2. 网络 ACL（子网级，常被遗忘）
# 3. 系统防火墙（iptables/firewalld）
# 4. 应用绑定地址（127.0.0.1 vs 0.0.0.0）
```

**经典故障模式**：
- "本地能连，外部不能" → 绑了 127.0.0.1 或安全组没放
- "同一内网通，跨网段不通" → 路由/网络 ACL
- "能 ping 通但端口不通" → 防火墙或安全组（ICMP 和 TCP 是分开放的）
- "间歇性不通" → 连接跟踪表满（见下）

## 连接跟踪表满（conntrack：间歇性丢包的隐形杀手）

```bash
# ✅ 症状：偶发连接超时，重启防火墙服务"就好了"（其实清了表）
cat /proc/sys/net/netfilter/nf_conntrack_count
cat /proc/sys/net/netfilter/nf_conntrack_max
# count 接近 max → 就是它

# dmesg 会有：nf_conntrack: table full, dropping packet
# 解决：调大 max + 缩短 timeout，或排查是否有连接泄漏
sysctl -w net.netfilter.nf_conntrack_max=2097152
```

## 抓包（终极武器）

```bash
# ⚠️ 抓包有 IO/CPU 开销，生产环境限制条件抓

# 抓某端口前 100 个包
tcpdump -i any -nn port 80 -c 100

# 抓某主机的包存文件（Wireshark 离线分析）
tcpdump -i any -nn host 10.0.1.5 -w /tmp/capture.pcap -c 1000

# 看 HTTP 明文请求（非 TLS）
tcpdump -i any -nn -A port 80 | grep -E "GET|POST|HTTP"

# 排查"连接是谁断的"：看 RST/FIN 来自哪边
tcpdump -i any -nn 'tcp[tcpflags] & (tcp-rst|tcp-fin) != 0' and port 8080

# DNS 查询抓包（排查解析异常）
tcpdump -i any -nn port 53
```

**三次握手异常速判**（在 pcap 里看）：
- SYN 发了没回 SYN-ACK → 对端没监听/防火墙丢包
- SYN-ACK 回了但客户端 RST → 客户端侧问题（如 backlog 满）
- 握手完成立即 RST → 应用层拒绝（如连接数满）

## 网络质量评估

```bash
# 延迟与丢包（✅）
ping -c 100 -i 0.2 <ip> | tail -2     # 看 packet loss 和 rtt min/avg/max/mdev
# mdev（标准差）大 = 延迟抖动厉害（视频会议/游戏卡的原因）

# 带宽实测（需要两端配合）
iperf3 -s                              # 服务端
iperf3 -c <server-ip> -t 10            # 客户端测 10 秒

# HTTP 层吞吐
curl -o /dev/null -w "下载速度: %{speed_download} B/s\n" http://example.com/bigfile
```

## IPv6 引发的玄学问题

```bash
# 症状：DNS 解析正常但 curl 超时，ping 却通（走了 IPv6 而服务没监听 v6）
curl -4 -v https://example.com         # 强制 IPv4 验证
curl -6 -v https://example.com         # 强制 IPv6 验证
# 解法：应用同时监听双栈，或 DNS 层处理
```
