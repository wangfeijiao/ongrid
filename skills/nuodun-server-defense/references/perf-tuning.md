# 性能优化手册（Windows / Linux）

> 读这份文档的时机：报告里出现 `DISK_BOTTLENECK` / `MEM_PRESSURE` / `LOAD_HIGH` /
> `CPU_SATURATED` / `SYSCTL_*` / `POWER_BALANCED` / `SVC_*_RUNNING` 等条目时。

## 铁律：先定位，再动手

性能问题的 90% 是「打偏了」——CPU 跑满其实是 IO 等待，内存告急其实是某进程泄漏。
**任何优化动作之前，必须先拿到证据**。没有证据的"优化"是赌博。

排查顺序永远是：**磁盘 → 内存 → CPU → 网络 → 应用**。原因：磁盘是最慢的一环，
磁盘饱和会伪装成 CPU 高、内存少、响应慢，把人带偏。

---

## 磁盘

### 判读指标

| 指标 | 健康 | 告警 | 说明 |
|---|---|---|---|
| Current Disk Queue Length | < 2×主轴数 | ≥ 2 | 平均排队请求数（Windows） |
| %Disk Time | < 60% | > 100% | 超过 100% 表示队列堆积 |
| Avg. Disk sec/Read | < 10ms | > 20ms | 单次读延迟 |
| `iostat -x` 的 `%util` | < 60% | > 80% | 设备繁忙度（Linux） |
| `iostat -x` 的 `await` | < 10ms | > 20ms | 平均 IO 等待 |

### Windows 采样

```powershell
Get-Counter -Counter `
  '\PhysicalDisk(_Total)\Current Disk Queue Length',`
  '\PhysicalDisk(_Total)\% Disk Time',`
  '\PhysicalDisk(_Total)\Avg. Disk sec/Read',`
  '\PhysicalDisk(_Total)\Avg. Disk sec/Write' `
  -SampleInterval 1 -MaxSamples 10
```

### Linux 采样

```bash
iostat -x 1 10                # 需 sysstat 包
iotop -oPa                    # 按累计 IO 排序，找写入大户
pidstat -d 1 5                # 按进程看读写
```

### 常见根因与处置

| 根因 | 证据 | 处置 |
|---|---|---|
| 机械盘随机 IOPS 不足 | 队列高但吞吐低，延迟忽高忽低 | **换 SSD**，这是唯一根治。过渡期把高频读写目录迁到独立盘 |
| 日志/备份与业务抢 IO | 备份时段 IO 飙高 | 错开到业务低峰；日志落到独立卷 |
| 杀毒实时扫描 | 扫描进程 CPU+IO 双高 | 给业务目录加排除（前提是目录可信） |
| 页面文件/swap 抖动 | 见「内存」节 | 压内存，或把 pagefile 换到独立盘 |
| 云盘 IOPS 被打满 | 云监控显示 IOPS 触顶 | 升配云盘（ESSD 的 PL 等级）或加带宽 |

### 磁盘空间治理

```powershell
# Windows：找出目录占用大户
Get-ChildItem C:\ -Directory | ForEach-Object {
  $s = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
  [pscustomobject]@{ Path=$_.FullName; GB=[math]::Round($s/1GB,2) }
} | Sort-Object GB -Descending | Select-Object -First 20
```
```bash
# Linux
du -xh --max-depth=1 / | sort -rh | head -20
journalctl --disk-usage                 # 只查大小
journalctl --vacuum-size=500M           # 收缩到 500M
journalctl --vacuum-time=14d            # 或只留 14 天
```

**inode 耗尽**（`df -h` 有空间但写不进去）：
```bash
df -i
# 找小文件重灾区
find / -xdev -printf '%h\n' 2>/dev/null | sort | uniq -c | sort -rn | head -20
```

---

## 内存

### 判读指标

| 指标 | 健康 | 告警 |
|---|---|---|
| 已用比例 | < 85% | ≥ 92% |
| Pages/sec（Windows） | < 200 | ≥ 1000 |
| `vmstat` 的 si/so | 0 | 持续 > 0 |
| Available MBytes | > 20% 总量 | < 10% |

**注意**：Linux 的"空闲内存少"往往是正常的 —— 页缓存会主动占用空闲内存。
要看 `MemAvailable`（`/proc/meminfo`），不是 `MemFree`。

### 找内存大户

```powershell
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 20 `
  Name, Id, @{n='MB';e={[math]::Round($_.WorkingSet64/1MB,1)}}
```
```bash
ps -eo pid,rss,comm,args --sort=-rss | head -20
```

### 判断是"正常吃内存"还是"泄漏"

对嫌疑进程连续采样 RSS，看是否**单调上升且不回落**：

```bash
for i in $(seq 1 60); do
  ps -o rss= -p <PID> >> /tmp/rss.log; sleep 60
done
```
```powershell
1..60 | ForEach-Object {
  "$(Get-Date -f HH:mm) $((Get-Process -Id <PID>).WorkingSet64/1MB)" |
    Out-File -Append D:\rss.log
  Start-Sleep 60
}
```

正常服务在 GC 后内存应回落；只涨不落就是泄漏。

### 页面文件 / swap

