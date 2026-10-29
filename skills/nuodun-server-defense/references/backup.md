# 备份与恢复手册

> 读这份文档的时机：要建立备份机制、验证备份可用性、或需要恢复数据时。

## 三个必须记住的真相

1. **没有校验过的备份不算备份。** 硬盘坏道、压缩损坏、复制中断都会静默地毁掉备份。
   本技能的每份备份都带 sha256 清单，`backup verify` 会逐个文件回读比对。
2. **只在本机留一份不算备份。** 主机被勒索、磁盘损坏、误删目录 —— 三种情况会同时带走
   原始数据和你"备份"在同一块盘上的副本。
3. **没做过恢复演练的备份等于没有。** 这是运维里最常见、代价最大的自我欺骗。

---

## 3-2-1 原则

| 要求 | 含义 | 落地方式 |
|---|---|---|
| **3** 份副本 | 原始 + 2 份备份 | 本机一份，异地一份，离线一份 |
| **2** 种介质 | 不放在同一种存储上 | 本机磁盘 + 对象存储 / 磁带 / NAS |
| **1** 份异地 | 至少一份离开这台机器 | 云对象存储（OSS/S3）、另一机房 |

**勒索软件专门删备份**。因此"离线副本"（写完后断开连接 / 只读挂载 / 对象存储的
版本控制 + 保留策略）是最后一道防线，不是可选项。

---

## 备份什么

### 默认清单（本技能内置）

Windows：
```
%SystemRoot%\System32\drivers\etc        hosts、lmhosts 等解析配置
%SystemRoot%\System32\inetsrv\config     IIS 配置（applicationHost.config）
%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp   启动目录
%USERPROFILE%\.ssh                       私钥与 known_hosts
C:\Windows\System32\winevt\Logs          事件日志（入侵溯源依赖）
```

Linux：
```
/etc                                     全部系统配置
/root/.ssh                               私钥
/home                                    用户数据
/usr/local/tomcat/conf                   Tomcat 配置
/etc/nginx  /etc/httpd                   Web 服务器配置
/var/spool/cron                          计划任务
/etc/mysql  /etc/redis                   数据库配置
```

### 必须另外纳入的（按业务补）

- **数据库**：不能靠拷文件，必须用逻辑备份
  ```bash
  mysqldump --single-transaction --routines --triggers --events --all-databases | gzip > db.sql.gz
  pg_dump -Fc -f appdb.dump appdb
  ```
- **应用代码与产物**（如果不是从 Git 部署）
- **证书与密钥**（注意加密存放，别明文躺在备份里）
- **容器/编排配置**（docker-compose.yml、K8s manifests）
- **自建脚本与计划任务定义**

### 不要备份的

- `node_modules` / `__pycache__` / 编译中间产物（本技能默认自动排除）
- 临时目录、缓存目录
- 单文件超过 2GB 的（会拖垮备份窗口，应单独用镜像/快照方案）

---

## 用本技能做备份

```bash
# 全量备份（默认清单）+ 自动校验
python scripts/cli.py backup run --dest D:\ops-backups

# 指定源与标签
python scripts/cli.py backup run --dest D:\ops-backups \
  --sources "D:\sites" "D:\config" --label web

# 增量（对比上一次 manifest 的哈希，只备份变化过的文件）
python scripts/cli.py backup run --dest D:\ops-backups \
  --incremental-from D:\ops-backups\daily-20261003.manifest.json --label daily

# 校验
python scripts/cli.py backup verify --manifest D:\ops-backups\daily-20261004.manifest.json
python scripts/cli.py backup verify-latest --dest D:\ops-backups

# 列出历史
python scripts/cli.py backup list --dest D:\ops-backups

# 保留策略（默认只列不删，确认后加 --yes）
python scripts/cli.py backup prune --dest D:\ops-backups --keep 14
python scripts/cli.py backup prune --dest D:\ops-backups --keep 14 --yes

# 生成每日定时任务脚本
python scripts/cli.py backup schedule --dest D:\ops-backups --hour 3

# 恢复到沙箱目录先核对（强烈推荐）
python scripts/cli.py backup restore --manifest <manifest路径> --to D:\restore-test
```

