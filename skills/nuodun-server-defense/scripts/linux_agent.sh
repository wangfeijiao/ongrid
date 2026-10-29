#!/bin/sh
# =============================================================================
# linux_agent.sh — 只读巡检探针（Linux / UNIX）
#
# 用途：在目标主机上采集运维事实，输出为「key<TAB>编码:值」的扁平流，供本地解析。
#
# 设计约束（都是踩过坑才加上的）：
#   * 纯 POSIX sh：不依赖 bash / python / jq / gawk，AIX、Solaris、macOS、BSD 也能跑。
#   * 全量 base64 编码：避免多行值、特殊字符、非 UTF-8 编码在传输中被破坏；
#     没有 base64/openssl 时降级为明文并如实标记，绝不静默丢数据。
#   * 全程只读：不写文件、不改配置、不装软件、不对外发起业务请求（仅本机读取）。
#   * 分段容错：任何一段失败都只导致对应键缺失 —— 缺失在判读时是「未覆盖」，
#     而不是「没问题」，这个区别决定了报告可不可信。
#   * 重复键 = 列表，由本地解析器聚合。
#
# 用法（本机）： sh linux_agent.sh [--deep]
# 用法（远程）： ssh -p PORT user@host 'sh -s -- --deep' < linux_agent.sh
# =============================================================================

DEEP=0
[ "$1" = "--deep" ] && DEEP=1

# --------------------------------------------------------------------------
# 编码器与输出原语
# --------------------------------------------------------------------------
if command -v base64 >/dev/null 2>&1; then
  _ENC=b; _b64() { base64 2>/dev/null | tr -d '\n'; }
elif command -v openssl >/dev/null 2>&1; then
  _ENC=b; _b64() { openssl base64 2>/dev/null | tr -d '\n'; }
else
  _ENC=p; _b64() { tr '\n' '\001'; }
fi

kv() {  # kv KEY VALUE —— VALUE 可为多行
  printf '%s\t%s:%s\n' "$1" "$_ENC" "$(printf '%s' "$2" | _b64)"
}

pairs() {  # 从标准输入读 "key|value" 行，逐条输出 —— 替代手写前缀，保证编码一致
  while IFS= read -r _line; do
    [ -z "$_line" ] && continue
    kv "${_line%%|*}" "${_line#*|}"
  done
}

lines() {  # lines KEY —— 标准输入每行一个值，同键多值
  while IFS= read -r _l; do
    [ -n "$_l" ] && kv "$1" "$_l"
  done
}

have() { command -v "$1" >/dev/null 2>&1; }
rd()   { [ -r "$1" ] && cat "$1" 2>/dev/null; }
rout() {  # 带超时执行，没有 timeout 命令就直跑
  if have timeout; then timeout "${TIMEOUT_S:-45}" "$@" 2>/dev/null; else "$@" 2>/dev/null; fi
}

# --------------------------------------------------------------------------
# 0. 元信息
# --------------------------------------------------------------------------
kv agent.version "1.0"
kv agent.deep    "$DEEP"
kv agent.time    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
kv agent.uname   "$(uname -a 2>/dev/null)"

# --------------------------------------------------------------------------
# 1. 系统清单
# --------------------------------------------------------------------------
kv sys.kernel   "$(uname -r 2>/dev/null)"
kv sys.hostname "$(uname -n 2>/dev/null)"
kv sys.arch     "$(uname -m 2>/dev/null)"

if [ -r /etc/os-release ]; then
  kv sys.distro "$(grep -E '^(PRETTY_NAME|VERSION)=' /etc/os-release 2>/dev/null | tr '\n' ' ')"
elif [ -r /etc/redhat-release ]; then
  kv sys.distro "$(cat /etc/redhat-release 2>/dev/null)"
elif [ -r /etc/issue ]; then
  kv sys.distro "$(head -1 /etc/issue 2>/dev/null)"
fi

if [ -r /proc/uptime ]; then
  kv sys.uptime_s "$(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
elif have sysctl; then
  kv sys.boot_time "$(sysctl -n kern.boottime 2>/dev/null)"
fi

if [ -r /proc/cpuinfo ]; then
  kv sys.cpu_model "$(grep -m1 -E 'model name|Processor|Hardware' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')"
elif have sysctl; then
  kv sys.cpu_model "$(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
