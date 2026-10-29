# 日志与监控：出问题时先看这里

**Linux 专属命令**：`journalctl`、`dmesg` 系列的 OOM 查法、`/var/log/syslog`。macOS 没有这些，替代是 `✅ log show --last 1h --predicate 'process == "nginx"'`（系统日志）与「控制台」App。

## 一、日志都在哪

```bash
✅ ls -lh /var/log/                    # 总览
✅ du -sh /var/log/* | sort -rh | head # 谁在吃日志空间
```

| 路径 | 内容 |
|---|---|
| `/var/log/syslog` | Debian/Ubuntu 系统综合日志（**CentOS 上是 `/var/log/messages`**） |
| `/var/log/secure` | SSH / 认证日志，Debian/Ubuntu 用 `auth.log`，CentOS 用 `secure` |
| `/var/log/nginx/` | `access.log`（访问）与 `error.log`（错误） |
| `/var/log/mysql/` | MySQL 日志目录（错误日志位置随发行版不同） |
| `/var/log/journal/` | systemd 二进制日志（在 `/run/log/journal` 时**重启即丢**） |
| `/var/log/dmesg` | 内核启动与硬件信息 |

**坑点**：发行版名字体系不同，不确定就先 `✅ ls /var/log/` 看实际有哪些文件，别照搬别人的路径。

## 二、journalctl 实战

```bash
✅ journalctl -u nginx -n 100 --no-pager   # 指定服务 + 最近 100 行 + 不走分页器
✅ journalctl -u nginx -f                  # 实时跟随（Ctrl+C 退出）
✅ journalctl --since "10 min ago"         # 时间切片追现场
✅ journalctl --since "2026-09-26 09:00" --until "2026-09-26 10:00" -u mysql
✅ journalctl -u nginx -o json             # JSON 输出，便于脚本处理
✅ journalctl -k                           # 内核日志（OOM、磁盘报错看这里）
✅ journalctl -b -1                        # 上一次启动的日志（崩溃后重启必看）
```

**为什么必加 `--no-pager`**：`journalctl` 默认接 `less`，管道给 `grep` 时会被分页器卡住，看起来像「什么都没输出」。脚本与排查一律加它。

**坑点 1**：日志「突然找不到」通常是被轮转了，历史在 `access.log.1` 这类文件里：`✅ ls -lh /var/log/nginx/ | head`。

**坑点 2**：默认持久化在 `/run`，**重启后历史日志丢失**。要长期留痕在 `/etc/systemd/journald.conf` 设 `Storage=persistent` 再 `⚠️ systemctl restart systemd-journald`（配置项随版本不同，**以当前 `man journald.conf` 为准**）。

## 三、Nginx 访问日志与错误日志

默认 `combined` 格式的一行：

```
203.0.113.88 - - [26/Sep/2026:10:00:01 +0800] "GET /api/user HTTP/1.1" 200 532 "-" "curl/7.88.1"
```

依次是**客户端 IP** → **远程用户** → **时间** → **请求方法与路径** → **状态码** → **响应字节数** → **来源页** → **UA**。错误日志行结构是 `时间 [级别] [连接信息] 错误`，级别 `info/warn/error/crit`。

**认这几个错误串就知道大致病因**：

| 错误串 | 含义 |
|---|---|
| `no live upstreams` | 后端全挂，502 的直接原因 |
| `connect() failed (111: Connection refused)` | 后端没监听或端口写错 |
| `permission denied` | 文件/目录权限，表现为 403 |
| `upstream timed out` | 后端处理超时，表现为 504 |
| `SSL Handshake has failed` | 证书与私钥不匹配、协议不兼容 |

```bash
✅ awk '{print $9}' access.log | sort | uniq -c | sort -rn | head     # 按状态码统计（最常用）
✅ awk '{print $1}' access.log | sort | uniq -c | sort -rn | head     # 按客户端 IP 统计
✅ grep '" 5[0-9][0-9] "' access.log | tail -20                       # 抽最近 20 条 5xx
✅ awk '{print $4" "$5}' access.log | sort | uniq -c | sort -rn | head # 按时间切片找访问高峰
```

**为什么 `$9` 是状态码**：combined 格式以空格分隔，第 9 个字段固定是状态码。一旦日志格式改成 `main` 或自定义写法，编号就变了——**先看一行真实日志再数字段**。

## 四、快速定位错误：几条管道

```bash
✅ grep -i error /var/log/nginx/error.log | tail -20
✅ tail -100 /var/log/syslog | grep -iE 'fail|error|refused|denied'
✅ tail -f /var/log/nginx/access.log | grep --line-buffered " 500 "
✅ zgrep -i error /var/log/nginx/error.log.2.gz | tail -20      # 已压缩的旧日志
✅ awk '$4 >= "[26/Sep/2026:10:00:00" && $4 <= "[26/Sep/2026:10:10:59"' access.log
```

**为什么**：`grep -i error | tail` 是成本最低的「现在坏没坏」判断；定位到时间再用 `awk` 切片看那几分钟，避免在几 GB 日志里翻页。**坑点**：`tail -f | grep` 会因管道缓冲看起来不动，加 `--line-buffered`，或反过来 `✅ grep -i error access.log | tail -f`。

## 五、磁盘：最容易被忽略的故障源

