# 定时任务与自动化运维：cron 与 systemd timer

覆盖「每天自动备份」「重启后自启」「脚本跑了但没效果」三类需求。systemd timer 需 **CentOS 7+/Ubuntu 16.04+**；`update-rc.d` 属 Debian/Ubuntu 系，`chkconfig` 属 CentOS/RHEL 系。

## 一、cron 完整语法

```
* * * * * 命令
│ │ │ │ │
│ │ │ │ └─── 星期几 0-7（0 和 7 都是周日）
│ │ │ └───── 月 1-12 │ │ └─── 日 1-31（与星期几是"或"关系）
│ └───────── 小时 0-23
└─────────── 分钟   0-59
```

```bash
⚠️ crontab -e    # 编辑当前用户的定时任务表（首次会选编辑器）
✅ crontab -l    # 查看已配置的任务
```

```bash
*/5 * * * * /usr/local/bin/check.sh     # 每 5 分钟
0 3 * * * /usr/local/bin/backup.sh      # 每天 03:00，避开业务高峰
0 3 * * 0 /usr/local/bin/weekly.sh      # 每周日凌晨 3 点
30 2 1 * * /usr/local/bin/monthly.sh    # 每月 1 号凌晨 2:30
```

**为什么不用 `* * * * *`**：最小粒度确实是 1 分钟，但每分钟跑备份脚本会把磁盘与 CPU 打满、日志爆炸，业务任务一律按小时/天级别设。

**特殊串**（写在字段位上代替五个字段）：`@daily`（`@midnight`）= 每天零点、`@hourly`、`@weekly`=`0 0 * * 0`、`@monthly`=`0 0 1 * *`、`@yearly`=`0 0 1 1 *`、`@reboot`= 开机执行一次。

## 二、头号坑：cron 的环境变量极窄

```bash
# ❌ 在 crontab 里这么写，任务静默失败
0 3 * * * backup.sh                             # 脚本不在 PATH 里
0 3 * * * cd /opt/myapp && ./bin/backup.sh      # 相对路径基准不是你以为的目录
0 3 * * * python3 script.py                     # python3 在 /usr/local/bin
```

**为什么失败**：cron 的 `PATH` 默认只有 `/usr/bin:/bin`（**不含 `/usr/local/bin`、`/usr/sbin`**），且**不加载 `.bashrc` 里的任何导出**（PATH 覆盖、conda/nvm 环境、代理设置统统没有）。上面三条分别对应：脚本不在 PATH、相对路径基准不是你以为的目录、`python3` 在 `/usr/local/bin`。

**正确写法**——`PATH=` 写在 `crontab -e` 的文件**顶部**（不是脚本内部）：

```bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * /usr/local/bin/backup.sh
0 3 * * * cd /opt/myapp && /bin/bash ./bin/backup.sh
0 3 * * * /usr/local/bin/python3 /opt/myapp/script.py
```

## 三、三处配置位置与差异

| 位置 | 格式 | 差异 |
|---|---|---|
| `crontab -e` | 五字段 + 命令 | 属于**当前用户**，不写用户名列。推荐 |
| `/etc/crontab` 与 `/etc/cron.d/xxx` | 五字段 + **用户名列** + 命令 | 系统级，cron.d 拆成文件便于分发 |

**坑点**：cron 按文件遍历执行，三处不会互相顶掉，但**`/etc/crontab` 与 `/etc/cron.d/*` 里的行所有人都会执行**，自己的任务放 `crontab -e` 才互不干扰。格式上 `/etc/cron.d/` 与 `/etc/crontab` 必须带用户名列（漏了报 `bad username`），而 `crontab -e` 里多写用户名反而报错。

```bash
✅ crontab -l                 # 查看当前用户任务
⚠️ crontab -e                 # 编辑，保存即生效，无需重启
🚫 crontab -r                 # 高危：清空当前用户所有任务，无确认、无回收站
🚫 crontab -i -r              # 唯一安全版本：逐个确认
```

`crontab -r` 不备份，要删就用 `crontab -e` 删行，改前 `✅ crontab -l > ~/crontab.backup`。

## 四、开机自启三种方式

| 方式 | 适用 | 写法 | 坑 |
|---|---|---|---|
| `systemctl enable` | **所有带 systemd 的现代发行版，首选** | `⚠️ systemctl enable --now nginx` | 只管开机，不影响当前状态 |
| `update-rc.d` | Debian/Ubuntu（有 systemd 时实为 systemd 接管） | `⚠️ update-rc.d nginx defaults` | 只在老 SysV 系统裸跑；配错不报错也不生效 |
| `@reboot` cron | 开机跑一次一次性初始化 | `@reboot /usr/local/bin/init.sh` | **不保证网络/依赖服务就绪**，见下方 |

```bash
✅ systemctl is-enabled nginx                                        # 看 enabled 状态
✅ ls -l /etc/systemd/system/multi-user.target.wants/ | grep nginx   # 软链是否真建了
```

**为什么 `@reboot` 不可靠**：它在开机早期触发，网络、磁盘挂载、依赖服务可能都还没好，脚本里要自带等待：`until curl -sS -m 2 http://127.0.0.1 >/dev/null; do sleep 5; done`。

## 五、备份实战脚本（可直接抄）

`/usr/local/bin/backup.sh`：MySQL 全备 + 网站目录打包，文件名带日期，保留 7 天，失败会写日志。