fi

_cpu=$(getconf _NPROCESSORS_ONLN 2>/dev/null)
[ -z "$_cpu" ] && _cpu=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)
[ -z "$_cpu" ] && have sysctl && _cpu=$(sysctl -n hw.ncpu 2>/dev/null)
kv sys.cpu_count "$_cpu"

if have systemd-detect-virt; then
  kv sys.virt "$(systemd-detect-virt 2>/dev/null)"
elif have virt-what; then
  kv sys.virt "$(virt-what 2>/dev/null | tr '\n' ',')"
fi

# 内存（Linux）
if [ -r /proc/meminfo ]; then
  awk -F: '
    /^MemTotal:/     {gsub(/[^0-9]/,"",$2); print "mem.total_kb|"$2}
    /^MemFree:/      {gsub(/[^0-9]/,"",$2); print "mem.free_kb|"$2}
    /^MemAvailable:/ {gsub(/[^0-9]/,"",$2); print "mem.avail_kb|"$2}
    /^Buffers:/      {gsub(/[^0-9]/,"",$2); print "mem.buffers_kb|"$2}
    /^Cached:/       {gsub(/[^0-9]/,"",$2); print "mem.cached_kb|"$2}
    /^Dirty:/        {gsub(/[^0-9]/,"",$2); print "mem.dirty_kb|"$2}
    /^SwapTotal:/    {gsub(/[^0-9]/,"",$2); print "mem.swap_total_kb|"$2}
    /^SwapFree:/     {gsub(/[^0-9]/,"",$2); print "mem.swap_free_kb|"$2}
    /^Committed_AS:/ {gsub(/[^0-9]/,"",$2); print "mem.committed_kb|"$2}
  ' /proc/meminfo 2>/dev/null | pairs
elif have vm_stat && have sysctl; then
  # macOS / BSD
  _ps=$(sysctl -n hw.pagesize 2>/dev/null); [ -z "$_ps" ] && _ps=4096
  _tot=$(sysctl -n hw.memsize 2>/dev/null)
  [ -n "$_tot" ] && kv mem.total_kb "$((_tot / 1024))"
  _free=$(vm_stat 2>/dev/null | awk '/Pages free/{gsub(/\./,"",$3); print $3}')
  [ -n "$_free" ] && kv mem.free_kb "$((_free * _ps / 1024))"
fi

# 分区
if df -PT >/dev/null 2>&1; then
  df -PT 2>/dev/null | awk 'NR>1 && $2 !~ /^(tmpfs|devtmpfs|squashfs|overlay|proc|sysfs|cgroup|autofs|fuse\.|nsfs|debugfs)/ {
    print "fs.row|" $1 "|" $2 "|" $3 "|" $4 "|" $5 "|" $7 }' | pairs
else
  df -kP 2>/dev/null | awk 'NR>1 {
    print "fs.row|" $1 "|?|" $2 "|" $3 "|" $4 "|" $6 }' | pairs
fi

# --------------------------------------------------------------------------
# 2. 性能采样（两次采样求差值）
# --------------------------------------------------------------------------
if [ -r /proc/loadavg ]; then
  kv perf.loadavg "$(cat /proc/loadavg 2>/dev/null)"
else
  kv perf.loadavg "$(uptime 2>/dev/null)"
fi

if [ -r /proc/stat ]; then
  _c1=$(grep '^cpu ' /proc/stat 2>/dev/null)
  sleep 1
  _c2=$(grep '^cpu ' /proc/stat 2>/dev/null)
  kv perf.cpu_stat "$_c1 ;; $_c2"
fi

if [ -r /proc/diskstats ]; then
  _s1=$(awk '$3 !~ /^(loop|ram|sr|dm-|fd)/ {print $3" "$6" "$10" "$13}' /proc/diskstats 2>/dev/null)
  sleep 1
  _s2=$(awk '$3 !~ /^(loop|ram|sr|dm-|fd)/ {print $3" "$6" "$10" "$13}' /proc/diskstats 2>/dev/null)
  kv perf.diskstats "$_s1 ;; $_s2"
fi

if [ -r /proc/vmstat ]; then
  kv perf.vmstat "$(grep -E '^(pgpgin|pgpgout|pswpin|pswpout|pgmajfault|oom_kill) ' /proc/vmstat 2>/dev/null)"
