# 常见错误模式速查表

> 本文件为故障日志诊断专家的参考资料，在分析对应技术栈的报错时可查阅。按技术栈分类，每条包含：典型报错信息、最可能根因、第一步排查命令。

## 目录

- [Java](#java)
- [Python](#python)
- [Go](#go)
- [数据库](#数据库)
- [Redis](#redis)
- [消息队列](#消息队列)
- [HTTP / 网关](#http--网关)
- [数仓 / 大数据](#数仓--大数据)
- [操作系统 / 资源](#操作系统--资源)

---

## Java

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `java.lang.NullPointerException` | 对象为 null 时调用方法/属性 | 查看堆栈最顶层行号，检查该对象来源 |
| `java.lang.OutOfMemoryError: Java heap space` | 堆内存不足，可能有内存泄漏或大对象 | `jmap -heap <pid>`、`jstat -gcutil <pid> 1s` |
| `java.lang.OutOfMemoryError: Metaspace` | 元空间不足，类加载过多或动态类泄漏 | 检查是否大量动态生成类（CGLIB/反射） |
| `java.lang.StackOverflowError` | 递归调用过深或无限递归 | 查看堆栈中重复出现的方法 |
| `java.net.ConnectException: Connection refused` | 目标服务端口无进程监听 | `nc -zv <host> <port>` |
| `java.net.SocketTimeoutException: Read timed out` | 下游服务响应慢，超过 read timeout | 检查下游服务负载和慢查询 |
| `org.springframework.beans.factory.BeanCreationException` | Spring Bean 初始化失败，通常是依赖注入问题 | 查看 nested exception，找到根本原因 |
| `com.netflix.hystrix.exception.HystrixRuntimeException` | 熔断器打开，下游服务不可用 | 检查下游服务状态和熔断配置 |
| `java.sql.SQLException: Too many connections` | 数据库连接数达到上限 | `SHOW PROCESSLIST;`、检查连接池配置 |
| `org.apache.kafka.common.errors.SerializationException` | 消息序列化/反序列化失败 | 检查消息格式和序列化器配置 |

## Python

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `KeyError: 'xxx'` | 字典中不存在该 key | 检查字典实际包含的 key：`print(dict.keys())` |
| `AttributeError: 'NoneType' object has no attribute 'xxx'` | 对象为 None 时调用属性/方法 | 检查对象为何为 None（函数返回值、查询结果） |
| `ModuleNotFoundError: No module named 'xxx'` | 依赖包未安装或环境不对 | `pip list | grep xxx`、确认虚拟环境 |
| `requests.exceptions.ConnectTimeout` | TCP 连接阶段超时（目标不可达） | `curl --connect-timeout 5 <url>` |
| `requests.exceptions.ReadTimeout` | 连接成功但响应超时（下游慢） | 检查下游服务响应时间 |
| `requests.exceptions.ConnectionError` | 连接失败（DNS/拒绝连接/网络中断） | `nslookup <host>`、`nc -zv <host> <port>` |
| `requests.exceptions.ConnectionError: Max retries exceeded` | requests 重试达到上限（DNS 解析失败/目标不可达/TLS 握手失败/连接超时） | `nslookup <host>`、`curl -v <url>` 观察握手阶段 |
| `BrokenPipeError: [Errno 32] Broken pipe` | 对端已关闭连接，仍在写入数据 | 检查下游服务是否重启/崩溃 |
| `psycopg2.OperationalError: could not connect to server` | PostgreSQL 连接失败 | 检查 PG 服务状态和连接配置 |
| `pymysql.err.OperationalError: (2003, "Can't connect to MySQL server")` | MySQL 连接失败 | `nc -zv <host> 3306` |
| `celery.exceptions.TimeoutError` | Celery 任务执行超时 | 检查任务执行时间和超时配置 |
| `RuntimeError: dictionary changed size during iteration` | 遍历字典时修改了字典 | 检查代码中是否在循环内增删字典元素 |

## Go

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `panic: runtime error: invalid memory address or nil pointer dereference` | nil 指针解引用 | 查看 panic 堆栈，定位 nil 变量 |
| `panic: runtime error: index out of range` | 数组/切片越界 | 检查切片长度和访问索引 |
| `context deadline exceeded` | 上下文超时（HTTP/DB/RPC 调用超时） | 检查超时配置和下游服务延迟 |
| `connection reset by peer` | 对端重置连接（服务崩溃/防火墙拦截） | 检查对端服务状态和网络 |
| `too many open files` | 文件描述符达到上限 | `ulimit -n`、`lsof -p <pid> | wc -l` |
| `dial tcp: i/o timeout` | TCP 连接超时 | `nc -zv <host> <port>` |
| `goroutine stack exceeds 1000000000-byte limit` | goroutine 栈溢出（无限递归） | 查看堆栈中重复的函数调用 |

## 数据库

### MySQL / PostgreSQL 通用

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `ERROR: deadlock detected` / `Deadlock found when trying to get lock` | 事务死锁 | 查看死锁日志，分析事务加锁顺序 |
| `Too many connections` | 连接数达到 max_connections | `SHOW PROCESSLIST;`、检查连接池 |
| `Duplicate entry 'xxx' for key 'yyy'` | 唯一约束冲突 | 检查业务逻辑是否重复写入 |
| `statement timeout` / `query timeout` | SQL 执行超过超时阈值 | `EXPLAIN ANALYZE <SQL>` |
| `relation "xxx" does not exist` / `Table 'xxx' doesn't exist` | 表不存在（未创建/被删/库名错） | `SHOW TABLES;` / `\dt` |
| `permission denied for relation xxx` | 权限不足 | 检查用户权限：`SHOW GRANTS;` |
| `could not serialize access due to concurrent update` | 并发更新冲突（可串行化隔离级别） | 检查事务隔离级别和重试机制 |

### 特定数据库

| 数据库 | 典型报错 | 最可能根因 |
|--------|----------|-----------|
| PostgreSQL | `canceling statement due to conflict with recovery` | 备库查询与 WAL 回放冲突 |
| MySQL | `Lock wait timeout exceeded` | 行锁等待超时 |
| Oracle | `ORA-01000: maximum open cursors exceeded` | 游标未关闭泄漏 |
| MongoDB | `MongoServerError: E11000 duplicate key error` | 唯一索引冲突 |

## Redis

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `OOM command not allowed when used memory > 'maxmemory'` | 内存达到上限且策略为 noeviction | `INFO memory`、`CONFIG GET maxmemory-policy` |
| `LOADING Redis is loading the dataset in memory` | Redis 正在启动加载数据 | 等待启动完成，检查 RDB/AOF 文件大小 |
| `MOVED 12345 10.0.0.1:6379` | Cluster 模式下 key 不在当前节点 | 使用集群客户端自动重定向，或 `CLUSTER KEYSLOT <key>` |
| `Connection refused` | Redis 服务未运行 | `systemctl status redis`、`ss -tlnp | grep 6379` |
| `READONLY You can't write against a read only replica` | 向从节点写入数据 | 写入应走主节点，检查客户端配置 |
| `MISCONF Redis is configured to save RDB snapshots, but is currently not able to persist on disk` | RDB 持久化失败（磁盘满/权限问题） | `df -h`、检查 Redis 数据目录权限 |
| `ERR max number of clients reached` | 客户端连接数达到上限 | `CLIENT LIST`、检查 maxclients 配置 |

## 消息队列

### RocketMQ

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `The broker does not exist` | Broker 未注册到 NameServer | 检查 Broker 状态和 NameServer 地址 |
| `Service not available now` | Broker 正在关闭或不可用 | 检查 Broker 进程和磁盘空间 |
| `CODE: 2 DESC: No route info of this topic` | Topic 不存在或未创建 | 检查 Topic 是否存在，开启自动创建 |
| 消费失败 + 重试次数增加 | 消费端处理异常或超时 | 查看消费端错误日志，检查消费超时配置 |

### Kafka

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `RebalanceInProgressException` | 消费者组正在重平衡 | 检查消费者是否频繁加入/退出，增加 session.timeout |
| `TimeoutException: Failed to update metadata after 60000 ms` | 无法连接 Broker 获取元数据 | 检查 Broker 地址和网络连通性 |
| `RecordTooLargeException` | 消息大小超过 max.message.bytes | 检查消息大小和 Broker 配置 |
| `NotEnoughReplicasException` | 同步副本数不足 | 检查 ISR 列表和 Broker 状态 |
| 消费延迟（Lag 持续增长） | 消费速度 < 生产速度 | 增加消费者实例、优化消费逻辑、检查下游依赖 |

## HTTP / 网关

| 状态码/报错 | 最可能根因 | 第一步排查 |
|-------------|-----------|-----------|
| `500 Internal Server Error` | 应用内部异常（代码 bug/依赖失败） | 查看应用错误日志和堆栈 |
| `502 Bad Gateway` | 网关/反向代理无法从上游获得响应 | 检查上游服务状态和端口 |
| `503 Service Unavailable` | 服务不可用（过载/维护/熔断） | 检查服务实例数、负载、熔断状态 |
| `504 Gateway Timeout` | 网关等待上游响应超时 | 检查上游服务响应时间和慢查询 |
| `429 Too Many Requests` | 请求频率超过限流阈值 | 检查限流配置和 QPS |
| `upstream prematurely closed connection` | 上游在响应完成前关闭连接 | 检查上游是否崩溃/重启/OOM |
| `connect() failed (111: Connection refused)` | 上游端口无进程监听 | `ss -tlnp | grep <port>` |
| `SSL certificate problem: certificate has expired` | SSL 证书过期 | 检查证书有效期：`openssl s_client -connect host:443` |

## 数仓 / 大数据

### Spark / Hive

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `Table or view not found: xxx` | 表不存在（库名错/表被删/Metastore 连错） | `SHOW TABLES IN <db>;`、检查 Metastore |
| `Partition not found` | 查询的分区不存在 | `SHOW PARTITIONS <table>;` |
| `org.apache.spark.shuffle.FetchFailedException` | Shuffle 数据拉取失败（Executor 崩溃/网络问题） | 检查 Executor 日志，增加 shuffle 重试 |
| `java.lang.OutOfMemoryError: Java heap space` (Executor) | Executor 内存不足（数据倾斜/大表 Join） | 检查数据倾斜，增加 executor-memory |
| `Container killed by YARN for exceeding memory limits` | 容器内存超限（堆外内存+堆内存） | 增加 spark.executor.memoryOverhead |
| `FileNotFoundException` | HDFS 文件不存在（被删除/路径错） | `hdfs dfs -ls <path>` |
| `AnalysisException: cannot resolve 'xxx' given input columns` | SQL 中引用了不存在的列 | 检查列名拼写和表结构 |
| `ClassNotFoundException: org.apache.hive.jdbc.HiveDriver` | 缺少 JDBC 驱动依赖 | 检查依赖包是否在 classpath |

### Flink

| 典型报错 | 最可能根因 |
|----------|-----------|
| `Checkpoint expired before completing` | Checkpoint 超时（背压/大状态） |
| `BackPressure`（Web UI 显示高背压） | 消费速度 > 处理速度，下游瓶颈 |
| `java.util.concurrent.TimeoutException: Slot request timeout` | TaskManager 资源不足或心跳丢失 |

## 操作系统 / 资源

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `Out of memory: Kill process xxx (java) score xxx` | OOM Killer 杀掉进程（系统内存不足） | `dmesg | grep -i "killed process"`、检查内存使用 |
| `No space left on device` | 磁盘满 | `df -h`、`du -sh /* | sort -rh | head -10` |
| `Too many open files` | 文件描述符达到上限 | `ulimit -n`、`lsof -p <pid> | wc -l` |
| `Resource temporarily unavailable` | 进程数/线程数达到上限 | `ulimit -u`、检查进程数 |
| `Permission denied` | 权限不足 | `ls -la <file>`、检查用户和组权限 |
| `Device or resource busy` | 文件/设备被占用 | `lsof <file>`、`fuser <file>` |
| `Connection timed out` | 网络连接超时（防火墙/路由/服务不可达） | `traceroute <host>`、`nc -zv <host> <port>` |

## 中文告警

| 典型报错 | 最可能根因 | 第一步排查 |
|----------|-----------|-----------|
| `【告警】…接口响应超时` / `登录接口返回500…服务不可用` | 中文日志/告警明确指向接口超时或服务不可用 | 查看服务状态、下游依赖性能与网络链路，APM 定位超时环节 |
| `kafka: client has run out of available brokers` | Kafka 客户端找不到可用 broker（broker 宕机/网络不通/配置错误） | 检查 bootstrap.servers、broker 进程与 `nc -zv <broker> <port>` |
| `【告警】CPU/内存/磁盘使用率超过阈值` | 资源使用率超阈值 | `top`/`free`/`df -h` 确认资源，排查高资源进程 |

---

## 使用说明

1. 分析日志时，先在预处理阶段识别报错属于哪个技术栈，再查阅对应分类。
2. 表中的「最可能根因」是经验性高概率判断，**必须结合实际日志证据确认**，不能直接作为结论。
3. 「第一步排查」命令是快速验证手段，排查清单中应给出更完整的步骤。
4. 如果报错模式不在表中，按通用流程分析：提取报错关键词 → 搜索官方文档 → 结合上下文推断根因。
