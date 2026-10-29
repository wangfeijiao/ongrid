# 数据库安装、配置与调优手册

> 读这份文档的时机：需要**安装**数据库，或报告出现 `DB_BIND_ALL` /
> `PORT_EXPOSED_*` / `DB_PORT_EXPOSED_*` 条目时。

## 安装前的三个决定

| 决定 | 建议 | 理由 |
|---|---|---|
| 数据目录放哪 | **独立数据盘**，不放系统盘 | 系统盘写满会连带数据库宕机；独立盘便于快照与扩容 |
| 监听地址 | **只监听 127.0.0.1 或内网地址** | 数据库永远不该直接暴露公网 |
| 内存上限 | **显式设定**，不留给"自动" | 默认值在大内存机器上会吃掉全部内存 |

---

# 一、MySQL / MariaDB

## Linux 安装

```bash
# RHEL / CentOS / Rocky
dnf install -y mysql-server
systemctl enable --now mysqld
mysql_secure_installation      # 交互式：设 root 口令、删匿名用户、禁远程 root

# Debian / Ubuntu
apt install -y mysql-server
systemctl enable --now mysql
mysql_secure_installation
```

## 配置文件（/etc/my.cnf 或 /etc/mysql/mysql.conf.d/mysqld.cnf）

```ini
[mysqld]
# --- 网络：只监听本机；确实需要跨机访问也只绑内网地址 ---
bind-address            = 127.0.0.1
port                    = 3306
max_connections         = 500
max_connect_errors      = 100

# --- 数据与日志：全部放数据盘 ---
datadir                 = /data/mysql
socket                  = /var/lib/mysql/mysql.sock
log-error               = /data/logs/mysql/error.log
slow_query_log          = 1
slow_query_log_file     = /data/logs/mysql/slow.log
long_query_time         = 2
log_queries_not_using_indexes = 0

# --- InnoDB：内存按物理内存的 50%~70% 给缓冲池 ---
innodb_buffer_pool_size = 4G
innodb_log_file_size    = 1G
innodb_flush_log_at_trx_commit = 2      # 1=最安全最慢, 2=性能模式(丢1秒)
innodb_flush_method     = O_DIRECT
innodb_io_capacity      = 2000          # SSD；机械盘 200
innodb_io_capacity_max  = 4000

# --- 字符集：统一 utf8mb4（emoji 与生僻字需要） ---
character-set-server    = utf8mb4
collation-server        = utf8mb4_general_ci

# --- 安全审计（企业版有 audit 插件；社区版可跳过）---
# 临时表空间与排序缓冲
tmp_table_size          = 64M
max_heap_table_size     = 64M
join_buffer_size        = 4M
sort_buffer_size        = 4M

[client]
default-character-set   = utf8mb4
```

⚠️ `innodb_buffer_pool_size` 是 MySQL 性能的第一变量。**给到物理内存的 50%~70%**，
但必须给操作系统和其他进程留够。

```bash
systemctl restart mysqld
```

## 安全加固

```sql
-- 删除匿名用户与远程 root
DELETE FROM mysql.user WHERE User='';
DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost','127.0.0.1','::1');
DROP DATABASE IF EXISTS test;

-- 应用账号：只给必要权限，不用 root
CREATE USER 'appuser'@'10.0.0.%' IDENTIFIED BY '<强口令>';
GRANT SELECT, INSERT, UPDATE, DELETE ON appdb.* TO 'appuser'@'10.0.0.%';
FLUSH PRIVILEGES;

SELECT user, host, plugin FROM mysql.user;
```

```bash
# 备份（mysqldump 全库 + 压缩）
mysqldump --single-transaction --routines --triggers --events \
  --all-databases | gzip > /data/backups/mysql-$(date +%F-%H%M).sql.gz
```

**必须用 `--single-transaction`**：InnoDB 下不锁表，且保证一致性快照。
不加它会在备份期间锁表，生产库会卡死。

---

# 二、PostgreSQL

```bash
dnf install -y postgresql-server postgresql-contrib
postgresql-setup --initdb
systemctl enable --now postgresql
```

```conf
# /var/lib/pgsql/data/postgresql.conf
listen_addresses = 'localhost'          # 只在需要跨机时才改
port = 5432
max_connections = 200
shared_buffers = 2GB                    # 物理内存的 25%
effective_cache_size = 6GB              # 物理内存的 50%~75%
work_mem = 8MB
maintenance_work_mem = 256MB
wal_level = replica
checkpoint_completion_target = 0.9
random_page_cost = 1.1                  # SSD 必改（默认 4.0 是机械盘假设）
effective_io_concurrency = 200         # SSD
log_min_duration_statement = 2000       # 记录超过 2s 的慢查询
```

```
# /var/lib/pgsql/data/pg_hba.conf —— 认证方式必须收紧
local   all   all                    peer
host    all   all   127.0.0.1/32     scram-sha-256
host    all   all   ::1/128          scram-sha-256
host    appdb appuser 10.0.0.0/24    scram-sha-256
```