elif have vmstat; then
  kv perf.vmstat "$(rout vmstat 1 2 | tail -1)"
fi

for _k in net.core.somaxconn net.ipv4.tcp_max_syn_backlog vm.swappiness \
          net.ipv4.ip_local_port_range fs.file-max net.ipv4.tcp_tw_reuse \
          net.core.rmem_max net.core.wmem_max net.ipv4.tcp_max_tw_buckets \
          fs.inotify.max_user_watches; do
  _p="/proc/sys/$(printf '%s' "$_k" | tr '.' '/')"
  [ -r "$_p" ] && kv "perf.sysctl.$_k" "$(cat "$_p" 2>/dev/null)"
done

kv perf.ulimit_n "$(ulimit -n 2>/dev/null)"
[ -r /proc/sys/fs/file-nr ] && kv perf.file_nr "$(cat /proc/sys/fs/file-nr 2>/dev/null)"
[ -r /proc/sys/fs/inode-nr ] && kv perf.inode_nr "$(cat /proc/sys/fs/inode-nr 2>/dev/null)"

# inode 使用率（inode 耗尽同样会导致服务写失败）
df -Pi 2>/dev/null | awk 'NR>1 && $5+0 > 80 {print $6 "|" $5 "%"}' | lines "perf.inode_high"

# Top 进程（RSS 降序）
ps -eo pid,rss,comm,args 2>/dev/null | sort -k2 -rn 2>/dev/null | head -20 | \
  awk 'NF>2 {print $1 "|" $2 "|" substr($0, index($0,$4))}' | pairs

# --------------------------------------------------------------------------
# 3. 网络与监听
# --------------------------------------------------------------------------
if have ss; then
  rout ss -H -tulpn | awk 'NF>=5 {print $1 "|" $5 "|" $0}' | pairs
elif have netstat; then
  rout netstat -tulpn | awk '/^(tcp|udp)/ {print $1 "|" $4 "|" $0}' | pairs
elif have lsof; then
  rout lsof -nP -iTCP -sTCP:LISTEN | awk 'NR>1 {print "TCP|" $9 "|" $1}' | pairs
fi

# 已建立的外部连接（用于判断异常外联）
if [ "$DEEP" = "1" ]; then
  have ss && rout ss -H -tunp state established | head -40 | lines "net.established"
fi

# --------------------------------------------------------------------------
# 4. 安全基线
# --------------------------------------------------------------------------
# sshd 生效配置（含 drop-in 目录；后者会覆盖前者，本地按"最后出现者优先"判定）
for _f in /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf; do
  [ -r "$_f" ] || continue
  grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$_f" 2>/dev/null | \
    awk 'NF>=2 && $1 !~ /^#/ {print $1 "|" $2}' | lines "sec.sshd"
done
kv sec.sshd.port "$(rout sshd -T 2>/dev/null | awk '/^port /{print $2}' | head -1)"

