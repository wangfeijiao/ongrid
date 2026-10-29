# 故障应急手册（🚨 最高优先级）

> 原则：**先恢复，后追查**。用户在故障现场时是慌的，先给止血命令，再给定位步骤。

## 应急总流程

```
接警 → 定界（影响面）→ 止血（恢复服务）→ 定位（根因）→ 修复 → 复盘
```

**定界三问**（30 秒内回答）：
1. 全部用户还是部分用户受影响？→ 全部：入口层（DNS/LB/网关）；部分：单实例/单机房
2. 所有功能还是单一功能？→ 所有：基础设施；单一：应用或依赖
3. 什么时候开始的？→ 对齐发布记录/变更记录/流量曲线

## 场景 1：CPU 100%

```bash
# ① 找进程（✅ 只读）
top -bn1 | head -20
ps aux --sort=-%cpu | head -10

# ② 找线程（Java/多线程程序关键一步）
top -H -p <pid>                     # 或 ps -Lfp <pid>

# ③ Java 应用：线程栈定位（⚠️ jstack 会短暂停顿）
printf "%x\n" <tid>                 # 线程号转 16 进制
jstack <pid> | grep -A 30 <nid16>   # 找到对应线程栈

# ④ 非 Java：看进程在干什么
strace -p <pid> -c -f 2>&1 | head   # ⚠️ 有性能开销，采样几秒就 Ctrl+C
cat /proc/<pid>/stack               # 内核栈（需 root）
```

**常见根因**：死循环/正则回溯/频繁 Full GC/序列化大对象/加密解密突增。
**止血**：重启该实例（先摘流量！）→ 保留现场（jstack/heap dump 先抓一份再重启）。

## 场景 2：内存打满 / OOM

```bash
# ① 确认 OOM Killer 是否出过手（✅）
dmesg -T | grep -i -E "oom|killed process"
journalctl -k | grep -i oom          # systemd 系统

# ② 找内存大户（✅）
ps aux --sort=-%mem | head -10
smem -rtk | head                     # 更准（含 PSS），需安装

# ③ 看内存去向
cat /proc/meminfo | head -10        # 关注 Slab（内核缓存）、PageTables
free -h                              # available 才是真正可用
```

**注意**：`free` 里 buff/cache 高≠内存不足——Linux 会把空闲内存拿去做页缓存，`available` 才是真话。

**止血**：重启泄漏进程（先 dump）；临时 `echo 3 > /proc/sys/vm/drop_caches` 清页缓存（🚫 生产慎用，会引发 IO 突刺）。

## 场景 3：磁盘满了

```bash
# ① 找哪个分区（✅）
df -h
df -i                                # ⚠️ inode 耗尽也报 No space left！小文件多的机器常中招

# ② 找大目录（✅ 只读但 IO 开销大，生产环境慢盘慎用）
du -sh /* 2>/dev/null | sort -rh | head -10
du -sh /var/log/* | sort -rh | head

# ③ 经典坑：文件删了空间没释放（进程还持有句柄）
lsof +L1                             # 找 deleted 但仍被打开的文件
# 解决：重启持有进程，或 truncate 而非 rm：
: > /var/log/big.log                 # ✅ 清空但保留句柄，不用重启
```

**止血顺序**：清日志 → 清临时文件 → 清 docker（`docker system prune`）→ 扩容。

## 场景 4：连接数打满

```bash
# ① 看连接状态分布（✅）
ss -s
ss -ant | awk '{print $1}' | sort | uniq -c | sort -rn

# ② TIME_WAIT 海量（>1万）：多是短连接高频调用
ss -ant state time-wait | wc -l
# 根因：应用没用连接池/HTTP Keep-Alive。临时缓解：
sysctl -w net.ipv4.tcp_tw_reuse=1    # ⚠️ 客户端侧有效

# ③ 文件句柄耗尽（too many open files）
cat /proc/<pid>/limits | grep "open files"
ls /proc/<pid>/fd | wc -l            # 当前用了多少
# 临时调大：ulimit -n 65535（重启失效）；永久：/etc/security/limits.conf
```

## 场景 5：网站打不开（分层定位法）

```
浏览器报错？
├─ DNS 解析失败      → dig/nslookup，查 DNS 配置
├─ 连接超时          → 网络层：ping → mtr → telnet 端口 → 防火墙/安全组
├─ 连接被拒          → 服务没监听：ss -tlnp | grep :80
├─ 502 Bad Gateway   → Nginx 在，后端挂了：查后端进程+upstream 日志
├─ 504 Gateway Timeout → 后端在但慢：查后端线程池/慢查询
└─ 5xx 应用错误       → 应用日志
```

```bash
# 快速验证链路（✅）
curl -v http://target/healthz 2>&1 | head -30    # 看完整握手过程
curl -o /dev/null -s -w "DNS:%{time_namelookup} TCP:%{time_connect} TLS:%{time_appconnect} 首字节:%{time_starttransfer} 总:%{time_total}\n" https://example.com
```

## 场景 6：数据库被打爆 → 雪崩

```
典型链路：慢查询 → 连接池耗尽 → 应用线程阻塞 → 上游超时重试 → 流量放大 → 雪崩
```

**止血三板斧**：
1. **限流**：网关层把非核心流量砍掉（保核心接口）
2. **降级**：关掉非核心功能（推荐位/统计/评论）
3. **杀慢查询**：`SELECT * FROM information_schema.processlist WHERE time > 10;` → `KILL <id>;`（⚠️ 确认不是 DDL/大事务再杀）

## 场景 7：负载高但 CPU 不高（Load ≠ CPU）

`load average` 包含 **D 状态（不可中断睡眠）进程**——多数是 IO 等待：

```bash
# ✅ 看 D 状态进程
ps aux | awk '$8 ~ /^D/ {print}'
# 看 IO 压力
iostat -xz 2 3                       # %util 接近 100 = 磁盘瓶颈
iotop -oP                            # 谁在读写
```

常见根因：NFS 挂了/磁盘故障/日志同步写/磁盘队列拥塞。

## 止血手段代价表（按代价从小到大选）

| 手段 | 代价 | 适用 |
|---|---|---|
| 重启单实例 | 秒级抖动 | 单实例异常，集群健康 |
| 扩容 | 钱 | 流量型压力 |
| 回滚 | 新功能退回 | 变更引起的故障 |
| 限流/降级 | 部分功能受损 | 系统性过载 |
| 切机房/切备 | 复杂、有风险 | 机房级故障 |
| 重启数据库 | 🚫 最后手段 | 万不得已 |

## 复盘模板（故障报告）

```markdown
## 故障概述（时间、影响面、损失）
## 时间线（精确到分钟：发现→定位→止血→恢复）
## 根因（5 Why 追到技术根因，不停在"人操作失误"）
## 做得好的 / 做得差的
## 改进项（每条带负责人和截止时间：监控补盲/流程/架构）
```

> 复盘文化：**对事不对人**。惩罚个体的结果是下一次没人敢上报故障。
