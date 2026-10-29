# 备份与容灾

> 运维界的残酷真相：**没验证过恢复的备份 = 没有备份**。备份的价值不在"做了"，在"能恢复"。

## 3-2-1 原则

```
3 份副本：生产数据 + 2 份备份
2 种介质：本地磁盘 + 对象存储（或磁带/另一台机器）
1 份异地：跨机房/跨城市/跨云
```

- **RPO**（能容忍丢多少数据）：定时备份=丢一个备份周期；binlog 实时同步≈丢几秒
- **RTO**（能容忍停多久）：冷备=小时级；热备主从切换=分钟级；多活=秒级

## MySQL 备份实战

### 逻辑备份：mysqldump（小库 <50GB）

```bash
# 全量备份（✅ 单事务一致性，不锁表——仅限 InnoDB）
mysqldump -u root -p --single-transaction --routines --triggers \
  --master-data=2 mydb | gzip > mydb-$(date +%F).sql.gz
# --master-data=2：记录 binlog 位点（用于恢复到任意时间点）

# 恢复
gunzip < mydb-2026-09-19.sql.gz | mysql -u root -p mydb
```

### 物理备份：xtrabackup（大库）

```bash
# 全量（热备，不锁表）
xtrabackup --backup --target-dir=/backup/full-$(date +%F) -u root -p

# 增量（基于上次全量）
xtrabackup --backup --target-dir=/backup/inc1 \
  --incremental-basedir=/backup/full-2026-09-19 -u root -p

# 恢复：先 prepare（回滚未提交事务），再 copy-back
xtrabackup --prepare --target-dir=/backup/full-2026-09-19
xtrabackup --copy-back --target-dir=/backup/full-2026-09-19
```

### 恢复到任意时间点（PITR：删库跑路救援）

```bash
# 前提：全量备份 + 备份之后的 binlog 都还在
# 场景：10:30 有人 DROP TABLE，恢复到 10:29
mysqlbinlog --start-position=<备份时的位点> \
  --stop-datetime="2026-09-19 10:29:59" \
  /var/lib/mysql/binlog.000123 | mysql -u root -p
```

### 备份自动化脚本（含保留策略 + 校验）

```bash
#!/usr/bin/env bash
# mysql-backup.sh —— 每日全量 + 7 天保留 + 备份校验
set -euo pipefail

BACKUP_DIR=/backup/mysql
KEEP_DAYS=7
DATE=$(date +%F)
mkdir -p "$BACKUP_DIR"

# 备份
mysqldump -u backup_user -p"$MYSQL_PASS" --single-transaction \
  --all-databases | gzip > "$BACKUP_DIR/full-$DATE.sql.gz"

# ⚠️ 关键一步：校验备份文件有效（gzip 完整 + 非空 + 有内容）
if ! gunzip -t "$BACKUP_DIR/full-$DATE.sql.gz"; then
    echo "❌ 备份文件损坏！" | mail -s "BACKUP FAILED" ops@example.com
    exit 1
fi
SIZE=$(du -h "$BACKUP_DIR/full-$DATE.sql.gz" | cut -f1)
echo "✅ 备份完成：$SIZE"

# 清理过期
find "$BACKUP_DIR" -name "full-*.sql.gz" -mtime +$KEEP_DAYS -delete

# 异地同步（3-2-1 的那个 1）
rclone copy "$BACKUP_DIR/full-$DATE.sql.gz" remote:mysql-backup/
```

## 主从复制（读写分离 + 热备）

```bash
# 主库 my.cnf
# [mysqld]
# server-id = 1
# log-bin = binlog
# binlog_format = ROW

# 从库配置
CHANGE MASTER TO
  MASTER_HOST='10.0.1.10',
  MASTER_USER='repl', MASTER_PASSWORD='xxx',
  MASTER_LOG_FILE='binlog.000123', MASTER_LOG_POS=456;
START SLAVE;

# 检查状态（✅ 两个 Yes 才正常）
SHOW SLAVE STATUS\G
# Slave_IO_Running: Yes
# Slave_SQL_Running: Yes
# Seconds_Behind_Master: 0     ← 延迟监控就盯这个
```

**主从延迟排障**：大事务/DDL 在从库串行重放 → 监控 `Seconds_Behind_Master`，超阈值告警；读请求打主库兜底。

## Redis 备份

```bash
# RDB 快照：bgsave 子进程 fork 写盘（不阻塞）
redis-cli BGSAVE
# 文件：/var/lib/redis/dump.rdb

# AOF：每秒追加（RPO≈1s）
# redis.conf: appendonly yes, appendfsync everysec

# 生产推荐：RDB（定时全量）+ AOF（实时增量）混合
```

## 容灾演练（做了备份只完成一半）

```
季度演练清单：
1. 随机挑一天：把备份恢复到一台干净机器，验证数据完整性
   （记录 RTO：实际恢复花了多久）
2. 主从切换演练：手动 failover，观察应用是否自动跟随
3. 机房断电演练：模拟整机房不可用，验证 DNS/LB 切换
4. 每半年：完整走一遍"删库救援"流程（PITR）
```

**演练铁律**：演练脚本和生产故障手册必须是同一份——故障时手抖，没空现想。

## 常见备份反模式

- ❌ 备份和数据库在同一台机器（机器坏了全完）
- ❌ 备份脚本跑了三年没人看过结果（磁盘满了静默失败两年）
- ❌ 只备份数据库，不备份配置文件/证书/密钥（恢复时抓瞎）
- ❌ 备份目录无权限管控（勒索软件连备份一起加密）→ 备份目标**只写不删**（对象存储 WORM/版本控制）
