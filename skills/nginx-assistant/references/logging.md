# 日志体系

> 日志是 Nginx 排障的眼睛——默认格式不够用，先定义好格式再出问题。

## log_format 变量实战版

```nginx
# http 级定义
log_format main '$remote_addr - [$time_local] "$request" $status $body_bytes_sent '
                '"$http_referer" "$http_user_agent" '
                'rt=$request_time urt=$upstream_response_time '
                'up=$upstream_addr cache=$upstream_cache_status';

server {
    access_log /var/log/nginx/api.access.log main;
    # access_log off;              # 不想记（如健康检查刷日志）
    # access_log /dev/stdout main; # 容器部署写 stdout

    error_log /var/log/nginx/error.log warn;   # 级别：debug|info|notice|warn|error|crit
    # 生产用 warn（error 以上必须看）；排障临时开 debug（⚠️ 量巨大，别久开）
}
```

**必加的两个耗时变量**：
- `rt=$request_time`：NginX 处理全程（含等客户端）
- `urt=$upstream_response_time`：纯后端耗时——rt 高 urt 低 = 问题在客户端或 Nginx，urt 高 = 后端慢

## 常用分析管道（实测验证）

```bash
LOG=/var/log/nginx/access.log

# Top10 访问 IP
awk '{print $1}' $LOG | sort | uniq -c | sort -rn | head -10

# 每分钟请求量（找流量突刺）
awk '{print substr($4,2,17)}' $LOG | sort | uniq -c | sort -rn | head

# 状态码分布
awk '{print $9}' $LOG | sort | uniq -c | sort -rn

# 5xx 的 URL Top10
awk '$9 ~ /^5/ {print $7}' $LOG | sort | uniq -c | sort -rn | head

# 慢请求 Top10（urt 在倒数第二个字段时）
awk '{print $(NF-1), $7}' $LOG | grep urt= | sort -t= -k2 -rn | head
# 更稳的写法：log_format 里把 urt 放最后，awk '$NF+0 > 1 {print $NF, $7}'

# 各 URL 平均耗时
awk '{path=$7; split($0,a,"urt="); split(a[2],b," "); t=b[1]+0; sum[path]+=t; cnt[path]++}
     END {for (p in sum) printf "%.3fs %6d %s\n", sum[p]/cnt[p], cnt[p], p}' $LOG | sort -rn | head
```

## 日志切割（三种方式）

### 1. logrotate（Linux 标配）

```bash
# /etc/logrotate.d/nginx
/var/log/nginx/*.log {
    daily
    rotate 14
    compress
    delaycompress        # 最新一份不压（方便 tail）
    missingok
    notifempty
    # nginx 重开日志文件（USR1 信号：不中断服务，重开 fd）
    sharedscripts
    postrotate
        [ -f /var/run/nginx.pid ] && kill -USR1 $(cat /var/run/nginx.pid)
    endscript
}
```

### 2. 按天分文件（无需 logrotate）

```nginx
# ⚠️ 变量在 access_log 里可用（error_log 不行）
# 代价：每天新文件要重开 fd，流量极高时有一瞬抖动
access_log /var/log/nginx/api_$time_iso8601.log main;
# 更干净：用 map 提取日期部分
map $time_iso8601 $logdate { '~^(?<ymd>\d{4}-\d{2}-\d{2})' $ymd; default 'date-miss'; }
access_log /var/log/nginx/api_$logdate.log main;
```

### 3. 容器：写 stdout

```nginx
access_log /dev/stdout main;
error_log stderr warn;
# 由 Docker/K8s 的日志驱动轮转（log-opts max-size）
```

## 按状态码分文件 / 过滤

```nginx
# map 出错误标志
map $status $error_log_flag {
    ~^[45]  1;      # 4xx/5xx
    default 0;
}

server {
    # 全量日志
    access_log /var/log/nginx/all.log main;
    # 错误请求另存一份（倍速排障）
    access_log /var/log/nginx/error-only.log main if=$error_log_flag;
    # if= 参数：变量非空且非 "0" 才记录（实测：多个 access_log 指令可叠加，都记录）
}
```

## error_log 级别速查

| 级别 | 内容 | 什么时候用 |
|---|---|---|
| `error` | 错误（连接失败/配置错） | 最低要求，必看 |
| `warn` | 上游异常/限流触发等 | **生产推荐** |
| `notice` | 重载/启动等信息 | 默认级别 |
| `info` | 连接建立细节 | 排障 |
| `debug` | 全量处理流程 | 深度排障（⚠️ 生产勿久开，日志量 100 倍） |

## 日志不记录的坑

```nginx
# access_log off 要在正确上下文——location 级可覆盖 server 级
# 内嵌 location 会继承外部配置，但显式写了就以它为准
server {
    access_log /var/log/nginx/main.log;
    location /healthz {
        access_log off;          # ✅ 健康检查不刷日志（K8s 每 10s 探测一次会淹没有效日志）
    }
}
```