**`trust` 认证是重大风险**：任何能连上端口的客户端都可无密码登录。
`ident`/`peer` 之外的网络连接一律用 `scram-sha-256`。

```bash
systemctl reload postgresql
psql -c "SELECT version();"
```

---

# 三、Redis

```bash
dnf install -y redis
# 或源码编译（性能更好，可指定 jemalloc）
```

```conf
# /etc/redis/redis.conf
bind 127.0.0.1 -::1                # 只监听本机
protected-mode yes
port 6379
requirepass <强口令>                # 必须有！

# 持久化：RDB + AOF 双开（按业务取舍）
save 900 1
save 300 10
appendonly yes
appendfsync everysec               # 每秒刷盘，性能与安全的平衡

# 内存上限与淘汰策略（必须设，否则会 OOM 拖垮主机）
maxmemory 2gb
maxmemory-policy allkeys-lru

# 危险命令改名/禁用（防被写入 SSH key 或计划任务）
rename-command CONFIG   ""
rename-command FLUSHALL ""
rename-command FLUSHDB  ""
rename-command KEYS     ""
```

```bash
systemctl restart redis
redis-cli -a '<强口令>' ping        # 应返回 PONG
```

**为什么必须设 `requirepass`**：Redis 未授权访问是云主机最常见的入侵入口之一。
攻击者可写入 `authorized_keys`、写 crontab 计划任务，直接拿到 shell。

---

# 四、SQL Server（Windows）

```powershell
# 安装（图形界面或命令行）
Install-WindowsFeature -Name NET-Framework-45-Core
# 使用 SQL Server 安装中心安装，注意选"混合模式认证"并设强 SA 口令

# 安装后加固
# 1) 关闭 SQL Browser（1434/UDP 常被用来枚举实例）
Stop-Service SQLBrowser; Set-Service SQLBrowser -StartupType Disabled

# 2) 限制 TCP 只监听内网
#    SQL Server 配置管理器 → 网络配置 → 协议 → TCP/IP → IP 地址 → 输入内网 IP

# 3) 数据库引擎自动内存管理改为显式上限（企业版）
#    实例属性 → 内存 → 最大服务器内存(MB) = 物理内存的 70%

# 4) 防火墙只放行内网来源
New-NetFirewallRule -DisplayName 'SQL-Internal-Only' -Direction Inbound `
  -Protocol TCP -LocalPort 1433 -Action Allow -RemoteAddress 10.0.0.0/24
```

```sql
-- 清理无用登录名与过度权限
SELECT name, type_desc, is_disabled FROM sys.server_principals WHERE type IN ('S','U');

-- 检查是否有可疑的 public 权限或 xp_cmdshell 开启
SELECT name, value_in_use FROM sys.configurations WHERE name = 'xp_cmdshell';
EXEC sp_configure 'xp_cmdshell', 0; RECONFIGURE;
```

**`xp_cmdshell` 必须关闭** —— 它是 SQL Server 提权到操作系统命令执行的最直接路径。

---

# 五、通用：备份策略

数据库备份遵循 **3-2-1**：

- **3** 份副本（原始 + 2 份备份）
- **2** 种介质（本机磁盘 + 对象存储/磁带）
- **1** 份异地

```bash
# MySQL 全量 + 增量（binlog）
mysqldump --single-transaction --flush-logs --master-data=2 --all-databases > full.sql
mysqladmin flush-logs                              # 切换 binlog
mysqlbinlog mysql-bin.000012 > incr.sql            # 增量恢复

# PostgreSQL
pg_dump -Fc -f /data/backups/pg-$(date +%F).dump appdb
pg_restore -d appdb /data/backups/pg-2026-10-04.dump
```

**备份必须定期做恢复演练**。没验证过能恢复的备份，等于没有备份 ——
这是运维里最常见、代价最大的自我欺骗。

```bash
# 恢复演练：恢复到临时库核对行数
mysql -e "CREATE DATABASE appdb_restore_test"
gunzip < mysql-2026-10-04.sql.gz | mysql appdb_restore_test
mysql -e "SELECT COUNT(*) FROM appdb_restore_test.<关键表>"
```

---

# 六、上线前检查清单

- [ ] 数据目录在独立数据盘
- [ ] 监听地址为 127.0.0.1 或内网，非 0.0.0.0
- [ ] 已设置强口令，且删除了默认/匿名账户
- [ ] 应用使用专用低权账号，不用 root / sa
- [ ] 内存上限已显式设定
- [ ] 已配置每日备份 + 轮转 + 异地副本
- [ ] **做过一次恢复演练**
- [ ] 危险功能已关闭（Redis CONFIG、SQL Server xp_cmdshell、MongoDB 无认证）
- [ ] 慢查询日志已开启，定期 review
- [ ] 已纳入监控（连接数、慢查询、磁盘空间、锁等待）
