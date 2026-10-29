#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
linux_rules.py — Linux / UNIX 的规则引擎。

输入是 Facts（探针输出解析后的键值），输出是一组 CheckResult。
规则与采集彻底解耦，因此同一套判定同时适用于：
  * 本机 Linux
  * 远程 Linux / UNIX（SSH 管道执行探针）

三条原则：
  1. 缺失 ≠ 正常。探针没采到的项一律进「未覆盖清单」，绝不默认"没问题"。
  2. 每条结论必须带证据原文，让人能自己复核。
  3. 建议必须可执行（给命令），并标注验收判据（由 plan.py 补齐）。
"""

from __future__ import annotations

import re

from common import CheckResult, Finding, human_bytes, human_seconds, pct
from linux_facts import Facts, cpu_snapshot, disk_io_rates

# 期望探针至少应给出的键；缺了就要在未覆盖清单里点名
EXPECTED_KEYS = [
    "sys.kernel", "sys.hostname", "sys.cpu_count", "mem.total_kb",
    "fs.row", "listen.row", "sec.sshd", "sec.fw.tool", "malware.cron",
]

SUSPICIOUS_CMD = re.compile(
    r"(curl[^|]*\|\s*(ba)?sh|wget[^|]*\|\s*(ba)?sh|base64\s+-d|/dev/tcp/|nc\s+-e|"
    r"ncat\s+-e|bash\s+-i|python\s+-c|perl\s+-e|socat|mkfifo)",
    re.I,
)


# --------------------------------------------------------------------------
# 系统清单
# --------------------------------------------------------------------------

def rule_system(f: Facts) -> CheckResult:
    r = CheckResult(name="系统清单", category="config")
    r.fact("kernel", f.s("sys.kernel"))
    r.fact("hostname", f.s("sys.hostname"))
    r.fact("distro", f.s("sys.distro"))
    r.fact("arch", f.s("sys.arch"))
    r.fact("cpu_model", f.s("sys.cpu_model"))
    r.fact("cpu_count", f.i("sys.cpu_count"))
    r.fact("virt", f.s("sys.virt"))
    up = f.f("sys.uptime_s")
    r.fact("uptime_s", up)
    r.fact("uptime_human", human_seconds(up) if up else "未知")

    total = f.i("mem.total_kb") * 1024
    avail = f.i("mem.avail_kb") * 1024 or f.i("mem.free_kb") * 1024
    if total:
        r.fact("mem_total", total)
        r.fact("mem_avail", avail)
        r.fact("mem_used_pct", pct(total - avail, total))

    swaps = f.i("mem.swap_total_kb") * 1024
    r.fact("swap_total", swaps)
    if swaps == 0 and total:
        r.add(Finding(
            id="NO_SWAP",
            title="未配置交换分区",
            severity="LOW",
            category="perf",
            evidence=["SwapTotal = 0"],
            impact="内存突增时内核只能直接 OOM 杀进程，没有缓冲余地；"
            "但云主机上大量使用 swap 同样会拖慢性能，需权衡。",
            fix="若内存紧张建议加 2~4GB swap 作为缓冲："
            "fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile",
            ref="references/perf-tuning.md#内存",
        ))

    # 分区
    vols = []
    for row in f.lst("fs.row"):
        parts = row.split("|")
        if len(parts) < 6:
            continue
        try:
            size = int(parts[2]) * 1024
            used = int(parts[3]) * 1024
            avail_b = int(parts[4]) * 1024
            used_pct = float(parts[5].rstrip("%"))
        except ValueError:
            continue
        vols.append({"device": parts[0], "fs": parts[1], "size": size,
                     "used": used, "free": avail_b, "mount": parts[6] if len(parts) > 6 else "?",
                     "used_pct": used_pct})
    r.fact("filesystems", vols)
    for v in vols:
        if v["used_pct"] >= 90:
            r.add(Finding(
                id="DISK_FULL",
                title=f"{v['mount']} 使用率 {v['used_pct']:.0f}%",
                severity="HIGH" if v["used_pct"] >= 95 else "MEDIUM",
                category="perf",
                evidence=[f"{v['device']} → {v['mount']}，剩余 {human_bytes(v['free'])}"],
                impact="分区写满会导致服务无法写日志/写数据，MySQL 会直接拒绝写入，"
                "systemd-journald 也会丢日志。",
                fix="journalctl --vacuum-size=500M；清理旧日志/备份；"
                "日志目录迁到独立卷；必要时扩容云盘（注意扩容后还要 resize2fs/xfs_growfs）。",
                ref="references/perf-tuning.md#磁盘空间治理",
            ))
    for row in f.lst("perf.inode_high"):
        r.add(Finding(
            id="INODE_HIGH",
            title=f"inode 使用率偏高：{row}",
            severity="MEDIUM",
            category="perf",
            evidence=[row],
            impact="inode 耗尽时即使磁盘有空间也无法创建新文件，表现为"
            "'No space left on device' 但 df 显示还有容量。",
            fix="用 `find / -xdev -printf '%h\\n' | sort | uniq -c | sort -rn | head` 找"
            "小文件重灾区；清理 session/cache；必要时重建文件系统并调大 inode 密度。",
            ref="references/perf-tuning.md#磁盘空间治理",
        ))
    return r


# --------------------------------------------------------------------------
# 性能
# --------------------------------------------------------------------------

def rule_perf(f: Facts) -> CheckResult:
    r = CheckResult(name="性能体检", category="perf")

    load_raw = f.s("perf.loadavg")
    nums = re.findall(r"(\d+\.\d+)", load_raw)
    l1 = float(nums[0]) if nums else 0.0
    cpus = f.i("sys.cpu_count", 1) or 1
    r.fact("loadavg", load_raw)
    r.fact("load_1m", l1)
    r.fact("cpu_count", cpus)

    cpu = cpu_snapshot(f)
    r.fact("cpu", cpu)
    if cpu:
        r.fact("cpu_nonidle_pct", cpu.get("user_pct"))

    if l1 > cpus * 1.5:
        r.add(Finding(
            id="LOAD_HIGH",
            title=f"1 分钟负载 {l1:.2f}（CPU {cpus} 核）",
            severity="HIGH" if l1 > cpus * 3 else "MEDIUM",
            category="perf",
            evidence=[f"loadavg = {load_raw}"],
            impact="负载高于核数意味着请求在排队。需先区分是 CPU 饥饿还是 IO 等待"
            "（vmstat 的 wa 列 / iowait），否则调优会打偏。",
            fix="`top -o %CPU` + `pidstat 1 5` 定位热点；若是 IO 等待，转磁盘方向排查；"
            "确认是纯计算瓶颈再考虑升配 vCPU。",
            ref="references/perf-tuning.md#cpu",
        ))
    if cpu and cpu.get("user_pct", 0) >= 90:
        r.add(Finding(
            id="CPU_SATURATED",
            title=f"CPU 采样占用 {cpu['user_pct']}%",
            severity="HIGH",
            category="perf",
            evidence=[f"两次 /proc/stat 采样差值 => {cpu['user_pct']}%"],
            impact="CPU 满负荷会放大所有请求延迟；挖矿类恶意程序也常表现为 CPU 打满。",
            fix="定位 top 进程；排除异常进程；对正常业务做水平扩展或升配。",
            ref="references/perf-tuning.md#cpu",
        ))

    # 内存压力
    total = f.i("mem.total_kb") * 1024
    avail = f.i("mem.avail_kb") * 1024 or f.i("mem.free_kb") * 1024
    if total:
        used_pct = pct(total - avail, total)
        r.fact("mem_used_pct", used_pct)
        if used_pct >= 92:
            r.add(Finding(
                id="MEM_PRESSURE",
                title=f"物理内存吃紧：已用 {used_pct}%",
                severity="CRITICAL" if used_pct >= 96 else "HIGH",
                category="perf",
                evidence=[f"总 {human_bytes(total)}，可用 {human_bytes(avail)}"],
                impact="内存不足会触发换页与 OOM kill，服务可能被内核直接杀掉。",
                fix="按 RSS 排序找大户；给关键服务设 cgroup 内存上限；"
                "排查内存泄漏（观察 RSS 是否单调增长）；长期方案扩容。",
                ref="references/perf-tuning.md#内存",
            ))
    sw_t, sw_f = f.i("mem.swap_total_kb") * 1024, f.i("mem.swap_free_kb") * 1024
    if sw_t and pct(sw_t - sw_f, sw_t) > 50:
        r.add(Finding(
            id="SWAP_HEAVY",
            title=f"交换分区已用 {pct(sw_t - sw_f, sw_t)}%",
            severity="MEDIUM",
            category="perf",
            evidence=[f"Swap {human_bytes(sw_t - sw_f)}/{human_bytes(sw_t)}"],
            impact="大量换页说明物理内存不足，延迟会被放大数倍。",
            fix="压低常驻内存；核查 vm.swappiness；扩容内存优于加 swap。",
            ref="references/perf-tuning.md#内存",
        ))

    # 磁盘 IO
    io = disk_io_rates(f)
    r.fact("disk_io", io)
    for dev, v in sorted(io.items(), key=lambda kv: -kv[1]["busy_pct"])[:4]:
        if v["busy_pct"] >= 80:
            r.add(Finding(
                id="DISK_BUSY",
                title=f"磁盘 {dev} 繁忙度 {v['busy_pct']}%",
                severity="MEDIUM",
                category="perf",
                evidence=[f"读 {human_bytes(v['read_bps'])}/s，写 {human_bytes(v['write_bps'])}/s"],
                impact="磁盘饱和会让所有依赖落盘的服务一起变慢（数据库尤其敏感）。",
                fix="iotop -oPa 找写入大户；日志与数据分离到不同卷；"
                "云盘升配 IOPS 或换 SSD 型；检查是否在跑备份/压缩等批量任务。",
                ref="references/perf-tuning.md#磁盘",
            ))

    # Top 进程
    procs = []
    for row in f.lst("proc.row"):
        p = row.split("|")
        if len(p) < 3:
            continue
        try:
            procs.append({"pid": p[0], "rss": int(p[1]) * 1024, "cmd": p[2][:180]})
        except ValueError:
            continue
    r.fact("top_processes", procs[:15])

    # 内核参数
    tips = {
        "net.core.somaxconn": (1024, "lt", "MEDIUM", "高并发下未完成连接会被内核直接丢弃，"
                                             "表现为偶发连接失败"),
        "net.ipv4.tcp_max_syn_backlog": (1024, "lt", "LOW", "SYN 队列过短会加剧握手失败"),
        "vm.swappiness": (20, "gt", "LOW", "默认 60 会让内存充足时也过度换页，拖慢延迟敏感服务"),
        "fs.file-max": (100000, "lt", "LOW", "全局 fd 上限过低会导致高并发服务报 Too many open files"),
    }
    for key, (good, direction, sev, why) in tips.items():
        if not f.has(f"perf.sysctl.{key}"):
            continue
        val = f.i(f"perf.sysctl.{key}")
        r.fact(key, val)
        bad = val < good if direction == "lt" else val > good
        if bad:
            r.add(Finding(
                id="SYSCTL_" + key.replace(".", "_").upper(),
                title=f"{key} = {val}，建议 {'≥' if direction == 'lt' else '≤'} {good}",
                severity=sev,
                category="perf",
                evidence=[f"{key} = {val}"],
                impact=why,
                fix=f"写入 /etc/sysctl.d/99-ops-tuning.conf：`{key} = {good}`，"
                f"再执行 `sysctl --system`（在线生效，无需重启）",
                auto_fixable=True,
                ref="references/perf-tuning.md#内核参数",
            ))

    # ulimit
    n = f.i("perf.ulimit_n")
    if n and n < 4096:
        r.add(Finding(
            id="ULIMIT_LOW",
            title=f"文件描述符软上限偏低：{n}",
            severity="MEDIUM",
            category="perf",
            evidence=[f"ulimit -n = {n}"],
            impact="高并发服务会因 fd 耗尽拒绝连接，且报错信息不直观（Too many open files）。",
            fix="/etc/security/limits.conf 加 `* soft nofile 65535` / `* hard nofile 65535`；"
            "systemd 服务加 `LimitNOFILE=65535` 后 daemon-reload + restart；"
            "同时核对 fs.file-max。",
            auto_fixable=True,
            ref="references/perf-tuning.md#内核参数",
        ))

    # 连接数
    listen_n = len(f.lst("listen.row"))
    r.fact("listen_count", listen_n)
    return r


# --------------------------------------------------------------------------
# 安全基线
# --------------------------------------------------------------------------

def _sshd_map(f: Facts) -> dict:
    """sshd 配置：同名键后者优先（drop-in 目录会覆盖主配置）。"""
    cfg: dict[str, str] = {}
    for row in f.lst("sec.sshd"):
        if "|" not in row:
            continue
        k, _, v = row.partition("|")
        cfg[k.strip().lower()] = v.strip()
    # sshd -T 拿到的生效端口优先
    p = f.s("sec.sshd.port")
    if p:
        cfg["port"] = p
    return cfg


def rule_sec(f: Facts) -> CheckResult:
    r = CheckResult(name="安全基线", category="sec")

    cfg = _sshd_map(f)
    r.fact("sshd", cfg)
    if cfg:
        root = cfg.get("permitrootlogin", "yes").lower()
        if root in ("yes", "prohibit-password", "without-password"):
            r.add(Finding(
                id="SSH_ROOT_LOGIN",
                title=f"SSH 允许 root 登录（PermitRootLogin={root}）",
                severity="HIGH",
                category="sec",
                evidence=[f"PermitRootLogin {root}"],
                impact="22 端口每天都承受全网扫描与爆破，root 直连一旦命中即为完全沦陷。",
                fix="sshd_config 设 `PermitRootLogin no`，改用普通用户 + sudo；"
                "改完 `sshd -t && systemctl reload sshd` 先验证再重载（避免锁死自己）。",
                auto_fixable=True,
                ref="references/hardening-linux.md#ssh",
            ))
        if cfg.get("passwordauthentication", "yes").lower() == "yes":
            r.add(Finding(
                id="SSH_PASSWORD_AUTH",
                title="SSH 仍开启密码认证",
                severity="MEDIUM",
                category="sec",
                evidence=["PasswordAuthentication yes"],
                impact="密码认证是爆破的唯一入口；密钥认证可基本免疫自动化爆破。",
                fix="先确认自己的公钥可用（新开一个会话验证！），再设 "
                "`PasswordAuthentication no` + `PubkeyAuthentication yes`。",
                auto_fixable=True,
                ref="references/hardening-linux.md#ssh",
            ))
        if cfg.get("permitemptypasswords", "no").lower() == "yes":
            r.add(Finding(
                id="SSH_EMPTY_PW_ALLOWED",
                title="SSH 允许空密码登录（PermitEmptyPasswords yes）",
                severity="CRITICAL",
                category="sec",
                evidence=["PermitEmptyPasswords yes"],
                impact="允许空口令直接建立 SSH 会话，是最严重的配置失误之一。",
                fix="设 `PermitEmptyPasswords no` 并立即排查所有账户口令为空的情况。",
                auto_fixable=True,
                ref="references/hardening-linux.md#ssh",
            ))
        if cfg.get("x11forwarding", "no").lower() == "yes":
            r.add(Finding(
                id="SSH_X11",
                title="SSH 开启 X11Forwarding（服务器通常不需要）",
                severity="LOW",
                category="sec",
                evidence=["X11Forwarding yes"],
                impact="多余的攻击面，且占用资源。",
                fix="设 `X11Forwarding no`。",
                auto_fixable=True,
                ref="references/hardening-linux.md#ssh",
            ))

    # 爆破强度
    fails = max([int(x) for x in [f.s("sec.ssh_authfail"), f.s("sec.ssh_authfail_journal")]
                 if x.strip().isdigit()] or [0])
    r.fact("ssh_failed_auth", fails)
    if fails >= 50:
        r.add(Finding(
            id="SSH_BRUTEFORCE",
            title=f"SSH 认证失败 {fails} 次，正在被爆破",
            severity="HIGH" if fails >= 500 else "MEDIUM",
            category="sec",
            evidence=[f"Failed password 计数 = {fails}"],
            impact="高频爆破说明主机已进入扫描器的目标列表；一旦有弱口令账户即刻失守。",
            fix="部署 fail2ban（Debian/Ubuntu: apt install fail2ban；RHEL: yum install "
            "fail2ban 或 epel）；只允许密钥；配合来源 IP 白名单/堡垒机。",
            auto_fixable=True,
            ref="references/hardening-linux.md#ssh",
        ))

    # 账户
    uid0 = f.lst("sec.uid0")
    r.fact("uid0_users", uid0)
    if len(uid0) > 1:
        r.add(Finding(
            id="MULTI_UID0",
            title=f"存在多个 UID=0 账户：{', '.join(uid0)}",
            severity="HIGH",
            category="malware",
            evidence=[f"UID 0 = {uid0}"],
            impact="额外 UID=0 账户是后门账号的典型形态（攻击者加一个就永久有最高权限）。",
            fix="核对每个账户创建时间（`stat /etc/passwd`、`last`、auditd）；"
            "确认非业务后 `userdel -r`；同时排查是谁创建的。",
            ref="references/malware-response.md#后门账号",
        ))

    empty_pw = f.lst("sec.empty_pw")
    if empty_pw:
        r.add(Finding(
            id="EMPTY_PASSWORD_ACCOUNT",
            title=f"存在空口令账户：{', '.join(empty_pw)}",
            severity="CRITICAL",
            category="sec",
            evidence=[f"/etc/shadow 中 {u} 第二字段为空" for u in empty_pw],
            impact="空口令账户可直接登录（本地或经其他可信路径），等于系统无防护。",
            fix="立即 `passwd -l <user>` 锁定；确认无用后 `userdel -r`。",
            ref="references/hardening-linux.md#账户",
        ))

    sudo_np = f.lst("sec.sudo_nopasswd")
    r.fact("sudo_nopasswd", sudo_np)
    if sudo_np:
        r.add(Finding(
            id="SUDO_NOPASSWD",
            title=f"存在 {len(sudo_np)} 条 NOPASSWD 提权规则",
            severity="MEDIUM",
            category="sec",
            evidence=sudo_np[:5],
            impact="拿到该用户 shell 的攻击者可直接无阻提权到 root，无需口令。",
            fix="收敛到具体命令白名单，删除 `ALL=(ALL) NOPASSWD: ALL` 这类通配授权。",
            ref="references/hardening-linux.md#提权",
        ))

    keys = f.lst("sec.ssh_key")
    r.fact("ssh_authorized_keys", keys)
    if keys:
        r.add(Finding(
            id="SSH_AUTHORIZED_KEYS",
            title=f"存在 {len(keys)} 条 SSH 公钥授权",
            severity="MEDIUM",
            category="sec",
            evidence=keys[:6],
            impact="未核实的公钥是长期免密后门：攻击者加一把 key 就能永久回来，"
            "且不受口令轮换影响。",
            fix="逐条核对指纹（`ssh-keygen -lf ~/.ssh/authorized_keys`）与运维台账；"
            "源不明的立即删除；同时轮换相关口令并排查登入来源（`last`、auth log）。",
            ref="references/malware-response.md#后门账号",
        ))

    # 防火墙
    tool = f.s("sec.fw.tool")
    state = f.s("sec.fw.state")
    r.fact("firewall_tool", tool)
    r.fact("firewall_state", state)
    inactive = (
        tool in ("", "none")
        or "inactive" in state.lower()
        or "Status: inactive" in state
        or (tool == "iptables" and not f.has("sec.fw.rules"))
    )
    if inactive:
        r.add(Finding(
            id="FW_INACTIVE",
            title=f"主机防火墙未启用（工具：{tool or '未检测到'}）",
            severity="HIGH",
            category="sec",
            evidence=[f"firewall tool={tool}, state={state or '未知'}"],
            impact="所有监听端口直接对网络开放，无法做来源限制；"
            "对外网可达的主机等于把攻击面完全暴露。",
            fix="按发行版启用：Ubuntu/Debian `ufw default deny incoming && ufw allow 22/tcp && "
            "ufw enable`；RHEL/CentOS `systemctl enable --now firewalld`。"
            "云主机还应再叠加安全组做一层。",
            auto_fixable=True,
            ref="references/hardening-linux.md#防火墙",
        ))

    # MAC
    if f.has("sec.selinux") and f.s("sec.selinux") == "0":
        r.add(Finding(
            id="SELINUX_PERMISSIVE",
            title="SELinux 处于 Permissive 模式",
            severity="LOW",
            category="sec",
            evidence=["/sys/fs/selinux/enforce = 0"],
            impact="失去强制访问控制兜底：即使服务被拿下，SELinux 本可限制其越权访问。",
            fix="先 `setenforce 0` 状态下跑通业务，确认无 AVC 拒绝后 `setenforce 1`，"
            "并写入 /etc/selinux/config 固化。",
            ref="references/hardening-linux.md#MAC",
        ))
    if f.has("sec.apparmor") and f.s("sec.apparmor") == "disabled":
        r.note = "AppArmor 未启用"
        r.fact("apparmor", "disabled")

    # 敏感端口暴露
    for row in f.lst("listen.row"):
        parts = row.split("|")
        if len(parts) < 2:
            continue
        proto, local = parts[0], parts[1]
        addr, _, port = local.rpartition(":")
        addr = addr.strip("[]")
        try:
            p = int(port)
        except ValueError:
            continue
        bind_all = addr in ("0.0.0.0", "*", "::", "[::]", "")
        if bind_all and p in (23, 21, 1433, 3306, 5432, 6379, 27017, 9200, 11211, 5601, 8080, 9090):
            sev = "HIGH" if p in (6379, 27017, 11211, 9200, 5601) else "MEDIUM"
            extra = row.split("|", 2)[2] if len(parts) > 2 else ""
            r.add(Finding(
                id=f"PORT_EXPOSED_{p}",
                title=f"敏感端口 {p} 监听在所有网卡",
                severity=sev,
                category="sec",
                evidence=[f"{proto} {local}", extra[:200]],
                impact="Redis/MongoDB/ES/Memcached 若未鉴权且网络可达，可直接被写入或拖库；"
                "1433/3306 暴露则面临爆破与拖库。",
                fix=f"服务侧改 `bind 127.0.0.1`（或内网地址）+ 强口令；"
                f"防火墙侧 `ufw deny {p}` / 安全组只放行必要来源 IP。",
                ref="references/hardening-linux.md#数据库",
            ))

    # 补丁
    pend = f.i("sec.updates.pending")
    r.fact("updates_pending", pend)
    if pend > 0:
        r.add(Finding(
            id="PKG_UPDATES_PENDING",
            title=f"有 {pend} 个软件包待更新",
            severity="MEDIUM" if pend > 20 else "LOW",
            category="sec",
            evidence=[f"待更新包数 = {pend}"],
            impact="含内核、OpenSSL、glibc 等基础组件的安全更新，未打即留有公开可用的漏洞。",
            fix="先在测试环境验证依赖；生产上用 `yum update --security` 或 "
            "`apt-get install --only-upgrade` 分批更新；内核更新需安排重启窗口。",
            ref="references/hardening-linux.md#补丁",
        ))
    if f.has("sec.reboot_required"):
        r.add(Finding(
            id="REBOOT_REQUIRED",
            title="系统提示需要重启才能让更新完全生效",
            severity="LOW",
            category="sec",
            evidence=["/var/run/reboot-required 存在"],
            impact="内核与关键库的新版本要重启才装载，不重启等于补丁没打。",
            fix="安排维护窗口重启；重启前确认服务自启配置正确（systemctl is-enabled）。",
            ref="references/hardening-linux.md#补丁",
        ))
    return r


# --------------------------------------------------------------------------
# 恶意代码与持久化
# --------------------------------------------------------------------------

def rule_malware(f: Facts, deep: bool = False) -> CheckResult:
    r = CheckResult(name="恶意代码与持久化", category="malware")

    # cron
    cron = f.lst("malware.cron") + f.lst("malware.cron_script")
    ucr = f.s("malware.crontab_user")
    r.fact("cron_count", len(cron))
    r.fact("cron_entries", cron[:40])
    if ucr:
        r.fact("crontab_user", ucr)
        cron = cron + [f"user crontab: {l}" for l in ucr.splitlines() if l.strip()]
    for e in cron:
        if SUSPICIOUS_CMD.search(e):
            r.add(Finding(
                id="CRON_SUSPICIOUS",
                title="计划任务含下载/反弹/编码执行特征",
                severity="HIGH",
                category="malware",
                evidence=[e[:250]],
                impact="cron 是挖矿与后门最常用的驻留点；"
                "「下载后管道执行」几乎可直接判定为恶意。",
                fix="确认非业务后删除该行；同时排查其下载源与已建立的外联"
                "（`ss -tunp state established`）；检查是否有同源的其他持久化。",
                ref="references/malware-response.md#持久化位置",
            ))
            break

    # systemd
    units = f.lst("malware.systemd")
    r.fact("suspicious_systemd", units)
    for u in units:
        r.add(Finding(
            id="SYSTEMD_SUSPICIOUS",
            title="systemd 服务单元从临时目录启动或含下载特征",
            severity="HIGH",
            category="malware",
            evidence=[u[:250]],
            impact="/tmp、/var/tmp、/dev/shm 下的服务单元是持久化后门的典型形态。",
            fix="`systemctl disable --now <unit>` 停用；核对内容并留存证据后删除单元文件；"
            "`systemctl daemon-reload`。",
            ref="references/malware-response.md#持久化位置",
        ))

    # ld.so.preload
    pre = f.s("malware.ld_preload")
    if pre.strip():
        r.add(Finding(
            id="LD_PRELOAD_HIJACK",
            title="存在 /etc/ld.so.preload 劫持",
            severity="CRITICAL",
            category="malware",
            evidence=[pre[:250]],
            impact="ld.so.preload 会让恶意 so 被所有进程加载，是用户态 rootkit 的经典手法，"
            "常用于隐藏进程、文件、网络连接（你看到 ps/netstat 输出都可能是假的）。",
            fix="先取证：`cp /etc/ld.so.preload /root/evidence/` 并复制所列 so 文件；"
            "再用静态编译的 busybox 交叉验证 `ps`/`netstat` 输出；"
            "确认后清空 /etc/ld.so.preload。",
            ref="references/malware-response.md#rootkit",
        ))
    if f.s("malware.ld_env").strip():
        r.add(Finding(
            id="LD_PRELOAD_ENV",
            title=f"当前环境存在 LD_PRELOAD/LD_LIBRARY_PATH 注入：{f.s('malware.ld_env')}",
            severity="MEDIUM",
            category="malware",
            evidence=[f.s("malware.ld_env")[:200]],
            impact="环境变量注入会劫持子进程的动态库加载。",
            fix="核查其来源（/etc/profile、~/.bashrc、systemd Environment=）并清理。",
            ref="references/malware-response.md#rootkit",
        ))

    # 临时目录可执行
    tmpbins = f.lst("malware.tmp_exec")
    r.fact("tmp_executables", tmpbins)
    if tmpbins:
        r.add(Finding(
            id="TMP_EXECUTABLES",
            title=f"临时目录存在 {len(tmpbins)} 个可执行文件",
            severity="MEDIUM",
            category="malware",
            evidence=tmpbins[:8],
            impact="落地即执行是挖矿与后门的标准动作；正常服务极少在 /tmp 长期驻留二进制。",
            fix="`file` + `ls -la` + `stat` 核查创建时间；确认非业务后"
            "**先复制到取证目录再删除**（不要直接 rm，证据会丢）。"
            "更彻底的做法是给 /tmp 挂 noexec（需评估业务兼容性）。",
            ref="references/malware-response.md#落地样本",
        ))

    # SUID
    suids = f.lst("malware.suid")
    r.fact("suid_count", len(suids))
    sus_suid = [s for s in suids if re.search(r"^/(tmp|home|opt|var/tmp|dev/shm|srv|data)/", s)]
    if sus_suid:
        r.add(Finding(
            id="SUID_SUSPICIOUS",
            title=f"业务/临时目录下存在 {len(sus_suid)} 个 SUID 文件",
            severity="HIGH",
            category="malware",
            evidence=sus_suid[:8],
            impact="非系统目录的 SUID 位几乎是本地提权后门的标志。",
            fix="`chmod u-s <file>` 去掉 SUID；核查文件来源与哈希；"
            "用基线工具（如 `rpm -Va` / `debsums`）核对系统文件是否被篡改。",
            ref="references/malware-response.md#提权后门",
        ))

    # 进程数差异（隐藏进程初判）
    pc = f.s("malware.proc_count")
    if "/" in pc:
        a, _, b = pc.partition("/")
        try:
            if int(b) and int(a) > int(b) * 1.5 and int(a) - int(b) > 20:
                r.add(Finding(
                    id="HIDDEN_PROCESS_SUSPECT",
                    title=f"/proc 中进程数({a})远多于 ps 统计({b})，疑似隐藏进程",
                    severity="HIGH",
                    category="malware",
                    evidence=[f"/proc/[0-9]* = {a}，ps -e = {b}"],
                    impact="两者数量显著不一致，通常意味着 ps 被 rootkit 劫持或存在命名空间隔离"
                    "（容器场景属正常，需先排除）。",
                    fix="用静态 busybox 的 ps 交叉验证；`ls -l /proc/*/exe` 逐个核对；"
                    "检查是否有 ld.so.preload 劫持。",
                    ref="references/malware-response.md#rootkit",
                ))
        except ValueError:
            pass

    # 深扫项
    if deep:
        deleted = f.lst("malware.deleted_exe")
        r.fact("deleted_binaries", deleted)
        if deleted:
            r.add(Finding(
                id="PROC_DELETED_EXE",
                title=f"{len(deleted)} 个进程的可执行文件已删除但仍驻留内存",
                severity="CRITICAL",
                category="malware",
                evidence=deleted[:8],
                impact="「删除二进制但进程仍在跑」是无文件落地类后门/内存马的强特征，"
                "重启才会消失，但持久化机制会让它复活。",
                fix="先取证：`cp /proc/<pid>/exe /root/evidence/` 并"
                "`cat /proc/<pid>/maps`；再 kill；随后必须找出复活来源"
                "（cron / systemd / ld.so.preload / init 脚本）。",
                ref="references/malware-response.md#内存马",
            ))
        recent = f.lst("malware.recent_exec")
        r.fact("recent_executables", recent[:40])
        if len(recent) >= 15:
            r.add(Finding(
                id="RECENT_EXEC_DROP",
                title=f"近 7 天新增 {len(recent)} 个可执行文件（系统目录之外）",
                severity="MEDIUM",
                category="malware",
                evidence=recent[:10],
                impact="大批新落地二进制可能是入侵后的工具投递，也可能是正常发布，需人工区分。",
                fix="逐个核对来源与哈希（`sha256sum`）；与发布记录/配置管理基线比对。",
                ref="references/malware-response.md#落地样本",
            ))
        hidden = f.lst("malware.hidden")
        r.fact("hidden_files", hidden[:30])
        estab = f.lst("net.established")
        r.fact("established_connections", estab[:30])
    return r


# --------------------------------------------------------------------------
# 日志与崩溃
# --------------------------------------------------------------------------

def rule_log(f: Facts) -> CheckResult:
    r = CheckResult(name="日志分析", category="log")

    errs = f.s("log.journal_err") or f.s("log.syslog_err")
    n = f.i("log.journal_err_count") or (len(errs.splitlines()) if errs else 0)
    r.fact("error_lines", n)
    r.fact("error_sample", errs.splitlines()[-60:] if errs else [])

    oom = f.s("log.oom").strip()
    if oom:
        r.add(Finding(
            id="OOM_KILL",
            title=f"近期发生 OOM 杀进程（{len(oom.splitlines())} 条记录）",
            severity="HIGH",
            category="crash",
            evidence=oom.splitlines()[-5:],
            impact="内核 OOM killer 强杀进程会导致服务无预警重启、连接中断、"
            "未落盘数据丢失，且往往形成周期性故障。",
            fix="确认被杀进程（`dmesg | grep -i 'killed process'`）；给关键服务设内存上限"
            "（systemd MemoryMax / cgroup）；排查内存泄漏；必要时扩容或加 swap 缓冲。",
            ref="references/logs-and-crash.md#oom",
        ))

    segv = f.i("log.segv")
    r.fact("segfault_count", segv)
    if segv >= 3:
        r.add(Finding(
            id="SEGFAULT_REPEAT",
            title=f"近 7 天出现 {segv} 次段错误",
            severity="MEDIUM",
            category="crash",
            evidence=[f"segfault 计数 = {segv}"],
            impact="重复段错误指向特定二进制的内存 bug 或动态库版本冲突，会造成进程反复退出。",
            fix="`coredumpctl list` 定位崩溃程序；用 gdb 看调用栈；"
            "核对是否近期升级过 glibc/openssl 等基础库。",
            ref="references/logs-and-crash.md#段错误",
        ))

    ioerr = f.s("log.ioerr").strip()
    if ioerr:
        r.add(Finding(
            id="IO_ERRORS",
            title=f"内核日志出现 {len(ioerr.splitlines())} 条 I/O 错误",
            severity="CRITICAL",
            category="crash",
            evidence=ioerr.splitlines()[-5:],
            impact="块设备 I/O 错误意味着磁盘/云盘故障或存储链路异常，"
            "随时可能演变为文件系统损坏与数据丢失。",
            fix="立即备份关键数据（先备份再排查）；`dmesg -T` 看完整上下文；"
            "检查 SMART（`smartctl -a /dev/sdX`）；云主机提工单核查底层存储。",
            ref="references/logs-and-crash.md#硬件",
        ))
    return r


def rule_crash(f: Facts) -> CheckResult:
    r = CheckResult(name="崩溃与稳定性", category="crash")

    oops = f.s("crash.oops").strip()
    if oops:
        r.add(Finding(
            id="KERNEL_OOPS",
            title="内核日志存在 Oops / Panic 记录",
            severity="HIGH",
            category="crash",
            evidence=oops.splitlines()[-6:],
            impact="内核级崩溃会整机宕机；即使内核侥幸恢复，被污染的子系统也可能留下隐患。",
            fix="从调用栈确认触发模块（第三方驱动？文件系统？内存？）；"
            "跑 memtester 排查内存；核对是否有 out-of-tree 内核模块；"
            "云主机可考虑更换实例规格或内核版本。",
            ref="references/logs-and-crash.md#内核崩溃",
        ))

    cores = f.lst("crash.core")
    r.fact("core_files", cores[:20])
    if cores:
        r.add(Finding(
            id="CORE_DUMPS",
            title=f"存在 {len(cores)} 个 core dump 文件",
            severity="LOW",
            category="crash",
            evidence=cores[:6],
            impact="core 文件本身不是故障，但持续堆积会占满分区，且说明有进程在反复崩溃。",
            fix="`coredumpctl info <pid>` 或 gdb 分析崩溃点；定位并修复后再清理，"
            "并在 /etc/systemd/coredump.conf 设置 MaxUse 限制占用。",
            ref="references/logs-and-crash.md",
        ))

    hs = f.lst("crash.hs_err")
    r.fact("jvm_crash_logs", hs[:20])
    if hs:
        r.add(Finding(
            id="JVM_CRASH_FILES",
            title=f"发现 {len(hs)} 个 JVM 崩溃日志（hs_err_pid）",
            severity="MEDIUM",
            category="crash",
            evidence=hs[:6],
            impact="JVM 级崩溃通常不是普通堆 OOM，而是堆外内存耗尽（DirectByteBuffer/mmap）"
            "或 JNI 段错误 —— 这类问题不会产生 java.lang.OutOfMemoryError，"
            "只会让进程直接消失。",
            fix="看 hs_err 头部的 Problematic frame 与 memory map 摘要；"
            "检查 -XX:MaxDirectMemorySize 与 Netty/Lettuce 等堆外分配方"
            "（云环境常见 mmap 计数超限）；必要时加 -XX:+HeapDumpOnOutOfMemoryError。",
            ref="references/logs-and-crash.md#jvm崩溃",
        ))
    return r


# --------------------------------------------------------------------------
# 应用栈
# --------------------------------------------------------------------------

def rule_app(f: Facts) -> CheckResult:
    r = CheckResult(name="应用栈探测", category="config")
    r.fact("units", f.s("app.units").splitlines()[:40] if f.has("app.units") else [])
    for k in ("app.nginx", "app.httpd", "app.apachectl", "app.mysql",
              "app.redis", "app.postgres", "app.docker"):
        if f.has(k):
            r.fact(k, f.s(k))

    javas = f.lst("app.java")
    r.fact("java_processes", javas)
    for j in javas:
        if "-Xmx" not in j:
            r.add(Finding(
                id="TOMCAT_NO_XMX",
                title="Java 进程未显式设置 -Xmx",
                severity="MEDIUM",
                category="perf",
                evidence=[j[:220]],
                impact="堆上限取默认值（物理内存的 1/4），多实例同机时会互相争抢内存，"
                "容器里还可能按宿主内存计算而直接 OOM。",
                fix="显式设置 `-Xms`=`-Xmx`、`-XX:MaxDirectMemorySize`、"
                "`-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/data/dumps`"
                "（放在 setenv.sh 里，不要写死在 catalina 脚本中）。",
                ref="references/iis-tomcat.md#tomcat调优",
            ))
            break
    return r


# --------------------------------------------------------------------------
# 未覆盖清单
# --------------------------------------------------------------------------

def rule_coverage(f: Facts) -> CheckResult:
    r = CheckResult(name="采集覆盖度", category="config")
    missing = f.missing(EXPECTED_KEYS)
    r.fact("total_lines", f.total_lines)
    r.fact("bad_lines", f.bad_lines)
    r.fact("encodings", sorted(f.encodings))
    r.fact("missing_keys", missing)

    if f.total_lines == 0:
        r.add(Finding(
            id="AGENT_NO_OUTPUT",
            title="巡检探针没有返回任何数据",
            severity="CRITICAL",
            category="config",
            evidence=[f.error or "无输出"],
            impact="本次体检**没有拿到任何事实**，因此不能据此下任何'系统正常'的结论。",
            fix="确认目标主机可达、有 sh、且 ssh 认证可用；"
            "可先手工执行：`ssh user@host 'sh -s' < linux_agent.sh` 看报错。",
            ref="references/README-coverage.md",
        ))
        r.fail("探针无输出")
    elif missing:
        r.add(Finding(
            id="COVERAGE_GAP",
            title=f"有 {len(missing)} 项预期数据未采集到",
            severity="INFO",
            category="config",
            evidence=[", ".join(missing)],
            impact="缺失项对应的检查**无法判定**（不等于通过）。常见原因是权限不足"
            "（非 root 读不到 /etc/shadow、iptables 规则）、命令不存在、"
            "或该系统不是 Linux（无 /proc）。",
            fix="若需完整覆盖，请以 root 或具备 sudo 权限的账号重跑；"
            "非 Linux（AIX/Solaris/BSD）请按 references 中的手工核查清单执行。",
            ref="references/README-coverage.md",
        ))
    if f.bad_lines:
        r.add(Finding(
            id="PARSE_LOSS",
            title=f"有 {f.bad_lines}/{f.total_lines} 行输出无法解析",
            severity="INFO",
            category="config",
            evidence=[f"解析失败行数 {f.bad_lines}"],
            impact="说明传输链路存在干扰（编码、base64 缺失或输出被截断），"
            "可能造成个别事实缺失。",
            fix="检查是否使用了非 UTF-8 终端；确认目标机有 base64 或 openssl。",
            ref="references/README-coverage.md",
        ))
    return r


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def apply_all(f: Facts, deep: bool = False) -> list[CheckResult]:
    out = [
        rule_coverage(f),
        rule_system(f),
        rule_perf(f),
        rule_sec(f),
        rule_malware(f, deep),
        rule_app(f),
        rule_crash(f),
        rule_log(f),
    ]
    return out
