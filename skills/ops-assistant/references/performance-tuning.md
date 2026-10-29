# 性能调优

> 调优第一定律：**先测量，后优化**。没有数据就调参数，等于闭眼开车。

## 四层瓶颈定位法

```
CPU → 内存 → 磁盘 IO → 网络，逐层排除，每层 2 分钟出结论
```

### 第一层：CPU

```bash
# ✅ 总览
uptime                    # load / 核数 > 1 = 饱和；看 1/5/15 分钟趋势
vmstat 1 5                # 关注：r（运行队列）> 核数 = CPU 不够；us+sy vs wa
mpstat -P ALL 1 3         # 单核打满（%usr 100%）而其他核闲着 = 单线程瓶颈

# top 里的关键信息
# %us 高 → 应用计算密集（业务逻辑/序列化/GC）
# %sy 高 → 内核态开销大（系统调用频繁/上下文切换）
# %wa 高 → 等 IO（转第三层）
# %st 高 → 虚拟机被宿主偷 CPU（云上超卖，找云厂商）
```

```bash
# 上下文切换过多（sy 高的常见原因）
vmstat 1 5                 # cs 列：每秒上下文切换，几十万级 = 异常
pidstat -wt 1              # 看哪个线程在疯狂切换（cswch/s 自愿 / nvcswch/s 非自愿）
```

### 第二层：内存

```bash
# ✅
free -h                   # 看 available，别看 free（cache 不算占用）
cat /proc/meminfo | grep -E "MemAvailable|Swap|Slab"

# swap 使用 ≠ 问题；swap 持续增长 + si/so 非零 = 内存真不够
vmstat 1 5                # si（换入）so（换出）持续非零 → 内存不足，在抖

# OOM 排查（见 incident-response）
```

### 第三层：磁盘 IO

```bash
# ✅ 核心三板斧
iostat -xz 1 5
# 关注：
#   %util → 接近 100% = 磁盘饱和
#   await → 单次 IO 平均等待（ms）：SSD 应 <1ms，超 10ms 异常；机械盘 <20ms
#   r_await/w_await 分开看读写
#   aqu-sz → 队列深度，持续 >1 = 排队了

iotop -oP                 # 谁在读写（按进程）

# 经典坑：日志同步刷盘拖慢一切
# 写放大定位：dstat --disk --io
```

### 第四层：网络

```bash
# ✅ 带宽与错误
sar -n DEV 1 5            # rxkB/txkB 换算成 Mbps 对比带宽上限
# 云主机注意：突发带宽实例（t 系列）有 CPU/带宽积分限制

ss -s                     # 连接统计总览
ss -ant state time-wait | wc -l     # TIME_WAIT 过多 = 短连接问题

# 丢包与重传
nstat -az | grep -iE "retrans|loss"
netstat -s | grep -i retrans        # 重传率 >1% 说明网络质量差

# 网卡错误
ethtool -S eth0 2>/dev/null | grep -iE "err|drop"
```

## 应用层慢的定位（系统没问题，就是接口慢）

```
接口慢拆解（按耗时占比排查）：
1. 数据库（80% 的慢在这）：慢查询日志 → EXPLAIN → 加索引
2. 下游调用：HTTP/RPC 超时设置、串行调用改并行
3. 序列化/大对象：响应体多大？分页了吗？
4. GC：Full GC 频繁 → 堆内存/对象泄漏
```

```bash
# MySQL 慢查询（✅）
# my.cnf: slow_query_log=1, long_query_time=1, slow_query_log_file=/var/log/mysql/slow.log
mysqldumpslow -s c -t 10 /var/log/mysql/slow.log    # 按次数 Top10
pt-query-digest /var/log/mysql/slow.log | less       # 更专业的分析

# 实时看正在执行的
mysql -e "SELECT id,time,state,info FROM information_schema.processlist WHERE time>2 ORDER BY time DESC\G"
```

## 内核参数调优（高并发 Web 服务器基线）

```bash
# /etc/sysctl.d/99-web.conf（⚠️ 逐条理解后再上，sysctl --system 生效）

# --- 连接队列 ---
net.core.somaxconn = 65535              # listen  backlog：高并发必调（默认 128 会丢连接）
net.ipv4.tcp_max_syn_backlog = 65535    # 半连接队列（防 SYN  flood 时尤其重要）
net.core.netdev_max_backlog = 65535     # 网卡收包队列

# --- TIME_WAIT ---
net.ipv4.tcp_tw_reuse = 1               # 客户端侧复用（服务端开了没用）

# --- 端口范围（本机作为客户端发起大量连接时） ---
net.ipv4.ip_local_port_range = 1024 65535

# --- 缓冲区（万兆网/高吞吐） ---
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216

# --- 文件句柄（配合 limits.conf） ---
fs.file-max = 2097152
fs.nr_open = 2097152

# --- 内存 ---
vm.swappiness = 10                      # 数据库服务器调低（能不 swap 就不 swap）
# 数据库/延迟敏感服务：干脆关 swap（swap 引起的毛刺比 OOM 更难查）
```

**配套 limits**（`/etc/security/limits.conf`）：

```
* soft nofile 655350
* hard nofile 655350
```

**调优纪律**：
1. 一次只改一个参数，改完观察——全改了出问题不知道是谁的锅
2. 写进 `/etc/sysctl.d/` 才持久；`sysctl -w` 重启就丢
3. 每条的默认值先记下来（回滚用）

## Java 应用速查（运维视角）

```bash
# GC 是否正常（✅ 不改任何东西）
jstat -gcutil <pid> 1s 10
# 关注：FGC（Full GC 次数）持续增长、FGCT（总耗时）大、O（老年代占用）逼近 100%

# 堆 dump（⚠️ 会停顿，低峰做）
jmap -dump:live,format=b,file=/tmp/heap.hprof <pid>

# 线程数爆炸
jstack <pid> | grep -c "java.lang.Thread.State"
```

## 性能分析工具箱速查

| 需求 | 工具 | 一句话 |
|---|---|---|
| CPU 火焰图 | perf | `perf top -p <pid>` 快速看热点 |
| 系统全景 | dstat / glances | 一个界面看全部 |
| 历史回溯 | sar | `/var/log/sa/` 里存着过去每一天 |
| IO 到文件 | blktrace | 块设备级追踪 |
| 系统调用 | strace | `strace -c -p <pid>` 统计调用分布 |
| eBPF 全家桶 | bcc-tools | `opensnoop`/`tcplife`/`biolatency` |
