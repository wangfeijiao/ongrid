# MySQL 领域知识

分析 MySQL 错误日志 / 应用日志中的 MySQL 报错时对照本文件。识别特征：`ERROR 1045`、`Duplicate entry`、`Deadlock found`、`Lock wait timeout` 等。

## 1. 常见错误码速查

| 错误码 | 报错特征 | 含义 | 根因 | 修复方案 |
|--------|----------|------|------|----------|
| **1045** | `Access denied for user` | 认证失败 | 用户名/密码错 / 主机未授权 | 核对账号密码、检查 `user` 表 host 授权 |
| **1040** | `Too many connections` | 连接数耗尽 | 超过 `max_connections` / 连接泄漏 | 调大 `max_connections`、排查连接未释放 |
| **1062** | `Duplicate entry ... for key` | 唯一键冲突 | 重复写入 / 幂等缺失 | 加幂等处理，或 `INSERT ... ON DUPLICATE KEY` |
| **1064** | `syntax error` | SQL 语法错误 | 拼接错误 / 语法拼写 | 检查 SQL 拼接与语法 |
| **1114** | `The table ... is full` | 表已满 | 磁盘满 / 表大小超限 | 清理磁盘、检查表空间 |
| **1146** | `Table ... doesn't exist` | 表不存在 | 库名/表名错 / 未建表 | 核对表名、检查迁移是否执行 |
| **1205** | `Lock wait timeout exceeded` | 锁等待超时 | 长事务持有锁 / 大事务 | 定位长事务、缩短事务、优化加锁顺序 |
| **1213** | `Deadlock found when trying to get lock` | 死锁 | 加锁顺序不一致 | 统一加锁顺序、减小事务粒度 |
| **2003** | `Can't connect to MySQL server` | 连接失败 | 服务未启动 / 网络不通 | 检查 MySQL 进程与端口、网络连通性 |
| **2006** | `MySQL server has gone away` | 连接断开 | `wait_timeout` 过短 / 服务重启 | 调大 `wait_timeout`、检查服务稳定性 |
| **2013** | `Lost connection during query` | 查询中断 | 查询过大 / 网络中断 | 优化大查询、检查网络 |

## 2. 慢查询（Slow Query）相关

- `Slow Query` 日志特征：`Query_time: 3.5 Lock_time: 0.01 Rows_examined: 100000`；
- **Rows_examined 远大于 Rows_sent** → 缺索引 / 全表扫描 → 加索引；
- **Lock_time 高** → 锁竞争严重 → 优化事务与加锁；
- 反复出现同一 SQL 的慢查询 → 优先优化该 SQL。

## 3. 死锁（1213）与锁等待（1205）排查要点

- 两者常成对出现：先有 `Lock wait timeout`，严重后升级为 `Deadlock`；
- 根因常是「长事务 + 加锁顺序不一致」；
- 修复：缩短事务、统一 UPDATE 顺序、避免在事务内做外部调用。

## 4. 噪音日志（可忽略）

- 正常的连接建立/断开日志（如 `Connect` / `Quit`，取决于 `general_log` 是否开启）；
- 周期性健康检查 `SELECT 1` 的记录；
- 备份/主从同步的常规日志。

## 5. 因果链提示

- `2003 Can't connect` 高频 + 应用层 `connection refused` → 根因「MySQL 服务不可用」；
- `1205 Lock wait timeout` → `1213 Deadlock` → 根因「长事务/加锁顺序」；
- `1040 Too many connections` + 应用层连接池报错 → 根因「连接池耗尽/泄漏」。