# 账户
awk -F: '$3==0 {print $1}' /etc/passwd 2>/dev/null | lines "sec.uid0"
awk -F: '$2=="" {print $1}' /etc/shadow 2>/dev/null | lines "sec.empty_pw"
awk -F: '($3>=1000 && $3<65534)||$1=="root" {print $1 "|" $3 "|" $7}' /etc/passwd 2>/dev/null | pairs
awk -F: '$2 ~ /^\*/ {print $1}' /etc/shadow 2>/dev/null | lines "sec.locked_account"
grep -hE 'NOPASSWD' /etc/sudoers /etc/sudoers.d/* 2>/dev/null | grep -v '^[[:space:]]*#' | \
  lines "sec.sudo_nopasswd"
kv sec.sudoers_files "$(ls -1 /etc/sudoers.d/ 2>/dev/null | wc -l)"

# SSH 公钥（长期免密后门的主要形态）
for _h in /root /home/*; do
  for _ak in "$_h/.ssh/authorized_keys" "$_h/.ssh/authorized_keys2"; do
    [ -r "$_ak" ] || continue
    grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$_ak" 2>/dev/null | cut -c1-140 | \
      sed "s|^|$_h: |" | lines "sec.ssh_key"
  done
done

# 防火墙
if have ufw; then
  kv sec.fw.tool ufw
  kv sec.fw.state "$(rout ufw status | head -1)"
  kv sec.fw.rules "$(rout ufw status numbered | head -40)"
elif have firewall-cmd; then
  kv sec.fw.tool firewalld
  kv sec.fw.state "$(rout firewall-cmd --state)"
  kv sec.fw.rules "$(rout firewall-cmd --list-all)"
elif have iptables; then
  kv sec.fw.tool iptables
  kv sec.fw.rules "$(rout iptables -S | head -60)"
elif have pfctl; then
  kv sec.fw.tool pf
  kv sec.fw.state "$(rout pfctl -s info | head -2)"
else
  kv sec.fw.tool none
fi

# MAC
[ -r /sys/fs/selinux/enforce ] && kv sec.selinux "$(cat /sys/fs/selinux/enforce 2>/dev/null)"
have getenforce && kv sec.selinux_mode "$(getenforce 2>/dev/null)"
have aa-status && kv sec.apparmor "$(aa-status --enabled 2>/dev/null && echo enabled || echo disabled)"

# SSH 爆破强度
for _log in /var/log/auth.log /var/log/secure /var/log/messages; do
  [ -r "$_log" ] && kv sec.ssh_authfail "$(grep -c 'Failed password' "$_log" 2>/dev/null)"
done
have journalctl && kv sec.ssh_authfail_journal \
  "$(rout journalctl -u ssh -u sshd --since '24 hours ago' --no-pager | grep -c 'Failed password')"

# 补丁
if have yum;      then kv sec.updates.pending "$(rout yum -q check-update | grep -cE '^[a-zA-Z0-9]')"
elif have dnf;    then kv sec.updates.pending "$(rout dnf -q check-update | grep -cE '^[a-zA-Z0-9]')"
elif have apt-get;then kv sec.updates.pending "$(rout apt-get -s -o Debug::NoLocking=1 upgrade | grep -c '^Inst')"
elif have zypper; then kv sec.updates.pending "$(rout zypper -q lu | grep -c '^v ')"
fi
[ -f /var/run/reboot-required ] && kv sec.reboot_required "yes"
[ -f /run/reboot-required ]     && kv sec.reboot_required "yes"

# --------------------------------------------------------------------------
# 5. 恶意代码与持久化
# --------------------------------------------------------------------------
for _f in /etc/crontab /etc/cron.d/* /var/spool/cron/* /var/spool/cron/crontabs/*; do
  [ -f "$_f" ] || continue
  grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$_f" 2>/dev/null | sed "s|^|$_f: |" | lines "malware.cron"
done
for _d in /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly; do
  [ -d "$_d" ] || continue
  ls -1 "$_d" 2>/dev/null | sed "s|^|$_d/|" | lines "malware.cron_script"
done
have crontab && kv malware.crontab_user "$(rout crontab -l 2>/dev/null | grep -vE '^#|^$')"

# systemd 单元指向临时目录 / 含下载特征
if [ -d /etc/systemd/system ]; then
  for _u in /etc/systemd/system/*.service /etc/systemd/system/*/*.service; do
    [ -f "$_u" ] || continue
    _x=$(grep -m1 -E '^ExecStart=' "$_u" 2>/dev/null)
    case "$_x" in
      *"/tmp/"*|*"/var/tmp/"*|*"/dev/shm/"*|*"base64"*|*"curl "*|*"wget "*) kv malware.systemd "$_u: $_x" ;;
    esac
  done
  kv malware.systemd_unit_count "$(ls -1 /etc/systemd/system/*.service 2>/dev/null | wc -l)"
fi

[ -s /etc/ld.so.preload ] && kv malware.ld_preload "$(cat /etc/ld.so.preload 2>/dev/null)"
kv malware.ld_env "$(env 2>/dev/null | grep -E '^LD_(PRELOAD|LIBRARY_PATH)=' | tr '\n' ' ')"

for _d in /tmp /var/tmp /dev/shm; do
  [ -d "$_d" ] || continue
  rout find "$_d" -maxdepth 3 -type f -perm -u+x -size +1k | head -30 | lines "malware.tmp_exec"
done

rout find / -xdev -perm -4000 -type f 2>/dev/null | head -120 | lines "malware.suid"

kv malware.lsmod "$(rout lsmod | head -40)"