```bash
✅ df -h                                                 # 各挂载使用率（Size/Used/Avail/Use%）
✅ df -h /                                               # 只看根文件系统
✅ df -i                                                 # inode 使用率，不是空间
✅ du -sh /var/* 2>/dev/null | sort -rh | head           # 逐级找大目录
✅ find /var/log -type f -size +100M -exec ls -lh {} \;  # 找超过 100M 的日志
```

**inode 满的场景**：`df -h` 显示还有几十 GB，服务却报 `No space left on device`——原因是**小文件太多**（session、缓存碎片、队列）先把 inode 耗尽了。用 `df` 和 `df -i` 各看一眼，确认是空间问题还是 inode 问题。

**日志膨胀的应急处理**：

```bash
✅ find /var/log -type f -mtime +30 -name "*.log*"          # 先确认有哪些旧日志
⚠️ truncate -s 0 /var/log/nginx/access.log                  # 留句柄、立刻释放空间
⚠️ nginx -s reopen                                          # 让 Nginx 重新打开日志文件
```

**为什么不能直接 `rm`**：Nginx/MySQL 持有文件句柄，直接删除后仍在往已删的 inode 写，磁盘不释放、日志永久丢失。`truncate -s 0` 才是安全的「清空间」动作。硬要删旧的：

```bash
# ⚠️ 有副作用：先确认路径与影响范围，最好先备份
⚠️ find /var/log -type f -name "*.log" -mtime +30 -delete
```

（对应 @references/maintenance-tasks.md 的定时清理方案。）

## 六、内存与负载

```bash
✅ free -h            # total / used / free / shared / buff/cache / available
✅ free -h -s 3 -c 5  # 每 3 秒采样共 5 次，看内存是否持续上涨
✅ uptime              # load average
```

**各列含义**：`free` 是完全没被用的内存；`buff/cache` 是内核拿空闲内存做的磁盘缓存（**可回收**）；`available` 才是**真正还能给新程序用的**。

**坑点**：看到 `free` 只剩几十 MB 就报内存不足是错的——那多半是缓存。**只有 `available` 持续下跌并触发 OOM 才是真不足**。

**load average 怎么读**：三个数是过去 1/5/15 分钟的平均就绪进程数（含等 CPU 和等 I/O 的）。**持续超过 CPU 核数就是堵了**；但 load 高也可能是磁盘 I/O 卡住（配合 `iostat -x 1` 看 `await`），不等于 CPU 跑满。`top` 交互键：`P` 按 CPU 排、`M` 按内存排、`1` 展开各核、`q` 退出。

**swap 与 OOM**：swap 一旦使用说明内存不够；swappiness（默认 60）可调低，数据库类服务常见做法。OOM 查证：

```bash
✅ dmesg -T | grep -i -A2 'out of memory'       # OOM Killer 的击杀记录
✅ journalctl -k | grep -i -A3 'killed process'  # systemd 机器的等价查法
```

**坑点**：被 OOM 杀掉的进程不会自动回来，除非服务配了 `Restart=always`（见 @references/service-management.md）。

## 七、探活：确认服务到底在不在

```bash
✅ curl -sS -I -m 5 http://127.0.0.1                # -I 只要响应头，-m 5 秒超时
✅ curl -sS -o /dev/null -w "%{http_code}\n" -m 5 https://example.com
```

**为什么**：把地址从 `127.0.0.1` 换成公网 IP 就能定界——**本机通、外网不通 = 防火墙/安全组/端口**；本机都不通 = 应用没起来。

**坑点**：脚本里 `curl` 必须加 `-m`，否则会一直挂着把定时任务卡死；另外 **crontab 里不能跑需要交互/终端的命令**，详见 @references/maintenance-tasks.md。

## 八、轻量监控初阶

服务存活探测脚本 `/usr/local/bin/check.sh`：

```bash
#!/bin/bash
# 探活：不存活就重启；磁盘超 85% 就告警
CODE=$(curl -sS -o /dev/null -w "%{http_code}" -m 5 http://127.0.0.1:3000/health 2>/dev/null)
if [ "$CODE" != "200" ]; then
  echo "$(date '+%F %T') health failed code=${CODE}" >> /var/log/healthcheck.log
  systemctl restart myapp
fi
USE=$(df -h / | awk 'NR==2{print $5}' | tr -d '%')
if [ "$USE" -gt 85 ]; then
  echo "$(date '+%F %T') disk ${USE}% on /" | mail -s "Disk Alert" admin@example.com
fi
```

```bash
⚠️ chmod +x /usr/local/bin/check.sh
⚠️ crontab -e    # */5 * * * * /usr/local/bin/check.sh >> /var/log/healthcheck.log 2>&1
```

**为什么 `2>&1` 必须有**：脚本里的报错不落盘，你会永远看不到——这是「定时任务跑了但没反应」的头号原因。

**磁盘告警阈值建议**：`Used% > 80%` 提醒、`> 90%` 告警、`> 95%` 立刻处理；inode 同理（同样用 `df -i`，阈值 90%）。阈值是经验值，**按业务增长速率调**。

**坑点**：脚本里命令要用绝对路径或显式设 `PATH`——cron 的默认 `PATH` 只有 `/usr/bin:/bin`，`curl` 装在 `/usr/local/bin` 时脚本会静默失败。

## 下一步

- 定时探活与定时日志清理 → @references/maintenance-tasks.md
- 备份策略与恢复演练 → @references/backup-restore.md
- 502/504/超时类报错的进一步排查 → @references/server-troubleshooting.md
- 服务本身的状态与自恢复 → @references/service-management.md