```powershell
# 设为系统自动管理（推荐）
$cs = Get-CimInstance Win32_ComputerSystem
Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$true}
# 或固定大小：建议 1~1.5 × 物理内存，且放在独立盘
```
```bash
# 新建 swap（内存紧张时的缓冲）
fallocate -l 4G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
# 让内核在内存充足时别换页
sysctl -w vm.swappiness=10
```

⚠️ 云主机上大量使用 swap 会严重拖慢性能。swap 是**缓冲**，不是解决方案。

### 关键服务的 JVM 内存

```bash
# 显式设定堆与堆外，放在 setenv.sh，不要改 catalina.sh
export CATALINA_OPTS="-Xms2g -Xmx2g \
  -XX:MaxDirectMemorySize=1g \
  -XX:+UseG1GC -XX:MaxGCPauseMillis=200 \
  -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/data/dumps"
```

`Xms` 与 `Xmx` 必须相等 —— 否则堆会动态伸缩，带来额外 GC 与系统调用开销。

---

## CPU

### 判读

```bash
top -o %CPU          # 交互式
mpstat -P ALL 1 5    # 看是否单核打满（单线程瓶颈）
vmstat 1 5           # 看 r 列（运行队列）与 wa 列（IO 等待）
```
```powershell
Get-Counter '\Processor(_Total)\% Processor Time','\System\Processor Queue Length' `
  -SampleInterval 1 -MaxSamples 10
```

**关键区分**：
- `vmstat` 里 `us+sy` 高 → 真 CPU 瓶颈
- `wa` 高 → 是磁盘瓶颈，别去加 CPU
- `top` 里高 CPU 的是 `%st`（steal）→ 云主机被宿主机抢占，需提工单

### 常见"假业务"高 CPU

- **挖矿木马**：进程名伪装成 `kworker`/`systemd-*`，CPU 长期 90%+，外联矿池 IP。
  见 `references/malware-response.md`。
- **正则回溯**：日志里同一条 URL 无限重复时，正则引擎会指数级回溯。
- **GC 风暴**：JVM 频繁 Full GC。`jstat -gcutil <pid> 1000`，看 FGC 计数是否快速上涨。

---

## 电源与后台服务（Windows）

```powershell
powercfg /getactivescheme
# 高性能（服务器/常开主机）
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

# 关掉抢 IO 的后台服务（确认无依赖再关）
Stop-Service SysMain -Force;  Set-Service SysMain  -StartupType Disabled
Stop-Service WSearch -Force;  Set-Service WSearch  -StartupType Disabled
```

⚠️ 如果这台机器兼任文件服务器、依赖 Windows 搜索做索引，**不要**关 `WSearch`。

其它可裁剪项：
```powershell
# 视觉效果：最佳性能
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' `
  -Name VisualFXSetting -Value 2
# 关闭休眠（省下 hiberfil.sys，约等于物理内存大小）
powercfg /hibernate off
```

---

## 内核参数（Linux）

写入 `/etc/sysctl.d/99-ops-tuning.conf`，然后 `sysctl --system`（在线生效，无需重启）。

```ini
# 连接队列：高并发下默认值会让内核直接丢弃已完成握手的连接
net.core.somaxconn = 4096
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.ip_local_port_range = 10240 65000

# TIME_WAIT 复用：短连接多的服务必备
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_max_tw_buckets = 262144

# 大带宽场景（按需，4 倍于 BDP）
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216

# 文件句柄
fs.file-max = 1000000
fs.inotify.max_user_watches = 524288

# 内存脏页：写密集服务降低突发刷盘
vm.swappiness = 10
vm.dirty_ratio = 20
vm.dirty_background_ratio = 5
```

### 文件描述符上限（三处都要改，缺一处不生效）

```bash
# 1. 登录会话
cat > /etc/security/limits.d/99-ops.conf <<'EOF'
* soft nofile 65535
* hard nofile 65535
* soft nproc  65535
* hard nproc  65535
EOF

# 2. systemd 服务（关键！systemd 服务不读 limits.conf）
mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nDefaultLimitNOFILE=65535\n' > /etc/systemd/system.conf.d/99-ops-limits.conf
# 或针对单个服务：/etc/systemd/system/xxx.service 里加 LimitNOFILE=65535

# 3. 全局
sysctl -w fs.file-max=1000000
```

验证：`cat /proc/<PID>/limits | grep 'open files'` —— 看的是目标进程，不是当前 shell。

---

## 网络

```bash
ss -s                      # 连接状态汇总
ss -tan state time-wait | wc -l
ethtool -S eth0 | grep -i drop   # 网卡丢包
ip -s link show eth0             # 收发错误计数
```
```powershell
Get-NetTCPConnection -State TimeWait | Measure-Object
netstat -s | Select-String -Pattern 'errors|discarded'
```

云主机还要看**安全组/带宽峰值**：带宽打满会表现为丢包与延迟抖动，本机指标看着完全正常。

---

## 优化动作的验收标准

改完必须回读验证，否则等于没改：

```bash
sysctl net.core.somaxconn vm.swappiness
cat /proc/<服务PID>/limits | grep 'open files'
```
```powershell
powercfg /getactivescheme
(Get-Service SysMain).Status; (Get-Service SysMain).StartType
```
然后重跑 `cli.py audit --scope perf`，比较前后健康分与具体条目。
