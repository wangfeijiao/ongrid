#!/bin/sh
# =============================================================================
# net_agent.sh — 只读「上网提速」诊断探针（Linux / UNIX）
#
# 与 linux_agent.sh 同格式：输出「key<TAB>编码:值」扁平流，本地解析。
# 分文件而不是塞进主探针，是因为网络诊断要跑 ping / 解析计时，耗时明显更长，
# 不该拖慢每次常规体检。
#
# 设计约束：
#   * 纯 POSIX sh，不依赖 bash / jq / gawk；AIX、Solaris、macOS、BSD 也能跑。
#   * **全程只读**：不写 sysctl、不改路由、不装包。
#   * 会发起网络探测（ping / DNS 查询），但只发往用户指定的诊断目标，
#     不上传任何本机数据。没有 ping 工具就如实缺失，不编数据。
#   * 分段容错：某段失败只导致对应键缺失（= 未覆盖），绝不返回默认值造假。
#
# 用法（本机）： sh net_agent.sh
# 用法（远程）： ssh -p PORT user@host 'sh -s' < net_agent.sh
# =============================================================================

TIMEOUT_S=${TIMEOUT_S:-8}

if command -v base64 >/dev/null 2>&1; then
  _ENC=b; _b64() { base64 2>/dev/null | tr -d '\n'; }
elif command -v openssl >/dev/null 2>&1; then
  _ENC=b; _b64() { openssl base64 2>/dev/null | tr -d '\n'; }
else
  _ENC=p; _b64() { tr '\n' '\001'; }
fi

kv() { printf '%s\t%s:%s\n' "$1" "$_ENC" "$(printf '%s' "$2" | _b64)"; }
have() { command -v "$1" >/dev/null 2>&1; }
rd() { [ -r "$1" ] && cat "$1" 2>/dev/null; }
rout() {
  if have timeout; then timeout "$TIMEOUT_S" "$@" 2>/dev/null; else "$@" 2>/dev/null; fi
}
rout2() {  # rout2 SECONDS cmd...
  _t="$1"; shift
  if have timeout; then timeout "$_t" "$@" 2>/dev/null; else "$@" 2>/dev/null; fi
}

kv agent.version "1.0"
kv agent.kind    "net"
kv agent.time    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)"
kv net.hostname  "$(uname -n 2>/dev/null)"
kv net.kernel    "$(uname -r 2>/dev/null)"

# 探针可用性 —— 缺工具就说缺，不要装作查过了
for _t in ping dig nslookup traceroute host ethtool ss netstat iw iwconfig ip ifconfig resolvectl; do
  if have "$_t"; then kv net.tool "$_t"; fi
done

# --------------------------------------------------------------------------
# 1. 接口清单（速率 / MTU / 状态 / 地址）
# --------------------------------------------------------------------------
if have ip; then
  ip -o link show 2>/dev/null | while IFS= read -r _l; do
    _if=$(printf '%s' "$_l" | sed -n 's/^[0-9]*: \([^:@]*\).*/\1/p')
    _st=$(printf '%s' "$_l" | sed -n 's/.*state \([A-Z]*\).*/\1/p')
    _mtu=$(printf '%s' "$_l" | sed -n 's/.*mtu \([0-9]*\).*/\1/p')
    _mac=$(printf '%s' "$_l" | sed -n 's/.*link\/ether \([0-9a-fA-F:]*\).*/\1/p')
    _ip4=$(ip -4 -o addr show dev "$_if" 2>/dev/null | awk '{print $4}' | head -1)
    [ -z "$_if" ] && continue
    kv net.if "${_if}|${_st:-?}|${_mtu:-?}|${_mac:-?}|${_ip4:-none}"
  done