```bash
#!/bin/bash
# ⚠️ 有副作用：写入备份目录并删除 7 天前的旧备份
BACKUP_DIR=/data/backup
MYSQL_USER=root
MYSQL_PASS='your_password_here'      # 生产建议改用 option-file，见下方坑点
WEB_DIR=/var/www/html
DATE=$(date +%F)
LOG=/var/log/backup.log

mkdir -p $BACKUP_DIR || { echo "$(date '+%F %T') mkdir failed" >> $LOG; exit 1; }

mysqldump -u$MYSQL_USER -p$MYSQL_PASS --single-transaction --routines \
  --all-databases | gzip > $BACKUP_DIR/mysql-$DATE.sql.gz \
  || { echo "$(date '+%F %T') mysqldump FAILED" >> $LOG; exit 1; }

tar -pczf $BACKUP_DIR/www-$DATE.tar.gz -C $WEB_DIR . \
  || { echo "$(date '+%F %T') tar www FAILED" >> $LOG; exit 1; }

find $BACKUP_DIR -name "mysql-*.sql.gz" -mtime +7 -delete
find $BACKUP_DIR -name "www-*.tar.gz"   -mtime +7 -delete
echo "$(date '+%F %T') backup ok" >> $LOG
```

```bash
⚠️ chmod +x /usr/local/bin/backup.sh
⚠️ /usr/local/bin/backup.sh                 # 先手动跑一次，确认不报错
⚠️ crontab -e                               # 加 cron：0 3 * * * /usr/local/bin/backup.sh >> /var/log/backup.log 2>&1
```

**为什么这样写**：① 每步用 `$?` 判断，失败写日志并 `exit 1`，避免「脚本天天跑其实天天失败」；② 时间戳文件名互不覆盖；③ `-mtime +7 -delete` 控住磁盘；④ `--single-transaction` 让 InnoDB 备份不锁表。

**坑点**：**密码不要明文进脚本**——写到 `/etc/mysql/conf.d/backup.cnf`（`[client]` 段，含 user/password）并 `chmod 600`；`mysqldump` 参数按 MySQL/MariaDB 版本调整，**以当前 `mysqldump --help` 为准**。备份策略与恢复见 @references/backup-restore.md。

## 六、systemd timer：cron 的现代替代

```ini
[Unit]
Description=Daily backup

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

```bash
⚠️ cp backup.timer /etc/systemd/system/backup.timer
⚠️ systemctl daemon-reload
⚠️ systemctl enable --now backup.timer
✅ systemctl list-timers             # 看所有 timer 与下次触发时间
```

**为什么更合适**：① `Persistent=true` 让关机期间错过的任务**开机后自动补跑**（cron 是直接错过）；② 日志直接在 `journalctl` 里；③ `RandomizedDelaySec` 打散并发，避免一堆任务同一秒戳服务器；④ 能声明依赖与资源限制、适合长任务，而 cron 最小粒度只有 1 分钟且无错峰。**坑点**：`OnCalendar` 语法字段多（`*-*-* 03:00:00` 每天 3 点，周日可写 `Sun 03:00`），随版本不同，**以当前 `man systemd.timer` 为准**；对应的 `.service` 必须 `Type=oneshot`，否则会立刻退出。

## 七、排错清单：跑了但没效果的 6 个原因

1. **PATH 太窄**（第二节）——最常见。脚本里命令全写绝对路径，或在 crontab 顶部设 `PATH=`。
2. **权限不足**——cron 以**任务所属用户**运行；root 的 crontab 里脚本要可执行、日志目录对该用户可写。
3. **相对路径基准不是你以为的目录**——cron 的工作目录是 `~`（root 即 `/root`）。用绝对路径或脚本开头 `cd /opt/myapp`。
4. **输出没落盘**——忘了 `>> /var/log/xxx.log 2>&1`，错误只能进邮件（多数机器没配 mail）后静默消失。
5. **时区不是你以为的**——cron 用系统时区（`✅ timedatectl` 看），与浏览器时区可能不同，夏令时地区尤其容易踩。
6. **脚本用了交互命令**——`git pull` 要密码、`ssh` 要确认 host key、`su` 要密码，在 cron 里会卡住或失败；改用 `ssh -o BatchMode=yes` 并配好免密。

排查顺序：`✅ crontab -l` 确认保存过 → 手动跑一次脚本看报错 → `✅ journalctl -u 服务名 -n 100 --no-pager`。

## 八、日志清理与磁盘水位守护

```bash
✅ find /var/log -type f -name "*.log" -mtime +30 -print          # 先看会删哪些（只读）
⚠️ find /var/log -type f -name "*.log" -mtime +30 -delete         # 🚫 高危：真删，先备份
```

**为什么标高危**：`-delete` 不询问、不进回收站，路径写错就会误删数据——执行前先跑上面的 `-print` 看清单。

```bash
⚠️ crontab -e
0 4 * * * /usr/local/bin/disk-watch.sh >> /var/log/disk-watch.log 2>&1   # 水位检查，阈值见 logs-monitoring
30 5 * * * find /var/log -type f -name "*.log" -mtime +30 -delete        # 低峰清理 30 天前的日志
```

**为什么错开时间**：先告警（阈值 90%）再在低峰清理，避免业务跑着删日志把 IO 打满。**坑点**：正在被写的应用日志直接删不会释放空间，要应用重启或 `⚠️ nginx -s reopen` 后才释放。

## 下一步

- 备份怎么恢复、3-2-1 策略 → @references/backup-restore.md
- 探活脚本与告警阈值 → @references/logs-monitoring.md
- 服务开机自启与自恢复 → @references/service-management.md
- 定时任务写错导致的资源异常 → @references/server-troubleshooting.md
