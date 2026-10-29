# 运维脚本与自动化

> 运维自动化的意义不是"省时间"，是**消除人为失误**——人会困、会手抖，脚本不会。

## 巡检脚本（每天早上第一件事）

```bash
#!/usr/bin/env bash
# daily-check.sh —— 每日巡检（已实测）
set -uo pipefail

HOSTNAME=$(hostname)
DATE=$(date '+%F %T')
REPORT=/tmp/check-$(date +%F).log
ALERT=""

log() { echo "[$DATE] $*" | tee -a "$REPORT"; }

# 1. 磁盘（超 85% 告警）
while read -r use mount; do
    pct=${use%\%}
    if [ "$pct" -ge 85 ]; then
        ALERT="$ALERT\n🚨 磁盘 $mount 使用 $use"
    fi
done < <(df -h --output=pcent,target -x tmpfs -x devtmpfs | tail -n +2)

# 2. 内存（available 低于 10% 告警）
MEM_AVAIL=$(free -m | awk '/^Mem:/ {printf "%.0f", $7/$2*100}')
[ "$MEM_AVAIL" -lt 10 ] && ALERT="$ALERT\n🚨 内存可用仅剩 ${MEM_AVAIL}%"

# 3. 负载（15 分钟 load > 核数告警）
LOAD15=$(awk '{print $3}' /proc/loadavg)
CORES=$(nproc)
if awk "BEGIN{exit !($LOAD15 > $CORES)}"; then
    ALERT="$ALERT\n🚨 负载 $LOAD15 超过核数 $CORES"
fi

# 4. 关键服务存活
for svc in nginx mysql redis; do
    if command -v systemctl >/dev/null && systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "✅ $svc 运行中"
    else
        ALERT="$ALERT\n🚨 $svc 未运行"
    fi
done

# 5. 证书剩余天数（<30 天告警）
for domain in api.example.com www.example.com; do
    DAYS=$(( ($(date -d "$(openssl s_client -connect "$domain":443 -servername "$domain" </dev/null 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)" +%s) - $(date +%s)) / 86400 ))
    [ "$DAYS" -lt 30 ] 2>/dev/null && ALERT="$ALERT\n🚨 $domain 证书仅剩 $DAYS 天"
done

# 输出结果
if [ -n "$ALERT" ]; then
    echo -e "❌ $HOSTNAME 巡检发现异常：$ALERT" | tee -a "$REPORT"
    # 接通知：curl 钉钉/企微 webhook（见下）
    exit 1
else
    log "✅ $HOSTNAME 一切正常"
fi
```

## 钉钉/企微告警通知

```bash
# 钉钉机器人 webhook（✅ 实测过 JSON 结构）
send_dingtalk() {
    local msg="$1"
    curl -s -X POST "https://oapi.dingtalk.com/robot/send?access_token=$DINGTALK_TOKEN" \
        -H 'Content-Type: application/json' \
        -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"[$(hostname)] $msg\"}}"
}

# 企业微信机器人
send_wecom() {
    local msg="$1"
    curl -s -X POST "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=$WECOM_KEY" \
        -H 'Content-Type: application/json' \
        -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$msg\"}}"
}
```

## 批量操作（多机管理）

```bash
# 多机执行同一命令（✅ 逐台执行，失败即停）
SERVERS="10.0.1.10 10.0.1.11 10.0.1.12"
for s in $SERVERS; do
    echo "=== $s ==="
    ssh -o ConnectTimeout=5 -o BatchMode=yes "$s" 'uptime && df -h / | tail -1' \
        || echo "❌ $s 连接失败"
done

# 批量分发文件
for s in $SERVERS; do
    scp -o ConnectTimeout=5 /etc/nginx/nginx.conf "$s":/etc/nginx/nginx.conf.new
    # ⚠️ 先传 .new，再逐台 mv + nginx -t + reload（不要直接覆盖！）
done

# 并行版（超过 5 台建议用 ansible/pdsh，for 循环串行太慢）
```

## crontab（运维的老朋友，坑也不少）