elif have ifconfig; then
  ifconfig -a 2>/dev/null | awk '
    /^[a-zA-Z]/ { if (n) print n"|"s"|"m"|"a; n=$1; s="?"; m="?"; a="none" }
    /[Uu][Pp] /  { s="UP" }
    /mtu /       { for(i=1;i<=NF;i++) if($i=="mtu") m=$(i+1) }
    /inet /      { if (a=="none") a=$2 }
    END          { if (n) print n"|"s"|"m"|"a }' 2>/dev/null \
  | while IFS= read -r _r; do
      _name=${_r%%|*}
      [ -z "$_name" ] && continue
      kv net.if "${_name}|$(printf '%s' "$_r" | cut -d'|' -f2)|$(printf '%s' "$_r" | cut -d'|' -f3)||$(printf '%s' "$_r" | cut -d'|' -f4)"
    done
fi

# 协商速率（ethtool 能读到真实链路速率，容器/虚拟网卡读不到是正常的）
if have ethtool && have ip; then
  for _if in $(ls /sys/class/net 2>/dev/null); do
    _sp=$(rout2 4 ethtool "$_if" 2>/dev/null | sed -n 's/.*Speed: \([0-9]*\)Mb\/s.*/\1/p' | head -1)
    [ -n "$_sp" ] && kv net.if_speed "${_if}|${_sp}"
    _dup=$(rout2 4 ethtool "$_if" 2>/dev/null | sed -n 's/.*Duplex: \([A-Za-z]*\).*/\1/p' | head -1)
    [ -n "$_dup" ] && kv net.if_duplex "${_if}|${_dup}"
    # 节能以太网：笔记本上开着会明显掉速/断流
    _eee=$(rout2 4 ethtool --show-eee "$_if" 2>/dev/null | sed -n 's/^EEE status: \(.*\)$/\1/p' | head -1)
    [ -n "$_eee" ] && kv net.if_eee "${_if}|${_eee}"
  done
fi

# --------------------------------------------------------------------------
# 2. 路由与 DNS
# --------------------------------------------------------------------------
if have ip; then
  _gw=$(ip route show default 2>/dev/null | head -1)
  [ -n "$_gw" ] && kv net.default_route "$_gw"
elif have netstat; then
  _gw=$(netstat -rn 2>/dev/null | awk '/^default|^0\.0\.0\.0/ {print $2; exit}')
  [ -n "$_gw" ] && kv net.gateway "$_gw"
fi

if [ -r /etc/resolv.conf ]; then
  grep -E '^[[:space:]]*nameserver' /etc/resolv.conf 2>/dev/null \
    | awk '{print $2}' | while IFS= read -r _ns; do
      [ -n "$_ns" ] && kv net.dns "$_ns"
    done
  grep -E '^[[:space:]]*(search|options)' /etc/resolv.conf 2>/dev/null \
    | while IFS= read -r _o; do kv net.resolv_opt "$_o"; done
fi

if have resolvectl; then
  rout2 6 resolvectl status 2>/dev/null \
    | grep -E 'DNS Servers|DNS Domain|DNSSEC|Current DNS' \
    | while IFS= read -r _r; do kv net.resolved "$_r"; done
fi

# DNS 劫持/污染线索：劫持常见做法是在 hosts 里塞私货
if [ -r /etc/hosts ]; then
  grep -vE '^[[:space:]]*(#|$)' /etc/hosts 2>/dev/null | while IFS= read -r _h; do
    kv net.hosts "$_h"
  done
  kv net.hosts_count "$(grep -vcE '^[[:space:]]*(#|$)' /etc/hosts 2>/dev/null)"
fi

# 代理残留会让人以为"网慢"，其实流量绕了一大圈
_pe=$(env 2>/dev/null | grep -iE '^(http_proxy|https_proxy|ftp_proxy|all_proxy|no_proxy)=' )
[ -n "$_pe" ] && printf '%s\n' "$_pe" | while IFS= read -r _p; do kv net.proxy_env "$_p"; done

