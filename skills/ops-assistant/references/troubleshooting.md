# 疑难杂症速查手册（按报错原文索引）

## 磁盘/文件类

### `No space left on device` 但 `df -h` 显示有空间
```bash
df -i                              # inode 耗尽了（海量小文件）
lsof +L1 | head                    # 已删除但仍被进程占用的文件
# 解法：找到持有进程重启，或 : > file 清空内容保留句柄
```

### `Argument list too long`
`rm -f /tmp/log*` 文件太多撑爆命令行参数。解法：
```bash
find /tmp -name "log*" -delete
# 或 find /tmp -name "log*" | xargs -n 100 rm -f
```

### 文件系统只读（Read-only file system）
```bash
dmesg -T | grep -iE "error|remount"     # 多半是磁盘故障触发了保护性只读
# 应急：mount -o remount,rw /（⚠️ 尽快备份数据换盘）
```

## 网络类

### `Connection refused` vs `Connection timed out`
- **refused**（秒拒）：对端收到了但没监听 → 服务没启动/端口不对
- **timeout**（干等）：包被丢了 → 防火墙/安全组/路由

### `Too many open files`
```bash
cat /proc/<pid>/limits | grep "open files"   # 当前限制
ls /proc/<pid>/fd | wc -l                     # 当前用量
# 临时：ulimit -n 65535；永久：/etc/security/limits.conf + systemd 服务的 LimitNOFILE
```

### `Address already in use`
```bash
ss -tlnp | grep :8080              # 谁占了
# 或前个进程没退干净：TIME_WAIT 占着 → 应用设 SO_REUSEADDR，或等 60s
```

### DNS 偶发解析失败
systemd-resolved 缓存/UDP 丢包导致。`dig @8.8.8.8 domain` 对比本地 DNS；偶发型抓包 `tcpdump -nn port 53`。

## 进程/服务类

### systemd 服务起不来
```bash
systemctl status myapp             # 先看这里（含最近日志）
journalctl -u myapp -n 100 --no-pager    # 完整日志
journalctl -u myapp --since "10 min ago"
# 常见：ExecStart 路径错、User 不存在、LimitNOFILE 太小、依赖服务没起（After=）
```

### 进程莫名其妙消失（没日志没报错）
```bash
dmesg -T | grep -i oom             # 90% 是 OOM Killer
journalctl -k | grep -i killed
# 排查 cgroup 内存限制（容器/podman/systemd slice 都可能限制）
```

### crontab 不执行
```bash
grep CRON /var/log/cron | tail     # 跑了没（CentOS；Ubuntu 看 syslog）
# 依次排查：脚本权限 chmod +x → 绝对路径 → PATH 差异 → % 转义 → 结尾换行
```

## 数据库类

### MySQL `Too many connections`
```sql
SHOW STATUS LIKE 'Threads_connected';
SHOW VARIABLES LIKE 'max_connections';
-- 临时调大：SET GLOBAL max_connections = 500;
-- 根治：应用连接池配置（HikariCP maximumPoolSize 总和不能超过 max_connections 的 80%）
```

### 主从延迟飙升（Seconds_Behind_Master 大）
```sql
SHOW SLAVE STATUS\G     -- 看 SQL_Thread 卡在哪个语句
-- 常见：大事务/DDL/无索引的 UPDATE 在从库串行重放
```

### Redis 内存满了（OOM command not allowed）
```bash
redis-cli INFO memory | grep -E "used_memory_human|maxmemory"
redis-cli --bigkeys                  # 找大 key（扫描会阻塞，从库上做）
# 配置 maxmemory-policy allkeys-lru（缓存场景）
```

## Web/Nginx 类

| 状态码 | 含义 | 第一检查点 |
|---|---|---|
| 502 | 后端挂了/拒连 | 后端进程、端口监听 |
| 503 | 限流/无可用后端 | limit_req、upstream 健康 |
| 504 | 后端超时 | proxy_read_timeout、慢查询 |
| 499 | 客户端主动断开 | 用户取消/上游超时太短 |
| 413 | 请求体太大 | client_max_body_size |

## 容器类

### 容器里 `apt/yum` 不通但宿主机通
Docker 网桥 DNS 问题：`cat /etc/resolv.conf`（容器内）；自定义 network 时内嵌 DNS 127.0.0.11。

### K8s `OOMKilled` 但容器内 free 显示有内存
cgroup 统计口径不同：page cache 也算进 cgroup 内存。Java 应用 Xmx + 堆外 + JVM 自身 > limits 就杀。

### 镜像拉不下来
私有仓库：`docker login` / imagePullSecrets；网络：`docker pull` 到节点上手动试；tag 不存在：`docker manifest inspect repo:tag`。

## 硬件/云类

### 云主机 `%st`（steal time）高
宿主超卖偷 CPU——找云厂商换宿主机，或升级实例规格。

### 磁盘 IO 突然变慢
```bash
iostat -xz 1 5    # await 突增
# 云盘：查是否触发了 IOPS/吞吐上限（云监控）；突发性能型云盘积分耗尽
smartctl -a /dev/sda | grep -iE "reallocated|pending"   # 物理盘坏道预警
```

## 终极心法

```
1. 报错原文 > 二手描述——让用户贴原始错误，别猜
2. 时间对齐——故障时间点 + 变更记录 + 监控曲线放一起看
3. 二分法——从中间切开验证（如：本机 curl 通不通 → 网关通不通 → 对端通不通）
4. 回滚优先——拿不准就先回到上一个正常状态
```
