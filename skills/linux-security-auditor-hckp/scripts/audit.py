#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Linux 服务器安全加固基线审计器 (Server Security Baseline Auditor)
---------------------------------------------------------------
纯标准库 / 零依赖 / 离线可用。

输入：一个文本快照（可由 `收集脚本` 在目标服务器上一次性采集后粘贴），
      按 [段名] 分节，常见段：
        [sshd_config]  /  [firewall]  /  [sysctl]
        [users]        /  [suid]      /  [services]
        [listening]    /  [packages]  /  [cron]
      缺失的段会被标记为「未提供」，不阻断其余检查。

输出：分级风险（CRITICAL/HIGH/MEDIUM/LOW/PASS）+ 修复命令 + 安全评分(0-100)。

用法：
  python audit.py snapshot.txt                  # 文本报告
  python audit.py snapshot.txt --format json    # JSON（便于接入看板/CI）
  python audit.py snapshot.txt --output rep.json
  python audit.py -                             # 从标准输入读取
"""
import argparse
import json
import re
import sys
from collections import OrderedDict

SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "PASS", "INFO"]
SEV_WEIGHT = {"CRITICAL": 12, "HIGH": 7, "MEDIUM": 3, "LOW": 1, "PASS": 0, "INFO": 0}

# 标准 SUID 白名单（应出现在常见发行版上的可信 SUID 程序）
TRUSTED_SUID = {
    "/usr/bin/su", "/usr/bin/sudo", "/usr/bin/passwd", "/usr/bin/chfn",
    "/usr/bin/chsh", "/usr/bin/gpasswd", "/usr/bin/newgrp", "/usr/bin/mount",
    "/usr/bin/umount", "/usr/bin/pkexec", "/usr/bin/crontab", "/usr/bin/at",
    "/usr/bin/chage", "/usr/bin/expiry", "/usr/bin/fusermount",
    "/usr/lib/openssh/ssh-keysign", "/usr/lib/dbus-daemon-launch-helper",
    "/bin/ping", "/bin/ping6", "/usr/bin/ping", "/sbin/unix_chkpwd",
    "/usr/sbin/pppd", "/usr/libexec/openssh/ssh-keysign",
}

# 已知有风险的遗留/明文服务包
RISKY_PKGS = {
    "telnetd": "HIGH", "telnet": "LOW", "rsh-server": "CRITICAL",
    "rsh": "MEDIUM", "rlogin": "CRITICAL", "rsh-redone-server": "CRITICAL",
    "vsftpd": "MEDIUM", "tftpd": "HIGH", "tftp-hpa": "HIGH",
    "nfs-kernel-server": "MEDIUM", "rpcbind": "MEDIUM",
    "xinetd": "LOW", "openbsd-inetd": "LOW", "ypserv": "CRITICAL",
    "nis": "CRITICAL", "bind9": "LOW", "isc-dhcp-server": "LOW",
}

# 危险端口（非必要对外开放即高危）
RISKY_PORTS = {
    23: "telnet(明文传输)", 512: "rexec/rlogin", 513: "rlogin",
    514: "rsh/shell", 111: "rpcbind", 2049: "NFS", 139: "SMB/netbios",
    445: "SMB", 512: "rexec", 3306: "MySQL", 5432: "PostgreSQL",
    6379: "Redis", 27017: "MongoDB", 9200: "Elasticsearch",
}


# ----------------------------------------------------------------------------
# 解析层
# ----------------------------------------------------------------------------
def parse_sections(text):
    """按 [段名] 拆分文本为 {段名: 段内容}。"""
    sections = OrderedDict()
    cur = "__body__"
    buf = []
    for line in text.splitlines():
        m = re.match(r"^\s*\[([A-Za-z0-9_]+)\]\s*$", line)
        if m:
            sections[cur] = "\n".join(buf)
            cur = m.group(1).lower()
            buf = []
        else:
            buf.append(line)
    sections[cur] = "\n".join(buf)
    return sections


def parse_kv(block):
    """解析 key=value 风格（sshd_config / sysctl）。返回 {key.lower(): value}。"""
    out = {}
    if not block:
        return out
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 取首个 = 或空白分隔
        if "=" in line:
            k, v = line.split("=", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            k, v = parts
        out[k.strip().lower()] = v.strip()
    return out


def parse_passwd(block):
    """解析 /etc/passwd 行，返回 list of dict(uid, name, shell, pwfield)。"""
    users = []
    if not block:
        return users
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split(":")
        if len(f) < 7:
            continue
        users.append({"name": f[0], "pw": f[1], "uid": f[2],
                      "gid": f[3], "shell": f[6]})
    return users


def parse_iptables(block):
    """粗略判断 INPUT 默认策略与是否有规则。"""
    policy = None
    has_rules = False
    if not block:
        return policy, has_rules
    for line in block.splitlines():
        m = re.search(r"Chain INPUT \(policy (\w+)\)", line)
        if m:
            policy = m.group(1)
        if re.search(r"^target\s+prot", line, re.I):
            continue
        if re.match(r"^\w+\s+\w+\s+\w+", line) and "Chain" not in line:
            has_rules = True
    return policy, has_rules


def parse_ufw(block):
    """粗略判断 ufw 是否 active。"""
    if not block:
        return None
    for line in block.splitlines():
        m = re.search(r"Status:\s*(active|inactive)", line, re.I)
        if m:
            return m.group(1).lower()
    return None


def parse_firewall(block):
    """融合 ufw / firewalld / iptables 判断防火墙状态。"""
    if not block:
        return {"active": None, "policy": None, "raw": block}
    ufw = parse_ufw(block)
    if ufw == "active":
        return {"active": True, "policy": "ufw-active", "raw": block}
    if "firewalld" in block.lower() and ("running" in block.lower()):
        return {"active": True, "policy": "firewalld-running", "raw": block}
    policy, has_rules = parse_iptables(block)
    if policy is not None:
        return {"active": (policy.upper() == "DROP" or has_rules),
                "policy": policy, "raw": block}
    return {"active": None, "policy": None, "raw": block}


def parse_listening(block):
    """解析 ss/netstat 输出，返回 [(proto, local_addr, port, proc)]。"""
    out = []
    if not block:
        return out
    for line in block.splitlines():
        # 形式：LISTEN 0 128 0.0.0.0:22 或 0.0.0.0:22 users:(("sshd",pid=1))
        m = re.search(r"(\d+\.\d+\.\d+\.\d+|\[?[\da-fA-F:]+\]?):(\d+)", line)
        if not m:
            continue
        addr = m.group(1)
        port = int(m.group(2))
        proto = "tcp" if "tcp" in line.lower() or "LISTEN" in line.upper() else "udp"
        out.append({"addr": addr, "port": port, "proto": proto, "raw": line.strip()})
    return out


def parse_service_files(block):
    """解析 systemctl list-unit-files 的 enabled 服务。"""
    enabled = []
    if not block:
        return enabled
    for line in block.splitlines():
        m = re.search(r"^(\S+\.service)\s+enabled", line)
        if m:
            enabled.append(m.group(1))
    return enabled


def parse_packages(block):
    """解析 dpkg -l / rpm -qa 的包名。"""
    pkgs = set()
    if not block:
        return pkgs
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # dpkg: ii  pkgname  version
        m = re.match(r"^[a-z]{2}\s+([\w.+-]+)\s", line)
        if m:
            pkgs.add(m.group(1))
            continue
        # rpm: pkgname-version-release.arch
        m = re.match(r"^([\w.+-]+)-[\d]", line)
        if m:
            pkgs.add(m.group(1))
    return pkgs


def parse_cron(block):
    """解析 crontab 行。"""
    jobs = []
    if not block:
        return jobs
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        jobs.append(line)
    return jobs


# ----------------------------------------------------------------------------
# 检查层：每个检查返回 (severity, item, detail, remediation) 或 None(不适用)
# ----------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.rows = []

    def add(self, severity, item, detail, remediation=""):
        self.rows.append({
            "severity": severity, "item": item,
            "detail": detail, "remediation": remediation,
        })


def check_ssh(ssh, rep):
    if not ssh:
        rep.add("INFO", "SSH 配置", "未提供 [sshd_config]，跳过 SSH 基线检查。",
                "采集：sshd -T 2>/dev/null || cat /etc/ssh/sshd_config")
        return
    pr = ssh.get("permitrootlogin", "prohibit-password")
    if pr.lower() == "yes":
        rep.add("HIGH", "SSH 允许 root 直接登录",
                "PermitRootLogin=yes，暴力破解可直攻 root。",
                "PermitRootLogin no  # 改用普通用户+sudo")
    else:
        rep.add("PASS", "SSH root 登录", "PermitRootLogin 已限制。")

    pa = ssh.get("passwordauthentication", "yes")
    if pa.lower() == "yes":
        rep.add("HIGH", "SSH 允许密码登录",
                "PasswordAuthentication=yes，建议仅用密钥。",
                "PasswordAuthentication no  # 配合 PubkeyAuthentication yes")
    else:
        rep.add("PASS", "SSH 密码登录", "已禁用密码认证。")

    proto = ssh.get("protocol", "2")
    if proto.strip() != "2":
        rep.add("CRITICAL", "SSH 协议版本过低",
                f"Protocol={proto}，存在 SSHv1 明文风险。",
                "Protocol 2")
    else:
        rep.add("PASS", "SSH 协议版本", "已使用 Protocol 2。")

    try:
        mat = int(ssh.get("maxauthtries", "6"))
    except ValueError:
        mat = 6
    if mat > 4:
        rep.add("MEDIUM", "SSH 最大尝试次数过高",
                f"MaxAuthTries={mat}，易被在线爆破。",
                "MaxAuthTries 3")
    else:
        rep.add("PASS", "SSH 尝试次数", f"MaxAuthTries={mat} 合理。")

    pep = ssh.get("permitemptypasswords", "no")
    if pep.lower() == "yes":
        rep.add("CRITICAL", "SSH 允许空密码",
                "PermitEmptyPasswords=yes，账户可被无密码登录。",
                "PermitEmptyPasswords no")
    else:
        rep.add("PASS", "SSH 空密码", "已禁止空密码登录。")

    x11 = ssh.get("x11forwarding", "no")
    if x11.lower() == "yes":
        rep.add("LOW", "SSH X11 转发开启",
                "X11Forwarding=yes，扩大攻击面。",
                "X11Forwarding no（无图形需求时）")
    else:
        rep.add("PASS", "SSH X11 转发", "X11 转发已关闭。")


def check_firewall(fw, rep):
    if not fw or fw.get("active") is None:
        rep.add("INFO", "防火墙", "未提供 [firewall]，跳过防火墙检查。",
                "采集：ufw status / firewall-cmd --list-all / iptables -L -n")
        return
    if not fw["active"]:
        rep.add("CRITICAL", "防火墙未启用",
                "未检测到有效防火墙规则（INPUT 默认 ACCEPT 或无规则），全部端口裸露。",
                "ufw enable  或  nft add rule inet filter input drop")
        return
    pol = fw.get("policy")
    if pol and pol.upper() == "ACCEPT" and not fw.get("raw"):
        rep.add("HIGH", "INPUT 默认策略 ACCEPT",
                "iptables INPUT 默认 ACCEPT，依赖逐条放行易遗漏。",
                "设默认 DROP：iptables -P INPUT DROP")
    else:
        rep.add("PASS", "防火墙", "已检测到生效的防火墙规则。")


def check_sysctl(sctl, rep):
    if not sctl:
        rep.add("INFO", "内核参数", "未提供 [sysctl]，跳过内核加固检查。",
                "采集：sysctl -a")
        return
    rules = [
        ("net.ipv4.conf.all.rp_filter", "1", "HIGH",
         "开启反向路径过滤，防 IP 欺骗/ spoofing。",
         "net.ipv4.conf.all.rp_filter=1"),
        ("net.ipv4.conf.all.accept_redirects", "0", "MEDIUM",
         "禁用 ICMP 重定向，防中间人路由篡改。",
         "net.ipv4.conf.all.accept_redirects=0"),
        ("net.ipv4.conf.all.send_redirects", "0", "LOW",
         "禁用发送重定向（非路由器场景）。",
         "net.ipv4.conf.all.send_redirects=0"),
        ("net.ipv4.tcp_syncookies", "1", "HIGH",
         "开启 SYN Cookies 缓解 SYN flood。",
         "net.ipv4.tcp_syncookies=1"),
        ("net.ipv4.icmp_echo_ignore_broadcasts", "1", "MEDIUM",
         "忽略广播 ping，防 Smurf 放大攻击。",
         "net.ipv4.icmp_echo_ignore_broadcasts=1"),
        ("kernel.randomize_va_space", "2", "HIGH",
         "ASLR 地址空间随机化须为 2（全随机）。",
         "kernel.randomize_va_space=2"),
        ("net.ipv4.ip_forward", "0", "MEDIUM",
         "非路由器应关闭 IP 转发，防被用作流量跳板。",
         "net.ipv4.ip_forward=0"),
    ]
    for key, want, sev, detail, fix in rules:
        val = sctl.get(key.replace(".", "."), None)
        if val is None:
            rep.add("LOW", f"内核参数 {key}",
                    "未采集到该值，建议显式加固。", fix)
            continue
        if val != want:
            rep.add(sev, f"内核参数 {key}",
                    f"当前={val}，建议={want}。{detail}", fix)
        else:
            rep.add("PASS", f"内核参数 {key}", "已加固。")


def check_users(users, rep):
    if not users:
        rep.add("INFO", "账户", "未提供 [users]，跳过账户检查。",
                "采集：getent passwd")
        return
    root_uids = [u["name"] for u in users if u["uid"] == "0"]
    if len(root_uids) > 1:
        rep.add("CRITICAL", "存在多个 UID=0 账户",
                f"UID 0 账户：{', '.join(root_uids)}，非 root 的 UID0 账户即后门。",
                "删除/降权多余 UID0 账户，仅保留 root。")
    else:
        rep.add("PASS", "UID 0 账户", "仅 root 拥有 UID 0。")

    empty = [u["name"] for u in users if u["pw"] in ("", "!", "*") and u["shell"] != "/usr/sbin/nologin" and u["shell"] != "/bin/false"]
    # 仅 alert 真正可能为交互登录的空/锁密码
    weak = [u["name"] for u in users if u["pw"] == ""]
    if weak:
        rep.add("CRITICAL", "空密码账户",
                f"账户 {', '.join(weak)} 密码字段为空，可无密码登录。",
                "为该账户设置强密码或锁定：passwd -l <user>")
    else:
        rep.add("PASS", "空密码账户", "未发现空密码交互账户。")

    risky_shells = [u["name"] for u in users
                    if u["shell"] not in ("/usr/sbin/nologin", "/bin/false", "/sbin/nologin")
                    and u["name"] not in ("root",)]
    if not risky_shells:
        rep.add("PASS", "可登录 shell", "系统账户均无可交互 shell。")
    else:
        rep.add("LOW", "可交互登录账户",
                f"共 {len(risky_shells)} 个可登录 shell 的普通账户，属正常但建议最小化。",
                "定期审计：for u in $(grep -E '/bin/(ba)sh' /etc/passwd|cut -d: -f1); do ...; done")


def check_suid(suid_lines, rep):
    if not suid_lines:
        rep.add("INFO", "SUID/SGID", "未提供 [suid]，跳过 SUID 检查。",
                "采集：find / -xdev \\( -perm -4000 -o -perm -2000 \\) -type f -print 2>/dev/null")
        return
    # 典型后门/提权落点：不在白名单即高危
    HIGH_LOCS = ("/usr/local/", "/opt/", "/home/", "/tmp/", "/var/tmp/",
                 "/dev/shm/", "/root/")
    SYS_PREFIX = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/")
    suspicious = []
    for raw in suid_lines:
        # 取路径（常见形如 -rwsr-xr-x 1 root root 12345 /usr/local/bin/xxx）
        m = re.search(r"(\/\S+)$", raw.strip())
        path = m.group(1) if m else raw.strip()
        if path in TRUSTED_SUID:
            continue
        if any(path.startswith(p) for p in HIGH_LOCS):
            suspicious.append((path, "HIGH"))
        elif any(path.startswith(p) for p in SYS_PREFIX):
            # 系统目录、但不在白名单（新装工具可能合法，仍需复核）
            suspicious.append((path, "MEDIUM"))
        else:
            suspicious.append((path, "HIGH"))
    if suspicious:
        sev = "HIGH" if any(s == "HIGH" for _, s in suspicious) else "MEDIUM"
        lines = "\n  ".join("[%s] %s" % (s, p) for p, s in suspicious)
        rep.add(sev, "可疑 SUID/SGID 程序",
                "非常规 SUID/SGID 程序（可能是后门或提权点，建议逐一起源核查）：\n  " + lines,
                "核查来源：rpm -qf <path> / dpkg -S <path>；非必要则 chmod u-s <path>")
    else:
        rep.add("PASS", "SUID/SGID", "未检出非常规 SUID/SGID 程序。")


def check_services(svcs, rep):
    if not svcs:
        rep.add("INFO", "系统服务", "未提供 [services]，跳过服务检查。",
                "采集：systemctl list-unit-files --type=service | grep enabled")
        return
    found = []
    for s in svcs:
        base = s.replace(".service", "")
        for pkg in RISKY_PKGS:
            if pkg in base or pkg in s:
                found.append((s, RISKY_PKGS[pkg]))
                break
    if found:
        for s, sev in found:
            rep.add(sev, f"遗留危险服务启用 {s}",
                    "该服务暴露明文/遗留协议，高攻击面。",
                    f"systemctl disable --now {s}")
    else:
        rep.add("PASS", "系统服务", "未发现已知危险 enabled 服务。")


def check_listening(listen, rep):
    if not listen:
        rep.add("INFO", "监听端口", "未提供 [listening]，跳过端口检查。",
                "采集：ss -tulnp")
        return
    hits = []
    for e in listen:
        if e["port"] in RISKY_PORTS:
            hits.append(e)
    if hits:
        details = "; ".join(f"{h['addr']}:{h['port']}({RISKY_PORTS[h['port']]})" for h in hits)
        rep.add("HIGH" if any(p in (23, 512, 513, 514, 111, 2049, 139, 445) for p in [h["port"] for h in hits]) else "MEDIUM",
                "危险端口对外开放",
                f"检测到：{details}。这些明文/遗留端口不应对公网暴露。",
                "用防火墙仅放行 22/80/443 等必要端口，危险端口绑定 127.0.0.1 或关闭服务。")
    else:
        rep.add("PASS", "监听端口", "未检测到预定义危险端口对外开放。")

    db_ports = [h for h in listen if h["port"] in (3306, 5432, 6379, 27017, 9200) and h["addr"] in ("0.0.0.0", ":::")]
    if db_ports:
        rep.add("HIGH", "数据库端口监听公网",
                "数据库端口绑定 0.0.0.0，存在被拖库风险。",
                "改绑 127.0.0.1 或通过跳板/防火墙限制来源 IP。")


def check_packages(pkgs, rep):
    if not pkgs:
        rep.add("INFO", "软件包", "未提供 [packages]，跳过包检查。",
                "采集：dpkg -l | grep -E 'telnet|rsh|tftp|vsftpd' / rpm -qa")
        return
    hits = []
    for p in pkgs:
        for rp in RISKY_PKGS:
            if p == rp or p.startswith(rp):
                hits.append((p, RISKY_PKGS[rp]))
                break
    if hits:
        for p, sev in hits:
            rep.add(sev, f"遗留/明文软件包 {p}",
                    "该包提供明文或高风险协议，建议卸载或严格限制。",
                    f"apt remove {p}  /  yum remove {p}")
    else:
        rep.add("PASS", "软件包", "未发现已知危险软件包。")


def check_cron(jobs, rep):
    if not jobs:
        rep.add("INFO", "计划任务", "未提供 [cron]，跳过 cron 检查。",
                "采集：crontab -l / cat /etc/cron.*")
        return
    world_writable_hint = []
    for j in jobs:
        # 检测以 root 运行、指向 /tmp 或 /var/tmp 下脚本的 job（常见持久化后门特征）
        if re.search(r"\s(/tmp/|/var/tmp/)", j) and "root" in j:
            world_writable_hint.append(j)
    if world_writable_hint:
        rep.add("MEDIUM", "root cron 指向临时目录",
                "root 计划任务指向 /tmp 等可写目录，存在被篡改提权风险：\n  "
                + "\n  ".join(world_writable_hint[:10]),
                "将脚本移至 /usr/local/bin 等仅 root 可写目录，并设置 0755。")
    else:
        rep.add("PASS", "计划任务", "未发现指向临时目录的可疑 root cron。")


# ----------------------------------------------------------------------------
# 评分 + 输出
# ----------------------------------------------------------------------------
def compute_score(rows):
    deduction = 0
    for r in rows:
        deduction += SEV_WEIGHT.get(r["severity"], 0)
    score = max(0, 100 - deduction)
    # 归一化到 0-100（最多允许扣到 0）
    return score


def grade(score):
    if score >= 90:
        return "A（优秀）"
    if score >= 75:
        return "B（良好）"
    if score >= 55:
        return "C（及格）"
    if score >= 35:
        return "D（偏弱）"
    return "E（高危）"


def print_report(sections, rep):
    score = compute_score(rep.rows)
    counts = {s: 0 for s in SEV_ORDER}
    for r in rep.rows:
        counts[r["severity"]] += 1

    print("=" * 68)
    print("  Linux 服务器安全加固基线审计  ·  安全评分 %d/100  [%s]" % (score, grade(score)))
    print("=" * 68)
    print("  检查项: %d  通过: %d  低: %d  中: %d  高: %d  严重: %d"
          % (len(rep.rows), counts["PASS"], counts["LOW"], counts["MEDIUM"],
             counts["HIGH"], counts["CRITICAL"]))
    print("-" * 68)

    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "PASS", "INFO"]:
        for r in rep.rows:
            if r["severity"] != sev:
                continue
            print("[%-8s] %s" % (sev, r["item"]))
            print("           %s" % r["detail"])
            if r["remediation"]:
                print("           修复: %s" % r["remediation"])
    print("-" * 68)

    # 优先修复清单
    crit = [r for r in rep.rows if r["severity"] in ("CRITICAL", "HIGH")]
    crit.sort(key=lambda x: 0 if x["severity"] == "CRITICAL" else 1)
    if crit:
        print("  优先修复（CRITICAL/HIGH 共 %d 项）:" % len(crit))
        for i, r in enumerate(crit, 1):
            print("   %d. [%s] %s" % (i, r["severity"], r["item"]))
            print("      %s" % r["remediation"])
    else:
        print("   未发现 CRITICAL/HIGH 风险，维持现状并定期复查。")
    print("=" * 68)


def to_json(sections, rep):
    score = compute_score(rep.rows)
    counts = {s: 0 for s in SEV_ORDER}
    for r in rep.rows:
        counts[r["severity"]] += 1
    return json.dumps({
        "tool": "linux-security-auditor",
        "score": score,
        "grade": grade(score),
        "summary": counts,
        "sections_provided": [k for k in sections if k != "__body__" and sections[k].strip()],
        "findings": rep.rows,
    }, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Linux 服务器安全加固基线审计器")
    ap.add_argument("input", help="快照文件路径，或用 - 从标准输入读取")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--output", help="将结果写到该文件（不再打印到 stdout）")
    args = ap.parse_args()

    try:
        if args.input == "-":
            text = sys.stdin.read()
        else:
            with open(args.input, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
    except FileNotFoundError:
        sys.stderr.write("错误：找不到输入文件 %s\n" % args.input)
        return 2
    except OSError as e:
        sys.stderr.write("错误：读取文件失败：%s\n" % e)
        return 2

    if not text.strip():
        sys.stderr.write("错误：输入为空，请提供服务器快照文本。\n")
        return 2

    sections = parse_sections(text)
    ssh = parse_kv(sections.get("sshd_config", ""))
    fw = parse_firewall(sections.get("firewall", ""))
    sctl = parse_kv(sections.get("sysctl", ""))
    users = parse_passwd(sections.get("users", ""))
    suid_lines = [l for l in sections.get("suid", "").splitlines() if l.strip()]
    svcs = parse_service_files(sections.get("services", ""))
    listen = parse_listening(sections.get("listening", ""))
    pkgs = parse_packages(sections.get("packages", ""))
    cron = parse_cron(sections.get("cron", ""))

    rep = Report()
    check_ssh(ssh, rep)
    check_firewall(fw, rep)
    check_sysctl(sctl, rep)
    check_users(users, rep)
    check_suid(suid_lines, rep)
    check_services(svcs, rep)
    check_listening(listen, rep)
    check_packages(pkgs, rep)
    check_cron(cron, rep)

    if args.format == "json":
        out = to_json(sections, rep)
    else:
        out = _capture_text(sections, rep)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)
            sys.stderr.write("结果已写入 %s\n" % args.output)
        except OSError as e:
            sys.stderr.write("错误：写入失败：%s\n" % e)
            return 2
    else:
        sys.stdout.write(out)
        if args.format == "text":
            sys.stdout.write("\n")
    return 0


def _capture_text(sections, rep):
    import io
    old = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        print_report(sections, rep)
    finally:
        sys.stdout = old
    return buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