### 恢复演练的标准流程

```bash
# 1) 恢复到沙箱目录，绝不直接覆盖生产
python scripts/cli.py backup restore --manifest xxx.manifest.json --to /tmp/restore-test

# 2) 校验恢复出来的内容
diff -r /etc /tmp/restore-test/etc | head -50

# 3) 数据库额外做一次导入演练
gunzip < db-20261004.sql.gz | mysql appdb_restore_test
mysql -e "SELECT COUNT(*) FROM appdb_restore_test.orders"

# 4) 记录演练结果（时间、耗时、发现问题）
```

**每季度至少演练一次**，并在变更后（改配置、升版本）追加一次。

---

## 挂定时任务

```bash
# Linux
crontab -e
0 3 * * * /bin/sh /path/to/ops-backups/backup-task.sh >> /var/log/ops-backup.log 2>&1
```

```powershell
# Windows（管理员）
schtasks /create /tn "OpsDailyBackup" /sc daily /st 03:00 ^
  /tr "powershell -NoProfile -ExecutionPolicy Bypass -File D:\ops-backups\backup-task.ps1" ^
  /ru SYSTEM
schtasks /query /tn "OpsDailyBackup" /v /fo LIST
```

**用 SYSTEM 身份运行**：避免因用户未登录或密码变更导致任务不执行。

---

## 备份目录本身也要保护

```bash
# 打成只读（防误删，也减慢勒索软件加密速度）
chattr +i /data/backups/*.zip 2>/dev/null
lsattr /data/backups/

# 挂载为只读（更强）
mount -o remount,ro /data/backups
```

```powershell
# Windows：只给 SYSTEM 与 Administrators 完全控制，其余只读
icacls D:\ops-backups /inheritance:r
icacls D:\ops-backups /grant "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"
```

对象存储侧开启：
- **版本控制**（Versioning）—— 被覆盖/删除还能找回
- **对象锁定 / WORM**（合规保留）—— 谁都删不掉
- **生命周期规则** —— 自动转低频/归档，控制成本

---

## 恢复的决策树

```
数据丢了 →
├─ 只是误删单个文件？
│   └─ 从最近的备份里取那一个文件（用 --to 恢复到沙箱再拷回）
├─ 数据库损坏 / 误删表？
│   ├─ 有 binlog / WAL → 全量恢复 + 重放增量，损失最小
│   └─ 只有全量 → 恢复到最近全量，损失该时间点之后的数据
├─ 整机故障？
│   └─ 云主机：用快照重建 → 再恢复数据目录
└─ 被勒索加密？
    ├─ 有离线/不可变副本 → 全新机器 + 从离线副本恢复（不要在被加密的机器上恢复）
    └─ 无 → 评估是否值得支付（通常不建议，且不保证能解密）
```

**勒索场景的关键动作**：**不要**在被加密的机器上执行恢复，也不要删掉被加密的文件
（可能留有解密线索或用于取证）。先隔离，再从干净的副本恢复。

---

## 备份健康度自检

```bash
# 最近一份备份的时间与大小
python scripts/cli.py backup list --dest D:\ops-backups | head -3

# 校验最新一份（建议纳入日常巡检）
python scripts/cli.py backup verify-latest --dest D:\ops-backups
```

**红线**：
- 最近一份备份距今 > 48 小时 → 定时任务可能失败了，去查日志
- 备份大小突然骤减 → 源目录可能已被删/被加密
- 校验失败 → 该备份不可用，立刻重做并排查存储介质

这三条建议做成监控告警。备份最大的失败模式不是"没做"，而是**以为做了、实际失败了、
没人发现**。