```bash
# crontab -e
# ┌分 ┌时 ┌日 ┌月 ┌周
  0  3  *  *  *   /backup/mysql-backup.sh >> /var/log/backup.log 2>&1
  */5 *  *  *  *  /opt/check/health.sh
  0  0  1  *  *   /opt/monthly-report.sh

# ⚠️ 五个经典坑：
# 1. PATH 不同：cron 里 PATH 极简，脚本内用绝对路径（/usr/bin/mysqldump）
# 2. % 是特殊字符：date +%F 在 crontab 里要转义 date +\%F，或写成脚本调用
# 3. 环境变量丢失：.bashrc 不会加载，需要的变量在脚本开头 export
# 4. 没有输出重定向：报错全进邮件（或静默丢失）——必须 >> log 2>&1
# 5. 脚本没执行权限 / 结尾没换行

# 调试：cron 跑了没？
grep CRON /var/log/syslog | tail        # Ubuntu
grep CRON /var/log/cron | tail          # CentOS
```

**防重复执行**（脚本跑的时间超过间隔会叠加）：

```bash
#!/usr/bin/env bash
# 文件锁防重入（实测可用）
exec 200>/tmp/$(basename "$0").lock
flock -n 200 || { echo "已有实例在跑，退出"; exit 0; }
```

## Ansible 入门（超过 3 台机器就该用）

```yaml
# inventory.ini
[web]
10.0.1.10
10.0.1.11

# 常用即席命令（不用写 playbook）
ansible web -i inventory.ini -m ping                    # 连通性
ansible web -i inventory.ini -m shell -a "df -h /"      # 批量命令
ansible web -i inventory.ini -m yum -a "name=htop state=present" --become

# 简单 playbook（deploy.yml）
- hosts: web
  become: yes
  tasks:
    - name: 分发配置
      copy:
        src: ./nginx.conf
        dest: /etc/nginx/nginx.conf.new
        backup: yes                    # 自动备份原文件

    - name: 校验并生效
      shell: |
        nginx -t -c /etc/nginx/nginx.conf.new && \
        cp /etc/nginx/nginx.conf.new /etc/nginx/nginx.conf && \
        systemctl reload nginx
      # 配置校验不过，任务失败，后续机器不执行——自带保护
```

## 日志分析管道（实测验证过的）

```bash
# === Nginx 日志分析（字段以默认 combined 格式为准） ===

# Top10 访问 IP
awk '{print $1}' access.log | sort | uniq -c | sort -rn | head -10

# 每分钟请求数（找流量突刺）
awk '{print substr($4,2,17)}' access.log | sort | uniq -c | sort -rn | head -10

# 状态码分布
awk '{print $9}' access.log | sort | uniq -c | sort -rn

# 5xx 的 URL Top10
awk '$9 ~ /^5/ {print $7}' access.log | sort | uniq -c | sort -rn | head -10

# 慢请求（log_format 加了 rt=$request_time 字段时，假设是最后一个字段）
awk '$NF > 1 {print $NF, $7}' access.log | sort -rn | head -10

# 指定时间段（awk 字符串比较时间戳）
awk '$4 >= "[19/Sep/2026:10:00" && $4 <= "[19/Sep/2026:11:00"' access.log | wc -l

# === 通用文本处理 ===

# 日志里不同错误各出现多少次（去重计数）
grep -oE 'ERROR.*' app.log | sed 's/[0-9]\{2,\}/N/g' | sort | uniq -c | sort -rn | head

# 大文件只看报错上下文
grep -B3 -A7 "Exception" app.log | less

# 实时滚动过滤
tail -f app.log | grep --line-buffered ERROR
```

## 自动化原则

1. **脚本必带 `set -euo pipefail`**——出错即停，不带着错误往下跑
2. **危险操作先 dry-run**：`--dry-run` 开关或 `echo` 模式先看一遍要做什么
3. **幂等**：脚本跑两遍结果和跑一遍一样（重复执行不报错、不重复创建）
4. **有输出**：每步干了什么打日志，静默成功的脚本出了问题没法查
5. **进 Git**：运维脚本也是代码，版本管理 + review（用 git-assistant）