# --------------------------------------------------------------------------
# 3. TCP 内核参数（决定吞吐上限与拥塞行为）
# --------------------------------------------------------------------------
_SYSCTLS="
net.ipv4.tcp_congestion_control
net.core.default_qdisc
net.ipv4.tcp_available_congestion_control
net.ipv4.tcp_fastopen
net.ipv4.tcp_slow_start_after_idle
net.ipv4.tcp_notsent_lowat
net.ipv4.tcp_window_scaling
net.ipv4.tcp_timestamps
net.ipv4.tcp_sack
net.ipv4.tcp_dsack
net.ipv4.tcp_fack
net.ipv4.tcp_moderate_rcvbuf
net.ipv4.tcp_mtu_probing
net.ipv4.tcp_rmem
net.ipv4.tcp_wmem
net.core.rmem_max
net.core.wmem_max
net.core.netdev_max_backlog
net.core.somaxconn
net.ipv4.tcp_max_syn_backlog
net.ipv4.tcp_tw_reuse
net.ipv4.tcp_fin_timeout
net.ipv4.tcp_keepalive_time
net.ipv4.ip_local_port_range
net.ipv4.tcp_syncookies
"
for _k in $_SYSCTLS; do
  if [ -r "/proc/sys/$(printf '%s' "$_k" | tr '.' '/')" ]; then
    _v=$(cat "/proc/sys/$(printf '%s' "$_k" | tr '.' '/')" 2>/dev/null)
  elif have sysctl; then
    _v=$(rout2 3 sysctl -n "$_k" 2>/dev/null)
  else
    _v=""
  fi
  [ -n "$_v" ] && kv net.sysctl "${_k}|${_v}"
done

# BBR 是否可用（可用的内核不开，就是白丢性能）
if [ -r /proc/sys/net/ipv4/tcp_available_congestion_control ]; then
  kv net.cc_available "$(cat /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null)"
fi

# 队列规则：fq 是 BBR 的搭档，缺了 BBR 效果打折
if have tc; then
  rout2 5 tc qdisc show 2>/dev/null | head -20 | while IFS= read -r _q; do
    [ -n "$_q" ] && kv net.qdisc "$_q"
  done
elif [ -r /proc/sys/net/core/default_qdisc ]; then
  kv net.qdisc "default_qdisc=$(cat /proc/sys/net/core/default_qdisc 2>/dev/null)"
fi

# --------------------------------------------------------------------------
# 4. 连接与流量（找出谁在偷带宽）
# --------------------------------------------------------------------------
if have ss; then
  rout2 8 ss -tan 2>/dev/null | awk 'NR>1 {print $1}' | sort | uniq -c | sort -rn | head -10 \
    | while IFS= read -r _r; do
        _c=$(printf '%s' "$_r" | awk '{print $1}')
        _s=$(printf '%s' "$_r" | awk '{print $2}')
        [ -n "$_s" ] && kv net.tcp_state "${_s}|${_c}"
      done
  # 已建立连接的对端端口聚合 —— 能看出是谁在持续通信
  rout2 8 ss -tn state established 2>/dev/null | awk 'NR>1 {print $5}' \
    | sed 's/.*://' | sort | uniq -c | sort -rn | head -15 \
    | while IFS= read -r _r; do
        _c=$(printf '%s' "$_r" | awk '{print $1}')
        _p=$(printf '%s' "$_r" | awk '{print $2}')
        [ -n "$_p" ] && kv net.estab_port "${_p}|${_c}"
      done
elif have netstat; then
  rout2 8 netstat -tan 2>/dev/null | awk '$1=="tcp" {print $6}' | sort | uniq -c | sort -rn | head -10 \
    | while IFS= read -r _r; do
        _c=$(printf '%s' "$_r" | awk '{print $1}')
        _s=$(printf '%s' "$_r" | awk '{print $2}')
        [ -n "$_s" ] && kv net.tcp_state "${_s}|${_c}"
      done
fi

