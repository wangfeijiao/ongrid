# 日志分析与崩溃诊断手册

> 读这份文档的时机：报告出现 `REPEATED_BLUESCREEN` / `EVENT_41` / `EVENT_1001` /
> `EVENT_1000` / `JVM_CRASH_FILES` / `KERNEL_OOPS` / `OOM_KILL` / `SEGFAULT_REPEAT` /
> `IO_ERRORS` / `DUMP_DISABLED` / `DISK_UNHEALTHY` / `AUDIT_LOG_CLEARED` 等条目时。

## 排障的基本方法：沿时间线收敛

崩溃类问题的正确解法不是"猜最可能的原因"，而是：

```
1. 确定崩溃时刻（精确到秒）
2. 拉出该时刻前后 5 分钟的全部日志（系统 + 应用 + 驱动）
3. 找"第一个"异常事件（后面往往是它的连锁反应）
4. 提取崩溃模块 / 调用栈
5. 对照版本、补丁、近期变更
```

**关键认知**：日志里最后一条错误通常**不是**根因，是结果。
要找的是**最先**出现的那条异常。

---

## 蓝屏（BSOD）诊断

### 前置：先确认 dump 是开的

```powershell
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' |
  Select-Object CrashDumpEnabled, AutoReboot, DumpFile, MinidumpDir
```

| CrashDumpEnabled | 含义 |
|---|---|
| 0 | 不生成（**排障时最怕这个**） |
| 1 | 完整内存转储 |
| 2 | 内核内存转储 |
| 3 | 小内存转储（256KB，够用） |
| 7 | 自动内存转储（推荐） |

```powershell
# 开启小内存转储
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' `
  -Name CrashDumpEnabled -Value 3
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' `
  -Name AlwaysKeepMemoryDump -Value 1 -Type DWord
```

⚠️ **先确认 dump 文件所在盘有足够空间**。自动内存转储要占用接近物理内存的大小。
默认 `DumpFile` 是 `%SystemRoot%\MEMORY.DMP`；如果系统盘小，改到数据盘。

### 提取崩溃代码

```powershell
# 最近的蓝屏事件（BugCheck = 1001，意外关机 = 41 / 6008）
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001,41,6008} -MaxEvents 20 |
  Select-Object TimeCreated, Id, @{n='Msg';e={($_.Message -split "`n")[0]}}
```

事件 1001 的 Message 里带 **BugCheck 代码**和**四个参数**，例如：

```
The computer has rebooted from a bugcheck.
The bugcheck was: 0x000000d1 (0x0000000000000018, 0x0000000000000002,
0x0000000000000000, 0xfffff8800a1b2c3d)
```

### 常见 BugCheck 代码对照

| 代码 | 名称 | 高频原因 |
|---|---|---|
| 0x000000D1 | DRIVER_IRQL_NOT_LESS_OR_EQUAL | **驱动**在错误 IRQL 访问内存 —— 最常见于第三方驱动 |
| 0x0000007E | SYSTEM_THREAD_EXCEPTION_NOT_HANDLED | 未处理异常，多为驱动或内存 |
| 0x00000050 | PAGE_FAULT_IN_NONPAGED_AREA | 内存条故障 / 驱动 / 磁盘损坏 |
| 0x0000009F | DRIVER_POWER_STATE_FAILURE | 电源管理驱动（休眠/睡眠相关） |
| 0x0000003B | SYSTEM_SERVICE_EXCEPTION | 驱动或系统服务 |
| 0x0000001A | MEMORY_MANAGEMENT | **内存硬件**问题 |
| 0x00000124 | WHEA_UNCORRECTABLE_ERROR | **硬件**（CPU/内存/主板报错） |
| 0xC000021A | STATUS_SYSTEM_PROCESS_TERMINATED | 关键进程被终止（安全软件冲突常见） |

### 分析 dump

**方法一：WinDbg（最权威）**

```powershell
# 安装 WinDbg（winget 或 SDK）
winget install Microsoft.WinDbg
# 打开 dump 后执行
!analyze -v
# 关注输出里的：
#   MODULE_NAME / IMAGE_NAME  → 出问题的模块
#   FAILURE_BUCKET_ID         → 归类
#   STACK_TEXT                → 调用栈
```

**方法二：不装工具也能快速定位**

```powershell
# dump 文件里的驱动名单，对比近期变更
Get-ChildItem C:\Windows\Minidump\*.dmp | Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name, LastWriteTime, Length
```

再配合驱动版本核对：

```powershell
# 列出所有第三方驱动（非微软签名）
Get-CimInstance Win32_SystemDriver | Where-Object { $_.State -eq 'Running' } |
  ForEach-Object {
    $p = $_.PathName -replace '\\\?\?\\',''
    $s = Get-AuthenticodeSignature $p -ErrorAction SilentlyContinue
    if ($s.SignerCertificate.Subject -notmatch 'Microsoft') {
      [pscustomobject]@{ Name=$_.Name; Path=$p; Signer=$s.SignerCertificate.Subject }
    }
  }
