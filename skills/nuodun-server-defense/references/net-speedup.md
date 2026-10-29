# 上网提速 · 网络诊断与调优参考

> 先立一个规矩：**没有任何软件能让你的网速超过运营商给你的带宽。**
> 凡是宣称"加速 300%"的，要么在骗你，要么在做代理转发（那是另一回事）。
>
> 这个模块做的是另一件事：**把本机这一侧的拖累逐个消掉**。
> 这些拖累真实存在，量得出来，改完能对比 —— 通常能把"该有的速度"拿回来。

---

## 一、按收益排序：先查什么

| 优先级 | 项目 | 典型收益 | 怎么查 |
|---|---|---|---|
| ① | 物理链路（网线/协商速率/无线信号） | **可能差 10 倍** | 网卡协商速率、丢包率、WiFi 信号 |
| ② | DNS 解析 | 首屏体验明显改善 | 对比各解析器耗时 |
| ③ | 丢包与抖动 | 直接决定实际吞吐 | 多目标 ping 的 min/avg/max |
| ④ | 拥塞控制算法（Linux） | 高延迟/丢包链路可达数倍 | `sysctl net.ipv4.tcp_congestion_control` |
| ⑤ | TCP 窗口与缓冲区 | 高带宽高延迟链路受益 | autotuning、rmem/wmem |
| ⑥ | 后台偷流量 | 释放被占带宽 | 连接数、对端端口分布 |
| ⑦ | 代理残留 / hosts 篡改 | 消除"慢但能通" | 系统代理设置、hosts 内容 |

**顺序很重要**：链路层没解决，调 TCP 参数是白费。
在一条 10Mbps 的网线上开 BBR，就像在乡道上装赛车引擎。

---

## 二、物理链路：收益最大也最容易被忽略

### 2.1 协商速率

网卡和交换机之间会协商一个速率。**协商失败会掉到 10Mbps** ——
此时下载速度上限就是 1.25MB/s，而你会以为"网络变差了"。

```bash
# Linux
ethtool eth0 | grep -E 'Speed|Duplex'
cat /sys/class/net/eth0/speed
```

```powershell
# Windows
Get-NetAdapter | Select-Object Name, LinkSpeed, Status
```

判读：

| 现象 | 原因 |
|---|---|
| 千兆网络里协商到 100Mbps 或 10Mbps | 网线老化 / 水晶头某对线接触不良 / 交换机口有故障 |
| 协商到 100Mbps 且 `Duplex: Half` | **双工不匹配** —— 会大量冲突与丢包，比 10M 全双工还慢 |
| 同一根线换个口就正常 | 对端口有问题，不是线的问题 |

> **先换线换口再查别的。** 这一步成本 5 块钱，收益经常是 10 倍带宽。

### 2.2 网卡错误计数

```bash
ip -s link show eth0        # RX errors / dropped、TX errors / dropped
ethtool -S eth0 | grep -iE 'err|drop|crc|discard'
```

- `rx_crc_errors` / `rx_frame_errors` 持续增长 → **物理层错误**，查线、查光衰、查双工
- `rx_dropped` 增长 → 环形缓冲区不足或中断处理跟不上，调 ring buffer：
  `ethtool -G eth0 rx 4096 tx 4096`

### 2.3 节能以太网（EEE）与网卡省电

笔记本上这两项会造成"闲置一会就慢"和间歇断流：

```bash
ethtool --show-eee eth0                  # 看状态
ethtool --set-eee eth0 eee off           # 关掉（需硬件支持）
```

Windows：设备管理器 → 网卡 → 电源管理 → 取消"允许计算机关闭此设备以节约电源"。

### 2.4 无线（WiFi）

无线场景下，**信号强度比任何系统调优都重要**：

```bash
iw dev wlan0 link        # signal / tx bitrate / freq
```

| 指标 | 判读 |
|---|---|
| signal 优于 -60 dBm | 良好 |
| -60 ~ -70 dBm | 可用，协商速率开始下降 |
| 弱于 -75 dBm | **必然频繁重传**，先解决覆盖问题 |
| freq 2.4GHz 且周围 AP 多 | 干扰严重，换 5GHz |

