# 备份与恢复：从「有备份」到「真能恢复」

适用发行版：Ubuntu / Debian / CentOS / RHEL / Rocky / AlmaLinux。涉及 GNU tar 1.30+、rsync 3.x、MySQL 5.7/8.0、MariaDB 10.x。CentOS/RHEL 系默认没配好 `sudo`，先见 @references/server-init.md。每个知识点都是「**完整命令 + 为什么 + 坑点**」，命令标 ✅ 只读 / ⚠️ 有副作用 / 🚫 高危。

## 0. 先看清删除类命令的后果

**带 `rm` / `--delete` / `DROP` 的命令执行后数据不会自己回来。** 执行前必须确认影响范围、现做一份备份、想好回滚办法。

```bash
# ⚠️ 有副作用：备份前留快照，这是你该做的第一件事
cp -a /var/www/html /var/www/html.bak.$(date +%F)        # 小目录
# 🚫 高危：清空网站目录，删掉的东西不进回收站
rm -rf /var/www/html/*
# 🚫 高危：--delete 会让源端没有的目标端文件被同步删除，路径写错 = 删库
rsync -avz --delete -e ssh /var/www/ root@1.2.3.4:/root/empty/
```

**坑点**：`rm -rf /var/www/html/*` 匹配不到 `.` 开头的隐藏文件，重启后 `.htaccess` 缺失会让站点行为异常，要连带隐藏文件用 `rm -rf /var/www/html/{*,.[!.]*}`，更稳妥是直接用上面的 `cp -a` 代替删除。Linux 没有回收站，`rm` 只解除目录项，inode 一旦被新数据覆盖就永久丢失。

## 1. 3-2-1 策略的可执行版本

3-2-1 = **3 份副本、2 种介质、1 份异地**：① 生产目录原文 ② 本机打包 `tar.gz` ③ 异地一份（rsync 到另一台机 / 对象存储）；介质分别是本地磁盘与另一台机器的磁盘；异地那份必须在另一台物理机或不同可用区。

**坑点**：本地磁盘备份挡不住 `rm -rf`、挡不住硬盘坏、挡不住主机被格式化；❌「备份放 `/backup`，跟网站同一个 500G 系统盘」只有 1 份副本；❌「每天 crontab 备份到 `~/backup/`」同机同盘，被入侵时攻击者第一个找的就是这里。备份文件本身也要轮转，否则会被自己撑爆。

```bash
# ✅ 只读：确认你的备份到底分布在几台机器上
find / -maxdepth 3 -name "*.tar.gz" -mtime -30 2>/dev/null | head -20
```

## 2. tar 打包备份

### 2.1 整目录打包（标准写法）

```bash
# ⚠️ 有副作用：消耗磁盘空间
tar -czpf /backup/full-$(date +%F).tar.gz -C / --exclude=proc --exclude=sys --exclude=dev \
  --exclude=run --exclude=mnt --exclude=media --exclude=lost+found --exclude=tmp --exclude=backup .
```

**为什么必须带 `-p`**：tar 靠它记录并恢复权限、属主、时间戳。不带 `-p`，恢复出的目录 owner 会变成恢复时的用户，Nginx/Apache 读不到文件直接 403/500；加了 `-p` 就必须用 root 解包。

**坑点（exclude 路径写法陷阱，必读）**：`--exclude=proc` 和 `--exclude=/proc` **不等价**——命令行排除项带前导 `/`，而打包起点是相对路径 `.`，两者对不上，排除失效，`/proc` 会被整块塞进包里。唯一正确组合是**起点写 `-C / .`、排除项一律不带斜杠**（上面那行已正确）。直接 `tar -czpf x.tar.gz /` 同样让 `/proc` 排除失效且包内文件名带 `./` 前缀；`--exclude=logs` 会匹配**任意层级**叫 `logs` 的目录，精确排除用 `--exclude=/var/log/nginx/*` 配合 `-C /` 写法。`-J`（xz）更小更慢，注意恢复参数配套。

### 2.2 只备份网站目录（更常用）

```bash
# ⚠️ 有副作用
tar -czpf /backup/www-$(date +%F).tar.gz -C /var/www html   # 还原：tar -xzpf ... -C /var/www
```