```

### 定位到驱动之后

```powershell
# 看驱动文件版本与日期
Get-Item C:\Windows\System32\drivers\<可疑驱动>.sys |
  Select-Object Name, LastWriteTime, @{n='Ver';e={$_.VersionInfo.FileVersion}}
```

**处置优先级**：
1. **升级**到厂商最新版（虚拟化驱动、存储驱动、安全软件驱动）
2. 若新版仍崩，**降级**到上一个稳定版本
3. **卸载**非必要的第三方驱动（尤其是安全软件、虚拟化增强包、老旧外设驱动）
4. 硬件方向：内存检测（`mdsched.exe`）、磁盘 SMART、CPU/主板 WHEA 错误计数

> **经验**：0xD1 / 0x7E 这类问题，90% 能在"第三方驱动版本冲突"里找到答案，
> 其中 **安全软件驱动 + 虚拟化驱动（VirtIO / VMware Tools）** 是最高频的组合。

---

## 硬件健康

```powershell
Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus, OperationalStatus, Size
Get-StorageReliabilityCounter -PhysicalDisk (Get-PhysicalDisk) |
  Select-Object DeviceId, Temperature, ReadErrorsTotal, WriteErrorsTotal, Wear
```
```bash
smartctl -a /dev/sda | grep -E 'Reallocated|Pending|Uncorrect|Wear|Temperature|Power_On'
```

**判据**：
- `Reallocated_Sector_Ct` > 0 且持续增长 → 准备换盘
- `Current_Pending_Sector` > 0 → 已有坏块
- `HealthStatus` ≠ Healthy → **立刻备份**

---

## JVM 崩溃（hs_err_pid）

### 关键认知

**JVM 级崩溃不是普通的内存不足。** 普通堆内存不足会抛 `OutOfMemoryError` 并被捕获，
进程不会直接消失。产生 `hs_err_pid*.log` 说明 JVM **被操作系统或自身致命错误终止**了。

### 读 hs_err 的重点

```
# 头部
# A fatal error has been detected by the Java Runtime Environment:
#
#  SIGSEGV (0xb) at pc=0x00007f..., pid=12345, tid=0x00007f...
#                                        ↑ 信号类型：SIGSEGV / SIGBUS / SIGABRT

# Problematic frame:            ← 最重要的一行
# C  [libnetty_transport_native_epoll_x86_64.so+0x...]
# 或
# V  [libjvm.so+0x...]

# Memory:                        ← 看内存分布
# Heap:  ... 
# Non-heap: ...
# 以及是否存在 native memory 耗尽的迹象

# 结尾的 dynamic libraries 与 vm_info 给出精确版本
```

### 堆外内存问题（云环境最常见的 JVM 崩溃）

**症状**：进程被 OOM Killer 杀掉或 SIGSEGV，堆内存看起来很空。

| 根因 | 识别 | 处置 |
|---|---|---|
| DirectByteBuffer 泄漏 | hs_err 里 native memory 高；`jcmd <pid> VM.native_memory` | 检查 Netty / Lettuce / NIO 的池化对象是否释放；限制 `-XX:MaxDirectMemorySize` |
| **mmap 计数超限** | hs_err 里有 `Out of native memory` 或 mmap 失败 | 云环境常见：`vm.max_map_count` 默认 65530 被打满。`sysctl -w vm.max_map_count=262144` |
| 线程栈耗尽 | hs_err 里有 `unable to create new native thread` | 限制线程池大小；核对 `ulimit -u` |
| JNI 段错误 | Problematic frame 指向第三方 `.so` | 升级或替换该 native 库 |

**Lettuce（基于 Netty 的 Redis 客户端）典型泄漏形态**：
每次创建连接却未正确释放，堆外的 direct buffer 持续增长直到 mmap 耗尽。
排查：

```bash
# 观察 mmap 数量增长
watch -n 5 'wc -l /proc/<PID>/maps'