# /proc 可见进程数 vs ps 统计数（差值过大 = 可能有隐藏进程）
if [ -d /proc ]; then
  _np=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l | tr -d ' ')
  _ps=$(ps -e 2>/dev/null | wc -l | tr -d ' ')
  kv malware.proc_count "$_np/$_ps"
fi

# --------------------------------------------------------------------------
# 6. 日志与崩溃
# --------------------------------------------------------------------------
if have journalctl; then
  kv log.journal_err       "$(rout journalctl -p err -n 200 --since '48 hours ago' --no-pager)"
  kv log.journal_err_count "$(rout journalctl -p err --since '48 hours ago' --no-pager | wc -l)"
  kv log.oom               "$(rout journalctl -k --since '7 days ago' --no-pager | grep -iE 'Out of memory|oom-kill|Killed process' | tail -20)"
  kv log.segv              "$(rout journalctl --since '7 days ago' --no-pager | grep -icE 'segfault|general protection')"
  kv log.ioerr             "$(rout journalctl -k --since '7 days ago' --no-pager | grep -iE 'I/O error|blk_update_request|EXT4-fs error|nvme.*timeout|medium error' | tail -20)"
  kv crash.oops            "$(rout journalctl -k --no-pager | grep -iE 'kernel panic|Oops|BUG: |call trace' | tail -20)"
else
  for _f in /var/log/messages /var/log/syslog /var/log/system.log; do
    [ -r "$_f" ] && kv log.syslog_err "$(grep -iE 'error|fail|panic' "$_f" 2>/dev/null | tail -100)"
  done
fi

[ -d /var/crash ] && rout find /var/crash -type f 2>/dev/null | head -20 | lines "crash.core"
[ -d /var/lib/systemd/coredump ] && \
  rout find /var/lib/systemd/coredump -type f 2>/dev/null | head -20 | lines "crash.core"
rout find /tmp /var/tmp /opt /usr/local -maxdepth 3 -name 'hs_err_pid*.log' 2>/dev/null | \
  head -20 | lines "crash.hs_err"

# --------------------------------------------------------------------------
# 7. 应用栈
# --------------------------------------------------------------------------
have systemctl && kv app.units \
  "$(rout systemctl list-units --type=service --state=running --no-pager --plain --no-legend | head -60)"
kv app.listen_summary "$(rout ss -H -tuln 2>/dev/null | awk '{print $1":"$5}' | head -60 | tr '\n' ' ')"

ps -eo pid,args 2>/dev/null | grep -E 'java|tomcat' | grep -v grep | head -15 | lines "app.java"

have nginx        && kv app.nginx "$(rout nginx -v 2>&1)"
have httpd        && kv app.httpd "$(rout httpd -v | head -1)"
have apachectl    && kv app.apachectl "$(rout apachectl -v | head -1)"
have mysql        && kv app.mysql "$(rout mysql --version)"
have redis-server && kv app.redis "$(rout redis-server --version)"
have psql         && kv app.postgres "$(rout psql --version)"
have docker       && kv app.docker "$(rout docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}' | head -30)"

# --------------------------------------------------------------------------
# 8. 深扫（--deep）
# --------------------------------------------------------------------------
if [ "$DEEP" = "1" ]; then
  # 可执行文件被删除但进程仍在 → 无文件落地 / 内存马的强特征
  for _p in /proc/[0-9]*; do
    [ -d "$_p" ] || continue
    _exe=$(readlink "$_p/exe" 2>/dev/null)
    case "$_exe" in
      *"(deleted)"*) kv malware.deleted_exe "PID $(basename "$_p"): $_exe" ;;
    esac
  done
  # 近 30 天新增的隐藏文件
  rout find / -xdev -maxdepth 3 -name '.*' -type f -newermt '-30 days' 2>/dev/null | head -40 | \
    lines "malware.hidden"
  # 近 7 天新增的可执行文件（排除系统目录）
  rout find / -xdev -type f -perm -u+x -newermt '-7 days' 2>/dev/null | \
    grep -vE '^/(proc|sys|dev|run|snap|var/lib)' | head -60 | lines "malware.recent_exec"
  # 认证日志要点
  kv log.auth_tail "$(rout tail -200 /var/log/auth.log /var/log/secure | grep -iE 'sudo|new user|useradd|passwd|Failed|Accepted' | tail -60)"
fi

kv agent.done "1"
exit 0