# TCP 重传与丢包计数（真正的"网慢"证据，而不是靠感觉）
if [ -r /proc/net/snmp ]; then
  _retrans=$(awk '
    /^Tcp:/ { n++ }
    n==2 && /^Tcp:/ {
      for (i=2;i<=NF;i++) h[i]=$i
      getline
      for (i=2;i<=NF;i++) v[i]=$i
      for (i in h) if (h[i]=="RetransSegs") print v[i]
    }' /proc/net/snmp 2>/dev/null | head -1)
  [ -n "$_retrans" ] && kv net.tcp_retrans "$_retrans"
fi
if [ -r /proc/net/netstat ]; then
  _rs=$(awk '/^TcpExt:/ {n++} n==2 && /^TcpExt:/ {for(i=1;i<=NF;i++) if(h[i]=="TCPTimeouts") print $i} n==1 && /^TcpExt:/ {for(i=1;i<=NF;i++) h[i]=$i}' /proc/net/netstat 2>/dev/null | head -1)
  [ -n "$_rs" ] && kv net.tcp_timeouts "$_rs"
fi

# conntrack 表满会导致新连接直接失败，表现就是"网时好时坏"
if [ -r /proc/sys/net/netfilter/nf_conntrack_count ]; then
  kv net.conntrack "$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null)/$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null)"
elif have conntrack; then
  kv net.conntrack "$(rout2 5 conntrack -C 2>/dev/null)"
fi

# 网卡错误与丢包（物理层问题的硬证据）
if have ip; then
  ip -s link show 2>/dev/null | awk '
    /^[0-9]+:/ { iface=$2; sub(/:$/,"",iface) }
    /RX:/ { getline; if (iface) printf "%s|rx_err=%s|rx_drop=%s\n", iface, $3, $4 }
    /TX:/ { getline; if (iface) printf "%s|tx_err=%s|tx_drop=%s\n", iface, $3, $4 }' 2>/dev/null \
  | while IFS= read -r _e; do kv net.if_err "$_e"; done
fi

# --------------------------------------------------------------------------
# 5. 无线质量（笔记本走 WiFi 时，这一项比任何调优都重要）
# --------------------------------------------------------------------------
if have iw; then
  for _w in $(ls /sys/class/net 2>/dev/null); do
    [ -d "/sys/class/net/$_w/wireless" ] || continue
    _info=$(rout2 5 iw dev "$_w" link 2>/dev/null)
    [ -z "$_info" ] && continue
    printf '%s\n' "$_info" | grep -E 'signal:|tx bitrate:|rx bitrate:|freq:|SSID:' \
      | while IFS= read -r _r; do kv net.wifi "${_w}|$(printf '%s' "$_r" | sed 's/^[[:space:]]*//')"; done
  done
elif have iwconfig; then
  iwconfig 2>/dev/null | grep -E 'ESSID|Quality|Signal level|Bit Rate' \
    | while IFS= read -r _r; do kv net.wifi "$(printf '%s' "$_r" | sed 's/^[[:space:]]*//')"; done
fi

# --------------------------------------------------------------------------
# 6. 实测：延迟 / 丢包 / DNS 解析耗时 / MTU
# --------------------------------------------------------------------------
_ping_one() {  # _ping_one LABEL HOST
  _out=$(rout2 12 ping -c 6 -W 2 "$2" 2>/dev/null)
  [ -z "$_out" ] && return
  _loss=$(printf '%s' "$_out" | sed -n 's/.*[ ,]\([0-9][0-9]*\)%[ ,].*/\1/p' | head -1)
  # 汇总行形如：rtt min/avg/max/mdev = 1.234/2.345/3.456/0.500 ms
  _sum=$(printf '%s' "$_out" | grep -E '=' | tail -1)
  _min=$(printf '%s' "$_sum" | sed -n 's/.*=\s*\([0-9.]*\)\/.*/\1/p')
  _avg=$(printf '%s' "$_sum" | sed -n 's/.*=\s*[0-9.]*\/\([0-9.]*\)\/.*/\1/p')
  _max=$(printf '%s' "$_sum" | sed -n 's/.*=\s*[0-9.]*\/[0-9.]*\/\([0-9.]*\).*/\1/p')
  # BusyBox ping 没有汇总行，只有逐条 time=xx ms —— 如实标注为 ?，不拿单次值冒充平均
  kv net.ping "$1|${2}|${_loss:-?}|${_min:-?}|${_avg:-?}|${_max:-?}"
}

if have ping; then
  _gw4=$(ip route show default 2>/dev/null | awk '{print $3}' | head -1)
  [ -n "$_gw4" ] && _ping_one "gateway" "$_gw4"
  _ping_one "ali-dns"   "223.5.5.5"
  _ping_one "tencent-dns" "119.29.29.29"
  _ping_one "baidu"     "www.baidu.com"
fi

# 逐个 DNS 服务器实测解析耗时 —— 换 DNS 是"上网变快"最直接的收益
_dns_time() {  # _dns_time SERVER
  _t0=$(date +%s 2>/dev/null)
  if have dig; then
    rout2 6 dig +time=2 +tries=1 "@$1" www.baidu.com A >/dev/null 2>&1 || { kv net.dns_time "$1|timeout"; return; }
  elif have nslookup; then
    rout2 6 nslookup www.baidu.com "$1" >/dev/null 2>&1 || { kv net.dns_time "$1|timeout"; return; }
  else
    return
  fi
  _t1=$(date +%s 2>/dev/null)
  # date +%s 只有秒精度，够判"能不能用"；配合上面的 timeout 判"可用/不可用"
  kv net.dns_time "$1|$(( _t1 - _t0 ))"
}

if have dig || have nslookup; then
  for _s in 223.5.5.5 119.29.29.29 114.114.114.114 180.76.76.76; do
    _dns_time "$_s"
  done
  # 当前配置的 DNS 也测一下 —— 它是"现在慢不慢"的基线
  if [ -r /etc/resolv.conf ]; then
    for _s in $(grep -E '^[[:space:]]*nameserver' /etc/resolv.conf 2>/dev/null | awk '{print $2}' | head -3); do
      case "$_s" in 223.5.5.5|119.29.29.29|114.114.114.114|180.76.76.76) ;; *) _dns_time "$_s" ;; esac
    done
  fi