# 或在 JVM 内看 direct buffer 池
jcmd <PID> VM.native_memory summary
jcmd <PID> VM.native_memory detail 2>/dev/null | grep -i 'direct\|mmap'
```

建议的兜底参数：

```bash
-XX:MaxDirectMemorySize=1g
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/data/dumps
-XX:ErrorFile=/data/dumps/hs_err_%p.log
-XX:+ExitOnOutOfMemoryError        # 配合进程守护，让 OOM 立即重启而不是僵死
```

---

## OOM（Linux）

```bash
dmesg -T | grep -iE 'oom|killed process'
journalctl -k --since '24 hours ago' | grep -i oom
```

**注意区分两类 OOM**：

| 类型 | 特征 | 处置 |
|---|---|---|
| 内核 OOM Killer | `dmesg` 有 `Killed process` | 系统级内存不足；收紧各服务上限 |
| cgroup 内存限制 | 容器/systemd 服务的 `MemoryMax` 触发 | 调整该服务的限制或排查其泄漏 |

**关键动作**：找出被杀进程的 RSS 增长曲线，判断是**突增**（正常）还是**单调上升**（泄漏）。

---

## 段错误与 core dump

```bash
coredumpctl list
coredumpctl info <PID>
coredumpctl gdb <PID>      # 进 gdb 看调用栈

# 无 systemd-coredump 时
gdb /path/to/binary /var/crash/core.xxx
(gdb) bt full
```

**高频原因**：glibc/openssl 等基础库升级后与旧二进制不兼容；
自编译程序的内存 bug；第三方 `.so` 版本冲突。

---

## I/O 错误（最紧急的一类）

```
blk_update_request: I/O error, dev vda, sector 123456
EXT4-fs error (device vda1): ext4_find_entry: ...
Buffer I/O error on dev sda1, logical block 654321
```

**含义**：块设备返回了硬件级错误。这不是软件问题。

**处置顺序（不要颠倒）**：

1. **立刻备份数据**（在故障盘还活着的时候）
2. `dmesg -T` 看完整上下文，确认是哪个设备、哪个扇区
3. `smartctl -a /dev/vda` 看 SMART
4. 云主机**提工单**核查底层存储（云盘故障需要平台侧处理）
5. 文件系统修复只能在**数据已备份**之后做：`fsck -y /dev/vda1`（需卸载）

⚠️ **不要在未备份的情况下跑 fsck** —— 它可能把还能读的数据改得更糟。

---

## 审计日志被清空（入侵红线）

```powershell
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=1102} -MaxEvents 10 |
  Select-Object TimeCreated, @{n='Msg';e={($_.Message -split "`n")[0]}}
```
```bash
grep -E 'Cleared|truncated' /var/log/auth.log /var/log/secure
# 或者看日志文件本身有没有断裂（时间戳跳变）
```

**这是入侵的强信号，不是运维失误**（运维清理日志会走归档流程）。
一旦确认，直接进入事件响应流程，见 `references/malware-response.md`。

---

## 日志查看速查

```powershell
# 系统 + 应用，仅错误与严重，最近 24 小时
$since = (Get-Date).AddHours(-24)
Get-WinEvent -FilterHashtable @{LogName=@('System','Application'); Level=@(1,2); StartTime=$since} |
  Group-Object Id, ProviderName |
  Sort-Object Count -Descending |
  Select-Object -First 20 Count, Name
```

```bash
journalctl -p err --since '24 hours ago' --no-pager | tail -100
journalctl -u <service> --since '1 hour ago' -f
journalctl --since "2026-10-04 13:00" --until "2026-10-04 13:10"    # 崩溃窗口
```

**按频次聚合**比逐条读更有效 —— 出现 500 次的那条错误，才是你要解决的。