**坑点**：tar 默认不保存 ACL、xattr、SELinux 上下文（需 `--acls --xattrs --selinux`），恢复带 SELinux 的 CentOS 站点会出现「文件都在但 403」，补一句 `restorecon -Rv /var/www/html`。

## 3. mysqldump：数据库备份与恢复

### 3.1 备份单个库

```bash
# ⚠️ 有副作用：生产库务必加参数，否则导出期间网站写不进数据
mysqldump -u root -p --single-transaction --routines --triggers \
  --default-character-set=utf8mb4 appdb > /backup/appdb-$(date +%F).sql
```

**为什么必须加 `--single-transaction`**：它在一致性快照里导出。不加则逐表锁，InnoDB 长事务、MyISAM 直接锁全表，导出期间订单、支付记录写不进去。

**坑点**：🚫 `--single-transaction` 不能和 `--lock-tables` 同时用（互斥，同时写会报错退出，InnoDB 用前者、纯 MyISAM 才用后者）；只写 `-p` 会在终端**明文回显密码**，脚本里改用权限 600 的 `~/.my.cnf`，命令写成 `mysqldump -u root appdb`；MySQL 8.0 常需加 `--no-tablespaces` 规避 `ERROR 14127`。

### 3.2 全库备份

```bash
# ⚠️ 有副作用
mysqldump -u root -p --single-transaction --all-databases \
  --routines --triggers --events > /backup/all-$(date +%F).sql
```

**坑点**：恢复时目标库必须不存在或为空，否则撞表名；`CREATE DATABASE` 写死了库名，不会自动改名。

### 3.3 恢复：顺序错了就白备份了

```bash
# ⚠️ 有副作用：以下会覆盖/新建数据，执行前务必先 dump 一份
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS appdb DEFAULT CHARACTER SET utf8mb4;"
mysql -u root -p -f appdb < /backup/appdb-2025-01-20.sql
gunzip -c /backup/appdb-2025-01-20.sql.gz | mysql -u root -p -f appdb
```

**为什么/坑点**：`mysql dbname < file` 不会创建库，库不存在直接报 `Unknown database`，所以必须先建库；导入前不要先手动 `DROP TABLE`；确认磁盘余量 > 备份文件大小 ×2，大库导入加 `--force` 并盯 `SHOW PROCESSLIST`，否则会 `Got timeout reading communication packets`。

## 4. rsync：异地备份与镜像

```bash
# ⚠️ 有副作用：会按参数修改目标端
rsync -avz --delete -e ssh /var/www/ root@backup.example.com:/backup/www/
# 只增不减版，新手先用这行
rsync -avz -e ssh /var/www/ root@backup.example.com:/backup/www/
```

**为什么/坑点**：只传变化过的部分，跨公网备份比 scp 快一个量级；但 `--delete` 会删除目标端存在而源端没有的文件，路径写错或把源与目标写反就是清空远端。`rsync -avz /var/www 目标:/dst/` 同步的是 `www` 目录本身，`rsync -avz /var/www/ 目标:/dst/` 同步目录内容，**尾部斜杠不能省**。首次同步先去掉 `--delete` 确认列表对得上，拿不准用 `--dry-run` 预演。

## 5. 快照与对象存储：什么时候不该自建备份

云快照/镜像：一键、整机级，但按量计费、粒度是整机不是单文件，适合系统盘整机回滚；自建 rsync 到第二台：便宜可控、增量，但要维护两台机器并自己加密，适合网站目录、配置、小库；对象存储（OSS/COS/S3）：异地、抗误删、可开版本控制，但要配鉴权、小文件多时慢，适合重要数据的第三份。

**取舍原则**：快照不是日常备份手段，它定时给整盘拍一张，你删掉文件后拍的快照里那个文件也没了，只适合「改坏了配置、整机回滚到昨天」。

**坑点**：对象存储的**版本控制/回收站**要主动开启，否则覆盖即丢失；跨公网裸传等于明文丢数据，务必加密且口令不要和备份同机：

```bash
# ⚠️ 有副作用
tar -czpf - -C /var/www html | openssl enc -aes-256-cbc -pbkdf2 -out /backup/www.enc
openssl enc -d -aes-256-cbc -pbkdf2 -in /backup/www.enc | tar -xzp -C /var/www
```

## 6. 7 天 / 30 天滚动：别让备份把磁盘撑爆