fi

# MTU 探测：逐步减小 payload 直到不分片 —— 找出真实可用 MTU
if have ping; then
  _dst="223.5.5.5"
  _mtu_if=$(ip route show default 2>/dev/null | awk '{print $5}' | head -1)
  _mtu_cur=""
  [ -n "$_mtu_if" ] && [ -r "/sys/class/net/$_mtu_if/mtu" ] && _mtu_cur=$(cat "/sys/class/net/$_mtu_if/mtu" 2>/dev/null)
  _ok=0
  for _sz in 1472 1464 1452 1442 1414 1400 1372 1300 1200; do
    if rout2 6 ping -c 1 -W 1 -M do -s "$_sz" "$_dst" >/dev/null 2>&1; then
      _ok=$(( _sz + 28 )); break
    fi
  done
  # BusyBox / macOS 的 ping 没有 -M，退化用 -D 或无 DF 探测
  if [ "$_ok" = "0" ]; then
    for _sz in 1472 1464 1452 1400 1300 1200; do
      if rout2 6 ping -c 1 -W 1 -D -s "$_sz" "$_dst" >/dev/null 2>&1; then
        _ok=$(( _sz + 28 )); break
      fi
    done
  fi
  kv net.mtu "${_mtu_if:-?}|${_mtu_cur:-?}|${_ok}"
fi

# --------------------------------------------------------------------------
# 7. 带宽占用（本机能看到的吞吐证据）
# --------------------------------------------------------------------------
if [ -r /proc/net/dev ]; then
  awk 'NR>2 {
    gsub(":","",$1)
    if ($1=="lo") next
    printf "%s|rx=%s|tx=%s\n", $1, $2, $10
  }' /proc/net/dev 2>/dev/null | while IFS= read -r _n; do kv net.if_bytes "$_n"; done
fi

kv agent.done "1"
exit 0
