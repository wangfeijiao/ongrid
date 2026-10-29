# 覆盖度与「未覆盖清单」说明

> 读这份文档的时机：报告里出现 `COVERAGE_GAP` / `PARSE_LOSS` / `AGENT_NO_OUTPUT`，
> 或者用户问「为什么这一项没查」「这个结论可信吗」。

## 为什么要有这一节

安全体检最危险的失败模式不是"漏检"，而是**漏检了却让人以为查过了**。
一份报告如果只写"发现 3 个问题"，读者会默认"其余都正常"，而事实可能是
"其余根本没读到"。

所以本技能把「没查到什么」当成一等公民，和「发现了什么」并列呈现。

---

## 三种"不确定"，含义完全不同

| 标记 | 含义 | 该怎么理解 |
|---|---|---|
| 未出现该条 Finding | **已检查且通过** | 可以认为这一项是好的 |
| `COVERAGE_GAP` 列出的键 | **根本没采到数据** | 无法判定，**不等于通过** |
| `PARSE_LOSS` | 采到了但解析失败 | 传输链路有问题，个别事实可能缺失 |
| `AGENT_NO_OUTPUT` | 一条数据都没拿到 | 本次体检**不能下任何结论** |

---

## 常见的"采不到"以及原因

### Windows

| 缺失项 | 原因 | 怎么补 |
|---|---|---|
| Security 事件日志 | 需要管理员 + 需先开启审计策略 | 以管理员运行；先执行 `auditpol` 开启审计 |
| Defender 状态 | 装了第三方杀软，Defender 被接管/停用 | 正常现象；改用第三方杀软的控制台核查 |
| `Get-MpThreatDetection` | 同上，或历史记录已被清理 | —— |
| 本地用户列表 | 域环境下的权限限制 | 用域管理工具核查 |
| 服务路径 | 部分服务需 SYSTEM 权限 | 以管理员运行 |

### Linux / UNIX

| 缺失项 | 原因 | 怎么补 |
|---|---|---|
| `sec.empty_pw` / `sec.uid0` 之外的 shadow 内容 | 非 root 读不到 `/etc/shadow` | 用 root 或 sudo 重跑 |
| `sec.fw.rules`（iptables） | 非 root 读不到规则 | 用 root 重跑，或 `sudo iptables -S` |
| `perf.*`（/proc 相关） | **非 Linux 系统**（AIX / Solaris / BSD / macOS） | 这些系统没有 `/proc`；按下方手工清单核查 |
| `log.*` | 没有 systemd（老系统 / 容器） | 改查 `/var/log/messages`、`/var/log/syslog` |
| `app.units` | 同上 | 用 `service --status-all` 或 `chkconfig --list` |
| `sec.updates.pending` | 包管理器不在 PATH 或锁被占用 | 手工执行 `yum check-update` / `apt list --upgradable` |

### 远程模式特有

| 现象 | 原因 | 处置 |
|---|---|---|
| `AGENT_NO_OUTPUT` | SSH 连不上 / 认证失败 / 目标无 `sh` | 手工验证：`ssh user@host 'sh -s' < scripts/linux_agent.sh` |
| 只有少量键 | 目标是非 Linux（无 /proc、无 systemd） | 属正常降级；按手工清单核查 |
| `PARSE_LOSS` | 目标无 `base64` 也无 `openssl`，降级为明文且值含换行 | 装 `coreutils`；或忽略（解析器已尽力还原） |
| 探针超时 | 目标磁盘慢 / 目录巨大（`find /` 耗时） | 加大 `--timeout`，或去掉 `--deep` |

---

## 非 Linux 系统的手工核查清单

AIX / Solaris / HP-UX / BSD / macOS 上，探针能拿到的部分有限。以下项需要手工执行：

### 账户与提权

```sh
# 多个 UID=0
awk -F: '$3==0 {print $1}' /etc/passwd
# 空口令（BSD/macOS 的 shadow 位置不同）
sudo vipw -s          # Solaris/BSD
sudo dscl . list /Users   # macOS
# sudo 免密
grep -r NOPASSWD /etc/sudoers /etc/sudoers.d/ 2>/dev/null
```

### 网络与监听

```sh
netstat -an | grep -i listen
lsof -i -P -n | grep LISTEN
# Solaris
netstat -an -f inet
```

### 持久化

```sh
crontab -l; cat /etc/crontab; ls -la /var/spool/cron/crontabs/
# AIX
ls -la /var/spool/cron/crontabs/ /etc/inittab
grep -v '^#' /etc/inittab                       # AIX 的启动项在这里
# Solaris
svcs -a | grep online                            # SMF 服务
inetadm                                          # inetd 托管服务
# macOS
launchctl list
ls -la /Library/LaunchDaemons /Library/LaunchAgents ~/Library/LaunchAgents
```

### 性能

```sh
# AIX
vmstat 1 5; iostat -Dl 1 5; topas
# Solaris
vmstat 1 5; iostat -xzn 1 5; prstat -Z 1 5
# macOS
top -l 3 -s 1; iostat -d 1 5
```

### 日志

```sh
# AIX
errpt -a | head -60                              # AIX 的硬件/系统错误日志
# Solaris
dmesg; /usr/bin/msgadm --list
# macOS
log show --last 1h --predicate 'messageType == error'
```

---

## 判读结论时的规范

给用户汇报时，请遵守以下口径：

1. **不要说"全面检查完毕，没有发现问题"**。
   正确说法：「在**已覆盖的 N 项**中未发现问题；另有 M 项因权限/平台原因未能检查，
   清单见报告。」

2. **`COVERAGE_GAP` 不为空时，必须主动说明缺了什么、为什么缺、怎么补**。

3. **`AGENT_NO_OUTPUT` 时，直接说"本次没拿到数据，无法给出任何结论"**，
   并给出可复现的排查命令。不要用"看起来还行"糊过去。

4. **降级模块（`degraded_modules`）要单独提一句**：
   「XX 模块采集降级，其结论可能不完整」。

---

## 提升覆盖度的手段

| 目标 | 做法 |
|---|---|
| 完整覆盖 Windows 安全项 | 以管理员身份运行；先开启审计策略 |
| 完整覆盖 Linux 账户与防火墙 | 用 root 或 sudo 权限的账号巡检 |
| 覆盖远程主机 | 配置 SSH 密钥认证（免交互，最稳） |
| 覆盖持久化与内存马 | 加 `--deep` |
| 覆盖更长的日志窗口 | `--log-hours 168`（7 天） |
| 覆盖非 Linux 系统 | 按上面的手工清单执行，并把结果手工补进结论 |
