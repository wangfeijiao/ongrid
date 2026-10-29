# 硬盘瘦身 · 空间治理参考

> 目标不是「删得多」，而是**只删该删的，并且删完还能回来**。
> 一台服务器最贵的不是磁盘，是上面跑的业务和不可再生的现场证据。

---

## 一、先想清楚：空间去哪了

清理之前必须先定位，否则就是拿删文件赌运气。按这个顺序看：

| 现象 | 真实原因（按概率排序） |
|---|---|
| 系统盘持续变小 | ① 日志没轮转 ② 更新缓存/组件存储堆积 ③ 应用写的临时文件没清 ④ 数据库 binlog/慢日志 ⑤ core dump ⑥ 备份文件写到了系统盘 |
| 某分区突然满了 | ① 应用日志暴涨（往往是死循环刷错误） ② 数据库 binlog 未清理 ③ 用户上传目录 ④ docker/overlay 层 ⑤ 大文件被写入 |
| inode 满了但空间还有 | 海量小文件：session 文件、队列积压、缩略图、邮件队列、`/var/spool` |
| 空间对不上（df 与 du 差很多） | **已删除但仍被进程持有**的文件（见第五节） |

**判定顺序**：`df -h` → 找到最紧的分区 → `du -x -h --max-depth=1 <挂载点> | sort -h` → 逐层下钻。
Windows 上对应：资源管理器看分区 → 用本技能的 `clean scan --big` 找大文件。

---

## 二、可以清的，和绝对不能清的

### 可以清（按风险从低到高）

| 类别 | Linux | Windows | 说明 |
|---|---|---|---|
| 临时文件 | `/tmp`、`/var/tmp` 超期内容 | `%TEMP%`、`C:\Windows\Temp` | 只清「N 天没动过」的，避让在用文件 |
| 包/更新缓存 | `/var/cache/apt\|yum\|dnf\|zypp\|pacman` | `SoftwareDistribution\Download`、传递优化 | 重装时会重新下载 |
| 日志 | `journalctl --vacuum-size=200M`、`*.gz`、`*.1` | `C:\Windows\Logs\CBS`、`*.etl` | **保留最近 14 天** |
| 缓存 | `~/.cache/thumbnails`、pip/npm/yarn 缓存 | 缩略图/图标缓存、浏览器 Cache | 自愈型 |
| 崩溃残留 | `core.*`、`/var/lib/systemd/coredump` | `MEMORY.DMP`、`Minidump` | 排查期间保留 |
| 回收站 | `~/.local/share/Trash` | `C:\$Recycle.Bin` | 不可用常规方式找回 |

### 绝对不能清（清了会出事故）

| 项 | 后果 |
|---|---|
| `/var/lib/mysql`、`/var/lib/pgsql`、SQL Server 数据目录 | 数据库损坏，业务中断 |
| `/etc`、`/boot` 下的任何东西 | 系统起不来 |
| `~/.ssh`、`/etc/ssh/ssh_host_*_key` | 失去登录能力 / 所有客户端告警 |
| 证书与私钥（`*.key`、`*.pem`、`*.pfx`、`.keystore`） | 服务无法启动，且无法恢复 |
| 正在运行的 `.jar`/`.war` 与 `logs/` 下的当前日志 | 应用崩溃或失去现场 |
| `WinSxS`（手删） | **必须用 DISM**，手删会破坏组件存储 |
| 事件日志（排查期间） | 销毁审计证据 |
| `pagefile.sys` | 系统不稳定 |

> 一条判据：**如果这个文件丢了之后你无法从别处重新生成它，那它就不是垃圾。**
> 缓存可以重建，日志可以重放，但**数据、密钥、证据**不行。

---

## 三、本技能怎么做：三段式，每段都留退路

```
①  scan   只读扫描 → 出「可回收清单」（JSON + 文本），可存档、可带回去复核
②  plan   点名类别 → 出清理计划（逐条列出将处理的路径 + 体积 + 风险等级）
③  clean  执行 → 默认进**隔离区**（move，不是 delete），可 restore 还原
                 只有显式 --purge 才是真删除
```

```bash
python cli.py clean scan                    # ① 只读，不改任何东西
python cli.py clean scan --big --path D:\   # 附带大文件扫描
python cli.py clean show --category user_temp   # 看某类别明细
python cli.py clean plan --categories user_temp,rotated_logs   # ② 出计划
python cli.py clean clean --categories user_temp --apply --yes # ③ 隔离执行
python cli.py clean quarantine               # 看隔离区
python cli.py clean restore --batch <目录>   # 反悔：还原
python cli.py clean purge --batch <目录> --yes  # 确认无碍后销毁（不可逆）
```

### 为什么要有隔离区

因为**「删错了」是唯一无法赎买的错误**。隔离区把这一步变成可逆：