---

## 三、DNS：最容易被低估的一环

### 3.1 为什么它影响"感觉上的网速"

访问一个网页要先解析域名。如果 DNS 慢或需要重试，
**页面会在"转圈"几秒后才开始加载** —— 带宽再大也救不了这个延迟。

### 3.2 常见问题

| 症状 | 原因 |
|---|---|
| 能 ping 通 IP，但域名打不开 | DNS 服务不可达 |
| 打开网页慢但最终能开 | 配了失效的 DNS（如已卸载的 VPN 残留），每次要先超时 |
| 解析结果指向奇怪 IP | DNS 被劫持（运营商或 hosts 篡改） |
| 内网系统访问不了 | 换成了公共 DNS，解析不到内网域名 |

### 3.3 换 DNS 前必须确认的事

> **企业内网 / 域环境不要盲目换成公共 DNS。**
> 内网域名、OA、内部服务只有内网 DNS 能解析，换掉会导致内网访问失败。
> 这是这个动作被标为「中风险」的唯一原因。

确定可以换之后，优先选**本地就近**的解析器（延迟最低的那个）：

```
223.5.5.5        阿里 AliDNS
119.29.29.29     腾讯 DNSPod
180.76.76.76     百度 BaiduDNS
114.114.114.114  114DNS
```

**判断标准不是"哪个更快"，而是"哪个更快且结果正确"。** 用 `net diag`
对比耗时，同时核对解析出的 IP 是否合理。

---

## 四、TCP 参数

### 4.1 Windows

```powershell
netsh int tcp show global
Get-NetTCPSetting -SettingName Internet
Get-NetOffloadGlobalSetting
```

| 项 | 建议 | 说明 |
|---|---|---|
| `autotuninglevel` | **normal** | 设为 disabled/restricted 会锁死接收窗口，是高延迟链路变慢的经典原因 |
| `heuristics` | **disabled** | 名字容易看反：微软对服务器/网关的建议是"关闭启发式"，让窗口按实际能力放大 |
| `ecncapability` | enabled | 用标记代替丢包通知拥塞；链路不支持时无害 |
| `timestamps` | enabled | 提高 RTT 测量与重传判定精度 |
| `ReceiveSideScaling` | enabled | 多核收包；关闭会让千兆以上链路的包挤在单核 |
| `ReceiveSegmentCoalescing` | enabled | 网卡层合并小包，降低 CPU 占用（**抓包时临时关掉**） |

### 4.2 Linux

```bash
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc
sysctl net.ipv4.tcp_available_congestion_control
```

| 项 | 建议 | 说明 |
|---|---|---|
| `tcp_congestion_control` | **bbr**（内核 ≥4.9） | 见下节 |
| `net.core.default_qdisc` | **fq** | BBR 需要 pacing |
| `tcp_slow_start_after_idle` | 0 | 空闲后不重置拥塞窗口；对长连接服务影响明显 |
| `tcp_mtu_probing` | 1 | PPPoE / VPN 环境防大包黑洞 |
| `tcp_fastopen` | 3 | 省掉一次往返 |
| `netdev_max_backlog` | 16384 | 高 PPS 场景防内核侧丢包 |
| `rmem_max` / `wmem_max` | 16MB | 高带宽高延迟链路需要更大缓冲 |

<a id="bbr"></a>
### 4.3 BBR 是什么、什么时候有用

BBR 不靠"丢包 = 拥塞"来判断，而是主动测量带宽与往返时延，据此控制发送速率。

| 链路特征 | BBR 相对 CUBIC 的效果 |
|---|---|
| 低延迟、无丢包（同机房） | 提升有限，可能略低 —— 不必强求 |
| 高延迟（跨境、卫星、云间互联） | **提升明显，常见数倍** |
| 有轻微丢包（1~3%，移动网络/无线） | **提升最大** —— CUBIC 会把丢包误判为拥塞而大幅降速 |
| 严重拥塞的共享链路 | 可能对其他流不公平，需评估 |