```bash
# ⚠️ 有副作用：会真正删除旧备份文件
tar -czpf /backup/www-$(date +%F).tar.gz -C /var/www html
find /backup -name "www-*.tar.gz" -mtime +7 -delete   # 只留 7 天
find /backup -name "all-*.sql"    -mtime +30 -delete   # 只留 30 天
[ "$(df -P /backup | awk 'NR==2{print $5}' | tr -d '%')" -gt 80 ] && echo "disk full, skip" && exit 1
```

**坑点**：`-mtime +7` 按修改时间算，不是「第 7 天」。以上会真正删除旧备份文件（⚠️）。

## 7. 恢复演练：没演练过的备份不算备份

备份文件可能损坏、包名/库名记错、版本不兼容、解压后 owner 不对，只有真恢复一次才发现；超过半年没恢复过的备份失败率极高，每月低峰期演练一次。

```bash
# ✅ 只读，先确认包在、能列出来（不解压）
tar -tzpf /backup/www-2025-01-20.tar.gz | head -20
tar -tzpf /backup/www-2025-01-20.tar.gz | wc -l
# ⚠️ 有副作用：装到临时目录验证内容，绝不直接覆盖生产目录
mkdir -p /tmp/restore-test && tar -xzpf /backup/www-2025-01-20.tar.gz -C /tmp/restore-test
diff -r /tmp/restore-test/var/www/html /var/www/html
# 数据库在测试机或从库演练（⚠️）
mysql -u root -p -e "DROP DATABASE IF EXISTS appdb_test; CREATE DATABASE appdb_test DEFAULT CHARACTER SET utf8mb4;"
mysql -u root -p appdb_test < /backup/appdb-2025-01-20.sql && mysql -u root -p -e "SELECT COUNT(*) FROM appdb_test.users;"
```

出事时的顺序：① 先止损切流量，别在故障中第一次尝试恢复 ② 评估时间点 ③ 恢复（先备份现状，哪怕现状是坏的） ④ 验证要挑真实业务数据对一下 ⑤ 记录耗时与失败原因。

## 8. 删错了怎么找回

### 8.1 立刻做的事（顺序不能反，越快成功率越高）

```bash
# ⚠️ 有副作用：这些操作本身会写入，越晚做成功率越低
systemctl stop nginx                    # 先停掉会写盘的服务
mount -o remount,ro /                   # 停不掉就只读挂载锁死（ext4/xfs）
lvcreate -l 5G -s -n snap1 /dev/mapper/vg0-root   # 有 LVM 立刻建快照
ls /backup/ /var/backups/               # ✅ 只读，看有没有现成备份
```

### 8.2 extundelete：能用但有硬限制

```bash
# ⚠️ 有副作用：必须在分区卸载或只读挂载后执行
command -v extundelete || apt-get install -y extundelete   # Ubuntu/Debian；CentOS/RHEL 包名常为 ext4undelete
extundelete /dev/sda1 --restore-file /var/www/html/index.html
extundelete /dev/sda1 --restore-directory /var/www/html/uploads
```

**坑点**：删掉的文件数据块还在，只是目录项没了，一旦有新写入覆盖就永久丢失，所以上面那几步顺序不能反。extundelete 只支持 ext4（开 `dir_index` 时部分场景受限），xfs / btrfs / overlayfs 上没有该工具；必须卸载或只读挂载；结果落在 `RECOVERED_FILES/`，文件名可能是随机串。生产环境建议提前装好，事发后再装，安装过程本身可能已写坏磁盘。

### 8.3 误删数据库表的应急顺序

先 `SHOW MASTER STATUS;` 记下 binlog 位点停住写入，找可用备份评估丢失量，再把结构恢复到临时库 `mysql -u root -p appdb_recover < /backup/appdb-2025-01-20.sql`，最后增量补齐（前提 binlog 没被 `PURGE` 清掉）：`mysqlbinlog --start-position=4 --stop-position=28123 /var/lib/mysql/mysql-bin.000012 > inc.sql`。比对无误再回到生产，全程记录每步——一上来就往生产库 `DROP TABLE`/`INSERT` 是二次伤害。

## 下一步

- 想让它每天自动跑 → @references/maintenance-tasks.md
- 防止再次误删、被入侵 → @references/server-security.md
- 磁盘满 / inode 爆了导致备份失败 → @references/logs-monitoring.md
- `No space left on device` 等报错速查 → @references/server-troubleshooting.md