- 默认隔离区放在**同一分区**（Windows `X:\.ops-quarantine\`、Linux `/.ops-quarantine/`），
  同盘 move 只是改目录项，几十 GB 也是瞬间完成；跨盘 move 会变成完整拷贝。
- 隔离区自带 `manifest.json` 与 `restore.ps1` / `restore.sh`，
  即使本工具被卸载，也能靠脚本+清单自己还原。
- 观察几天确认业务正常，再 `purge`。这是唯一会丢数据的动作，所以需要 `--yes`。

---

## 四、Windows 特有的几件事

| 项 | 正确做法 | 为什么不能手删 |
|---|---|---|
| 组件存储 | `DISM /Online /Cleanup-Image /StartComponentCleanup /ResetBase` | `WinSxS` 里是硬链接，手删会破坏已装组件；`/ResetBase` 后已装补丁无法卸载 |
| 休眠文件 | `powercfg /hibernate off` | 直接删 `hiberfil.sys` 系统会重建或报错；关掉会同时失去「快速启动」 |
| 页面文件 | 由系统管理，或移到其他盘 | 手删导致系统不稳定 |
| Windows.old | 确认不需要回退升级后再清 | 删了**再也无法回滚到升级前系统** |
| NTFS 压缩 | `compact /c /s:<目录>` | 是省空间但不是瘦身，会加重 CPU 与碎片 |
| 陈旧还原点 | `vssadmin delete shadows /for=C: /oldest` | 删多了失去系统还原能力 |

> `SoftwareDistribution\Download` 在补丁安装过程中被清可能导致更新失败。
> **建议在重启完成、确认补丁装完之后再清**。

---

## 五、空间对不上：被删了但没释放

```
df 显示 95%，du 加起来只有 40%  →  中间消失的空间被"已删除但仍被打开"的文件占着
```

进程打开一个文件后又把它删了，Linux 会保留 inode 直到句柄关闭，磁盘空间不释放。
常见于：日志轮转后应用没重新打开文件、Tomcat 的 catalina.out 被 `rm` 后仍在写。

**定位**：

```bash
lsof +L1 2>/dev/null | head -50          # 列出 deleted 且 size>0 的打开文件
lsof -nP 2>/dev/null | grep '(deleted)'  # 等价写法
```

**处理**：**不要 kill 进程**（会中断业务）。正确做法是让应用重新打开日志：

```bash
# tomcat / 多数应用：清空文件内容而不删文件（空间立刻释放，句柄继续有效）
: > /path/to/catalina.out
```

然后用 logrotate 的 `copytruncate` 模式，从根上避免这个问题：

```
/var/log/myapp/*.log {
    daily
    rotate 14
    compress
    copytruncate      # ← 关键：复制后清空原文件，不改变 inode
    missingok
    notifempty
}
```

Windows 上对应的问题是**进程持有文件句柄导致删除失败**，
表现是「拒绝访问」而不是空间不释放。处理方法是停服务或在 handle 层面确认占用者。

---

## 六、别让垃圾重新长回来

清一次是止痛，机制才是根治。**每次清理后都该问：它为什么会堆起来？**

| 垃圾来源 | 根治手段 |
|---|---|
| 应用日志无限制增长 | logrotate（`copytruncate`）+ 应用自身日志级别与滚动配置 |
| journal 无限增长 | `/etc/systemd/journald.conf` 设 `SystemMaxUse=500M` |
| 数据库 binlog | 设 `expire_logs_days` / `binlog_expire_logs_seconds` |
| 备份文件写到系统盘 | 备份目标改到独立盘或对象存储，并设保留份数 |
| core dump 反复产生 | 先修崩溃根因，再设 `LimitCORE=0` 或不重要机器上关闭 |
| 临时文件堆积 | 应用加清理任务，或系统定时清理（systemd-tmpfiles） |
| Windows 更新缓存 | 保持定期重启让更新真正完成 |

> **先说结论的话**：如果同一个目录三个月后又要清，那不是清理问题，是**配置问题**。
> 清理报告里应该把这一点讲清楚，而不是让用户每季度来做一次同样的事。

---

## 七、判读红线

扫描结果里出现以下情形，**先停下，不要清**：

- 目标机**正在被入侵排查**（`audit --scope malware` 有命中）→ 先取证，见
  `malware-response.md`。此时 `/tmp`、cron、日志、`~/.ssh` **全部都是证据**。
- 目标机是**数据库/存储服务器** → 任何非缓存目录都要先确认用途。
- 分区使用率**低于 70%** → 除非有明确的增长趋势，否则收益不足以承担风险。
- 无法确定某个目录是谁写的、干什么用的 → **不清**。不确定时的不作为，成本远低于误删。
- `clean scan` 报告里有「未能覆盖」项 → 说明清单不完整，**不能说"已经清干净了"**。

---

## 八、汇报口径

清完之后向使用者交代这几件事，缺一不可：

```
清理小结
  处理 N 项 / 释放 X GB
  实测 <挂载点>：可用 A → B（+C）      ← 用 df 前后对比，不要只报估算值
  隔离区：<路径>（可 restore 还原；确认无碍后 purge）
  未能覆盖：<n> 项 —— 原因 <...>
  命令型项（需你确认后手动执行）：
      $ journalctl --vacuum-size=200M
  根治建议：<为什么它会堆起来 + 该改哪个配置>
```

**不要只报「释放了 X GB」**。用户真正需要知道的是：**我删了什么、还能不能回来、下次怎么不用再删。**