```bash
# 启用（持久化，可回滚）
modprobe tcp_bbr
cat > /etc/sysctl.d/99-ops-bbr.conf <<'EOF'
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
EOF
sysctl --system
```

> 已建立的连接不会立即切换，**新连接才生效**。建议低峰执行。

### 4.4 关于队列规则

BBR 依赖 pacing，所以队列规则要能 pacing：

| 队列 | 能否配合 BBR |
|---|---|
| `fq` | 可以，BBR 的经典搭配 |
| `fq_codel` | 可以（很多发行版默认就是它） |
| `cake` | 可以 |
| `sfq` | 可以 |
| `pfifo_fast` | **不行** —— 无 pacing，BBR 效果打折 |

---

## 五、带宽被偷：谁在用你的网

```bash
# 已建立连接按对端端口聚合 —— 能看出谁在持续通信
ss -tn state established | awk 'NR>1 {print $5}' | sed 's/.*://' \
  | sort | uniq -c | sort -rn | head -20

# 按进程看（需要 root）
ss -tnp | grep -v LISTEN
```

Windows：

```powershell
Get-NetTCPConnection -State Established |
  Group-Object OwningProcess |
  Sort-Object Count -Descending | Select-Object -First 15
```

常见"偷流量"来源：网盘同步、系统更新、备份软件、云盘/下载器后台做种、
遥测上报、以及**失控重连**（连接数异常多但流量很小，说明在死循环重试）。

判据：**连接数很多但实际流量很低 → 不是占带宽，是程序有问题。**

---

<a id="proxy"></a>
## 六、代理残留：最像"网慢"的假象

表现：**网页打开很慢，但最终能打开；`curl` 卡住很久然后成功。**

原因：系统或环境变量里留着代理配置，而代理已经不可用，
每次请求都要先等连接超时，再回落到直连。

```bash
env | grep -i proxy        # http_proxy / https_proxy / all_proxy
```

Windows：

```powershell
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' |
  Select-Object ProxyEnable, ProxyServer, AutoConfigURL
```

`ProxyEnable=1` 但 `ProxyServer` 指向一个已经不存在的地址 ——
这就是卸载软件时没关开关留下的坑。

---

<a id="hosts"></a>
## 七、hosts 被改：不是"慢"，是"走错路"

```bash
grep -vE '^\s*(#|$)' /etc/hosts
```

如果看到把 `www.baidu.com`、`update.microsoft.com` 之类指向内网或第三方 IP，
那要么是历史遗留的阻断配置，要么是流量劫持。

Windows：`C:\Windows\System32\drivers\etc\hosts`

> 确认无关后删除，并把 hosts 设为只读（防再次被改）。

---

## 八、怎么证明"真的变快了"

改之前和改之后，用**同一组测量**对比，不要靠感觉：

```
① 延迟：  ping 网关 / 公共 DNS / 常用站点   → 看 min/avg/max 与丢包
② 解析：  net diag 里各解析器可用性
③ 首字节：网页 HTTP 响应耗时
④ 吞吐：  下载一个已知大小的文件，算实际 MB/s
```

**关键**：延迟看 `min`（链路本身好不好），`avg` 与 `max` 的差距看稳定性。
平均 20ms 但最大 400ms 的链路，体验比稳定 60ms 差得多。

---

## 九、汇报口径

```
上网诊断小结
  物理层：<网卡速率/双工/错误计数>   ← 有问题先修这里，其余调优都是次要
  DNS   ：当前 <...>，实测可用 <...>
  链路  ：网关 <min/avg/max，丢包>；外网 <...>
  TCP   ：<拥塞算法 / autotuning / 队列>

  建议按这个顺序处理（不要跳步）：
    1. <物理层>
    2. <DNS>
    3. <TCP 参数>
  调优脚本：<路径>（含回滚脚本）
  未覆盖：<n> 项 —— 原因 <...>

  ⚠ 明确说明：以上优化不会超过运营商带宽上限；带宽不足只能升级线路。
```

**不要承诺"网速变快多少倍"。** 应该说明：消除了哪一项本机侧限制，
预期在哪类场景（高延迟/丢包/首屏）受益，以及怎么验证。
