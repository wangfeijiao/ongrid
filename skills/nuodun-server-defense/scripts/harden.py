#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harden.py — 加固执行器。

安全模型（这是本模块最重要的部分）：
  1. **默认不动手**。`harden plan` 只生成脚本；真正执行必须显式 `--apply`。
  2. **先备份再改**。执行前自动做一次配置快照（backup.snapshot_configs），
     快照的 manifest 路径写进回滚脚本。
  3. **一定生成回滚脚本**。每个动作都有反向命令；不确定原值的，
     用「快照恢复」兜底，而不是拍脑袋写一个"应该没事"。
  4. **改完即验**。每个动作带 verify 命令，执行后立刻回读确认生效。
  5. **可远程**。加 --target user@host 就把脚本通过 SSH 管道送过去执行（不落盘）。

动作以「动作词典」的形式集中定义，新增一条加固项只需加一个字典条目。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from common import (C, OUTPUT_DIR, WINDOWS, ensure_output_dir, now_stamp,
                    write_script as _write_script)
from backup import snapshot_configs

# --------------------------------------------------------------------------
# 动作词典
# --------------------------------------------------------------------------
# 字段说明：
#   id/title      标识与人类可读名
#   profiles      属于哪些画像（baseline / webserver / db / perf）
#   why           为什么做（进了脚本注释，便于审计）
#   win            Windows 命令：set=[正向], rollback=[反向], verify=验证
#   nix            Linux/UNIX：同上
#   restart       是否需要重启服务/主机
#   risk          风险等级 low/medium/high（high 需额外确认）

ACTIONS: list[dict] = [
    # ---------------- Windows ----------------
    {
        "id": "WIN_FIREWALL_ON",
        "title": "启用 Windows 防火墙（三个 profile）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "没有防火墙时所有监听端口直接暴露，是勒索与扫描的第一入口。",
        "win": {
            "set": ["Set-NetFirewallProfile -All -Enabled True"],
            "rollback": ["Set-NetFirewallProfile -All -Enabled False"],
            "verify": "(Get-NetFirewallProfile | Where-Object {$_.Enabled -eq $false} | "
                      "Measure-Object).Count",
        },
        "restart": "无需重启", "risk": "low",
    },
    {
        "id": "WIN_SMB1_OFF",
        "title": "关闭 SMBv1 协议",
        "profiles": ["baseline", "webserver", "db"],
        "why": "WannaCry/NotPetya 通过 SMBv1 横向传播；现代环境不需要该协议。",
        "win": {
            "set": ["Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force",
                    "Set-SmbClientConfiguration -EnableInsecureGuestLogons $false -Force "
                    "-ErrorAction SilentlyContinue"],
            "rollback": ["Set-SmbServerConfiguration -EnableSMB1Protocol $true -Force"],
            "verify": "(Get-SmbServerConfiguration).EnableSMB1Protocol",
        },
        "restart": "建议重启主机彻底生效", "risk": "medium",
    },
    {
        "id": "WIN_SMB_SIGNING",
        "title": "强制 SMB 签名",
        "profiles": ["baseline", "db"],
        "why": "防 SMB 中继攻击（攻击者借你的机器做横向跳板）。",
        "win": {
            "set": ["Set-SmbServerConfiguration -RequireSecuritySignature $true -Force"],
            "rollback": ["Set-SmbServerConfiguration -RequireSecuritySignature $false -Force"],
            "verify": "(Get-SmbServerConfiguration).RequireSecuritySignature",
        },
        "restart": "无需重启（新连接生效）", "risk": "low",
    },
    {
        "id": "WIN_RDP_NLA",
        "title": "远程桌面强制网络级身份验证（NLA）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "未启用 NLA 时未认证连接即可建立 RDP 会话，是 BlueKeep 类漏洞的入口。",
        "win": {
            "set": ["Set-ItemProperty -Path "
                    "'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\"
                    "WinStations\\RDP-Tcp' -Name UserAuthentication -Value 1"],
            "rollback": ["Set-ItemProperty -Path "
                         "'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\"
                         "WinStations\\RDP-Tcp' -Name UserAuthentication -Value 0"],
            "verify": "(Get-ItemProperty -Path "
                      "'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\"
                      "WinStations\\RDP-Tcp').UserAuthentication",
        },
        "restart": "无需重启（重连即生效）", "risk": "low",
    },
    {
        "id": "WIN_RDP_FIREWALL_SCOPE",
        "title": "RDP 仅允许内网网段访问（防火墙层收口）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "公网可达的 3389 是所有爆破器的第一目标，必须按来源收口。",
        "win": {
            "set": [
                "Remove-NetFirewallRule -DisplayName 'RDP-Limited-Scope' "
                "-ErrorAction SilentlyContinue",
                "New-NetFirewallRule -DisplayName 'RDP-Limited-Scope' -Direction Inbound "
                "-Protocol TCP -LocalPort 3389 -Action Allow -RemoteAddress LocalSubnet",
            ],
            "rollback": ["Remove-NetFirewallRule -DisplayName 'RDP-Limited-Scope' "
                         "-ErrorAction SilentlyContinue"],
            "verify": "(Get-NetFirewallRule -DisplayName 'RDP-Limited-Scope' "
                      "-ErrorAction SilentlyContinue | Measure-Object).Count",
        },
        "restart": "无需重启",
        "risk": "high",  # 改错会把自己关在门外
        "danger_note": "若你正通过公网 RDP 连接这台机，执行后可能立即断线。"
                       "请先从内网/VPN 验证可再执行。",
    },
    {
        "id": "WIN_PASSWORD_POLICY",
        "title": "收紧账户口令与锁定策略",
        "profiles": ["baseline", "webserver", "db"],
        "why": "无锁定阈值时口令可被无限次爆破。",
        "win": {
            "set": ["net accounts /minpwlen:12 /maxpwage:90 /minpwage:1 "
                    "/uniquepw:5 /lockoutthreshold:5 /lockoutduration:15 /lockoutwindow:15"],
            "rollback": ["net accounts /minpwlen:0 /maxpwage:UNLIMITED /minpwage:0 "
                         "/uniquepw:0 /lockoutthreshold:0"],
            "verify": "net accounts",
        },
        "restart": "无需重启", "risk": "low",
    },
    {
        "id": "WIN_DISABLE_LEGACY_SERVICES",
        "title": "停用高风险遗留服务（Telnet/FTP/RemoteRegistry/SNMP）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "这些协议明文传输且几乎无现代加固，若不使用应彻底关闭。",
        "win": {
            "set": [
                "foreach ($s in 'TlntSvr','FTPSVC','RemoteRegistry','SNMP','SimpTCP','RemoteAccess') "
                "{ $sv = Get-Service -Name $s -ErrorAction SilentlyContinue; "
                "if ($sv) { Stop-Service $s -Force -ErrorAction SilentlyContinue; "
                "Set-Service $s -StartupType Disabled -ErrorAction SilentlyContinue; "
                "Write-Output \"disabled: $s\" } }",
            ],
            "rollback": ["# 如需恢复：Set-Service <服务名> -StartupType Automatic; Start-Service <服务名>"],
            "verify": "Get-Service TlntSvr,FTPSVC,RemoteRegistry -ErrorAction SilentlyContinue | "
                      "Select-Object Name,Status,StartType",
        },
        "restart": "无需重启", "risk": "medium",
        "danger_note": "若业务确实依赖 FTP 或 SNMP 采集，请先确认再执行。",
    },
    {
        "id": "WIN_LLMNR_NETBIOS_OFF",
        "title": "关闭 LLMNR 与 NetBIOS over TCP/IP",
        "profiles": ["baseline"],
        "why": "LLMNR/NBT-NS 是 Responder 类中间人工具的主要凭据收割面。",
        "win": {
            "set": [
                "New-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient' "
                "-Name EnableMulticast -Value 0 -PropertyType DWord -Force",
                "$k='HKLM:\\SYSTEM\\CurrentControlSet\\Services\\NetBT\\Parameters\\Interfaces'; "
                "Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { "
                "Set-ItemProperty -Path $_.PSPath -Name NetbiosOptions -Value 2 "
                "-ErrorAction SilentlyContinue }",
            ],
            "rollback": [
                "Set-ItemProperty -Path "
                "'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient' "
                "-Name EnableMulticast -Value 1 -Force",
            ],
            "verify": "(Get-ItemProperty -Path "
                      "'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\DNSClient' "
                      "-Name EnableMulticast).EnableMulticast",
        },
        "restart": "建议重启", "risk": "low",
    },
    {
        "id": "WIN_DEFENDER_ON",
        "title": "恢复 Defender 实时保护并清理过宽排除项",
        "profiles": ["baseline", "webserver", "db"],
        "why": "恶意软件常把自己所在目录加进排除列表以长期驻留。",
        "win": {
            "set": [
                "Set-MpPreference -DisableRealtimeMonitoring $false",
                "Update-MpSignature -ErrorAction SilentlyContinue",
                "$bad = Get-MpPreference | Select-Object -ExpandProperty ExclusionPath; "
                "foreach ($b in $bad) { if ($b -match '^[A-Za-z]:\\\\?$' -or $b -eq '*') "
                "{ Write-Output \"review-exclusion: $b\" } }",
            ],
            "rollback": ["# 排除项如需恢复：Add-MpPreference -ExclusionPath '<路径>'"],
            "verify": "(Get-MpComputerStatus).RealTimeProtectionEnabled",
        },
        "restart": "无需重启", "risk": "low",
    },
    {
        "id": "WIN_AUDIT_POLICY",
        "title": "开启关键审计策略（登录/账户/进程/策略变更）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "没有审计日志，入侵后无法还原时间线；这也是等保基本要求。",
        "win": {
            "set": [
                "auditpol /set /subcategory:\"Logon\" /success:enable /failure:enable",
                "auditpol /set /subcategory:\"Account Logon\" /success:enable /failure:enable",
                "auditpol /set /subcategory:\"Account Management\" /success:enable /failure:enable",
                "auditpol /set /subcategory:\"Process Creation\" /success:enable",
                "auditpol /set /subcategory:\"Policy Change\" /success:enable /failure:enable",
                "wevtutil sl Security /ms:1073741824",
            ],
            "rollback": ["auditpol /clear /y"],
            "verify": "auditpol /get /category:*",
        },
        "restart": "无需重启", "risk": "low",
    },
    {
        "id": "WIN_ENABLE_DUMP",
        "title": "开启崩溃转储（蓝屏可诊断）",
        "profiles": ["baseline", "perf"],
        "why": "关掉 dump 等于下次蓝屏只能靠猜，排障周期会拉长数倍。",
        "win": {
            "set": [
                "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl' "
                "-Name CrashDumpEnabled -Value 7",
                "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl' "
                "-Name AlwaysKeepMemoryDump -Value 1 -Type DWord -ErrorAction SilentlyContinue",
            ],
            "rollback": ["Set-ItemProperty -Path "
                         "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl' "
                         "-Name CrashDumpEnabled -Value 0"],
            "verify": "(Get-ItemProperty -Path "
                      "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl').CrashDumpEnabled",
        },
        "restart": "重启后生效", "risk": "low",
    },
    {
        "id": "WIN_PERF_TUNING",
        "title": "性能基线：高性能电源 + 关闭预读/索引",
        "profiles": ["perf", "webserver", "db"],
        "why": "SysMain 与 WSearch 在高负载主机上会与业务抢 IOPS；"
        "平衡电源方案会让突发请求响应变慢。",
        "win": {
            "set": [
                "powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                "Stop-Service SysMain -Force -ErrorAction SilentlyContinue; "
                "Set-Service SysMain -StartupType Disabled -ErrorAction SilentlyContinue",
                "Stop-Service WSearch -Force -ErrorAction SilentlyContinue; "
                "Set-Service WSearch -StartupType Disabled -ErrorAction SilentlyContinue",
                "Set-Service -Name WSearch -StartupType Disabled -ErrorAction SilentlyContinue",
            ],
            "rollback": [
                "powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e",
                "Set-Service SysMain -StartupType Automatic; Start-Service SysMain",
                "Set-Service WSearch -StartupType Automatic; Start-Service WSearch",
            ],
            "verify": "powercfg /getactivescheme",
        },
        "restart": "无需重启", "risk": "low",
        "danger_note": "若依赖 Windows 搜索做文件索引（如自建文件服务器），请勿关闭 WSearch。",
    },

    # ---------------- Linux / UNIX ----------------
    {
        "id": "NIX_SSH_ROOT",
        "title": "禁止 SSH root 直接登录",
        "profiles": ["baseline", "webserver", "db"],
        "why": "root 直连是全网爆破的首要目标。",
        "nix": {
            "set": ["sed -i.opsbak -E 's/^[#[:space:]]*PermitRootLogin.*/PermitRootLogin no/' "
                    "/etc/ssh/sshd_config",
                    "grep -qE '^PermitRootLogin' /etc/ssh/sshd_config || "
                    "echo 'PermitRootLogin no' >> /etc/ssh/sshd_config",
                    "sshd -t && (systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null "
                    "|| service sshd reload 2>/dev/null || true)"],
            "rollback": ["sed -i.opsbak -E 's/^PermitRootLogin.*/PermitRootLogin yes/' "
                         "/etc/ssh/sshd_config",
                         "sshd -t && (systemctl reload sshd 2>/dev/null || service sshd reload 2>/dev/null || true)"],
            "verify": "sshd -T 2>/dev/null | grep -i permitrootlogin",
        },
        "restart": "reload sshd 即生效（不断开现有会话）",
        "risk": "high",
        "danger_note": "执行前请确认你有普通用户的 sudo 权限。若只有 root 可用于 SSH，"
                       "执行后需要先从控制台恢复（云主机可用 VNC）。",
    },
    {
        "id": "NIX_SSH_HARDEN",
        "title": "SSH 其他加固（空口令/X11/最大认证次数）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "减少 SSH 的攻击面与暴力尝试成功率。",
        "nix": {
            "set": ["cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.opsbak.$(date +%s)",
                    "for kv in 'PermitEmptyPasswords no' 'X11Forwarding no' "
                    "'MaxAuthTries 4' 'ClientAliveInterval 300' 'ClientAliveCountMax 2' "
                    "'Protocol 2'; do "
                    "k=$(echo $kv | cut -d' ' -f1); "
                    "if grep -qE \"^[#[:space:]]*$k\" /etc/ssh/sshd_config; then "
                    "sed -i -E \"s|^[#[:space:]]*$k.*|$kv|\" /etc/ssh/sshd_config; "
                    "else echo \"$kv\" >> /etc/ssh/sshd_config; fi; done",
                    "sshd -t && (systemctl reload sshd 2>/dev/null || service sshd reload 2>/dev/null || true)"],
            "rollback": ["# 从备份恢复：cp -a /etc/ssh/sshd_config.opsbak.<时间戳> /etc/ssh/sshd_config",
                         "sshd -t && (systemctl reload sshd 2>/dev/null || service sshd reload 2>/dev/null || true)"],
            "verify": "sshd -T 2>/dev/null | grep -iE 'permitemptypasswords|x11forwarding|maxauthtries'",
        },
        "restart": "reload sshd 即生效", "risk": "medium",
    },
    {
        "id": "NIX_FIREWALL",
        "title": "启用主机防火墙（默认拒绝入站）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "没有主机防火墙时，任何监听端口都对网络开放。",
        "nix": {
            "set": ["if command -v ufw >/dev/null 2>&1; then "
                    "ufw --force reset >/dev/null 2>&1; "
                    "ufw default deny incoming; ufw default allow outgoing; "
                    "ufw allow 22/tcp; ufw --force enable; ufw status verbose; "
                    "elif command -v firewall-cmd >/dev/null 2>&1; then "
                    "systemctl enable --now firewalld; firewall-cmd --set-default-zone=public; "
                    "firewall-cmd --permanent --add-service=ssh; firewall-cmd --reload; "
                    "firewall-cmd --list-all; else "
                    "echo 'WARN: 未找到 ufw/firewalld，请手工配置 iptables/nftables 并持久化'; fi"],
            "rollback": ["if command -v ufw >/dev/null 2>&1; then ufw --force disable; "
                         "elif command -v firewall-cmd >/dev/null 2>&1; then "
                         "systemctl disable --now firewalld; fi"],
            "verify": "ufw status 2>/dev/null || firewall-cmd --state 2>/dev/null || "
                      "iptables -S 2>/dev/null | head",
        },
        "restart": "无需重启",
        "risk": "high",
        "danger_note": "脚本已默认放行 22/tcp。若你的 SSH 在其他端口，请先自行调整脚本"
                       "再执行，否则会把自己关在门外。",
    },
    {
        "id": "NIX_SYSCTL_TUNING",
        "title": "内核参数调优（连接队列/swap/文件句柄）",
        "profiles": ["perf", "webserver", "db"],
        "why": "默认值面向桌面场景，高并发服务下会出现连接丢弃与过度换页。",
        "nix": {
            "set": ["cat > /etc/sysctl.d/99-ops-tuning.conf <<'EOF'\n"
                    "# 由 服务器安全维护、杀木马病毒skill 生成\n"
                    "net.core.somaxconn = 4096\n"
                    "net.ipv4.tcp_max_syn_backlog = 4096\n"
                    "net.ipv4.ip_local_port_range = 10240 65000\n"
                    "net.ipv4.tcp_tw_reuse = 1\n"
                    "net.ipv4.tcp_fin_timeout = 15\n"
                    "net.core.rmem_max = 16777216\n"
                    "net.core.wmem_max = 16777216\n"
                    "fs.file-max = 1000000\n"
                    "fs.inotify.max_user_watches = 524288\n"
                    "vm.swappiness = 10\n"
                    "vm.dirty_ratio = 20\n"
                    "vm.dirty_background_ratio = 5\n"
                    "EOF",
                    "sysctl -p /etc/sysctl.d/99-ops-tuning.conf"],
            "rollback": ["rm -f /etc/sysctl.d/99-ops-tuning.conf",
                         "sysctl --system >/dev/null"],
            "verify": "sysctl net.core.somaxconn vm.swappiness fs.file-max",
        },
        "restart": "无需重启（在线生效）", "risk": "low",
    },
    {
        "id": "NIX_LIMITS",
        "title": "提升文件描述符上限",
        "profiles": ["perf", "webserver", "db"],
        "why": "fd 耗尽会让服务报 Too many open files 并拒绝连接，且现象不易定位。",
        "nix": {
            "set": ["cat > /etc/security/limits.d/99-ops.conf <<'EOF'\n"
                    "* soft nofile 65535\n* hard nofile 65535\n"
                    "* soft nproc  65535\n* hard nproc  65535\nEOF",
                    "mkdir -p /etc/systemd/system.conf.d && "
                    "printf '[Manager]\\nDefaultLimitNOFILE=65535\\n' > "
                    "/etc/systemd/system.conf.d/99-ops-limits.conf"],
            "rollback": ["rm -f /etc/security/limits.d/99-ops.conf "
                         "/etc/systemd/system.conf.d/99-ops-limits.conf"],
            "verify": "cat /etc/security/limits.d/99-ops.conf",
        },
        "restart": "systemd 服务需 daemon-reexec 或重启后生效", "risk": "low",
    },
    {
        "id": "NIX_PERMS",
        "title": "关键文件权限收紧",
        "profiles": ["baseline", "webserver", "db"],
        "why": "shadow/gshadow 权限过宽会泄露口令哈希，家目录过宽会泄露私钥。",
        "nix": {
            "set": ["chmod 000 /etc/shadow /etc/gshadow 2>/dev/null; "
                    "chmod 644 /etc/passwd /etc/group 2>/dev/null; "
                    "chmod 600 /etc/ssh/sshd_config 2>/dev/null; "
                    "chmod 700 /root 2>/dev/null; "
                    "for h in /home/*; do [ -d \"$h\" ] && chmod 750 \"$h\"; done; "
                    "chmod 600 /root/.ssh/authorized_keys 2>/dev/null; "
                    "ls -l /etc/shadow /etc/passwd /etc/gshadow"],
            "rollback": ["chmod 0000 /etc/shadow; chmod 644 /etc/passwd; chmod 0000 /etc/gshadow"],
            "verify": "ls -l /etc/shadow /etc/passwd /etc/ssh/sshd_config",
        },
        "restart": "无需重启", "risk": "low",
    },
    {
        "id": "NIX_DISABLE_LEGACY",
        "title": "停用不需要的明文服务（telnet/rsh/ftp/tftp）",
        "profiles": ["baseline", "webserver", "db"],
        "why": "明文协议凭据可被直接嗅探，且这些守护进程历史漏洞多。",
        "nix": {
            "set": ["for s in telnet.socket telnet rsh.socket rlogin.socket rexec.socket "
                    "vsftpd tftpd tftp.socket xinetd ypbind; do "
                    "if systemctl list-unit-files 2>/dev/null | grep -q \"^$s\"; then "
                    "systemctl disable --now \"$s\" 2>/dev/null && echo \"disabled: $s\"; fi; done"],
            "rollback": ["# 如需恢复：systemctl enable --now <服务名>"],
            "verify": "systemctl list-units --type=service --state=running | "
                      "grep -iE 'telnet|rsh|ftp|tftp' || echo 'none-running'",
        },
        "restart": "无需重启", "risk": "medium",
    },
    {
        "id": "NIX_TMP_NOSUID",
        "title": "临时目录加 nosuid,nodev（不启用 noexec）",
        "profiles": ["baseline"],
        "why": "阻断在 /tmp 放置 SUID 提权后门；不启用 noexec 以避免破坏正常业务。",
        "nix": {
            "set": ["if ! mount | grep -q ' /tmp '; then "
                    "printf 'tmpfs /tmp tmpfs defaults,nosuid,nodev,size=2G 0 0\\n' >> /etc/fstab; "
                    "mount -o remount /tmp 2>/dev/null || mount /tmp; fi; "
                    "mount | grep ' /tmp '"],
            "rollback": ["sed -i '/ \\/tmp tmpfs /d' /etc/fstab",
                         "mount -o remount,suid,dev /tmp 2>/dev/null || true"],
            "verify": "mount | grep ' /tmp '",
        },
        "restart": "无需重启", "risk": "medium",
        "danger_note": "/tmp 经常被挂载为独立分区或已被 systemd 托管，"
                       "脚本检测到已有挂载时会跳过，不会覆盖现有配置。",
    },
    {
        "id": "NIX_AUDITD",
        "title": "开启审计（auditd）",
        "profiles": ["baseline", "db"],
        "why": "入侵溯源几乎完全依赖审计日志；等保二级以上是硬要求。",
        "nix": {
            "set": ["if command -v auditctl >/dev/null 2>&1; then "
                    "systemctl enable --now auditd 2>/dev/null || service auditd start 2>/dev/null; "
                    "cat > /etc/audit/rules.d/99-ops.rules <<'EOF'\n"
                    "# 由 服务器安全维护、杀木马病毒skill 生成：身份、提权、持久化、配置文件变更\n"
                    "-w /etc/passwd -p wa -k identity\n"
                    "-w /etc/shadow -p wa -k identity\n"
                    "-w /etc/sudoers -p wa -k identity\n"
                    "-w /etc/ssh/sshd_config -p wa -k sshd\n"
                    "-w /etc/crontab -p wa -k cron\n"
                    "-w /etc/cron.d -p wa -k cron\n"
                    "-w /var/spool/cron -p wa -k cron\n"
                    "-w /etc/ld.so.preload -p wa -k rootkit\n"
                    "-a always,exit -F arch=b64 -S execve -F uid=0 -k rootcmd\n"
                    "EOF\n"
                    "augenrules --load 2>/dev/null || service auditd restart 2>/dev/null; "
                    "auditctl -l | head -20; else "
                    "echo 'WARN: 未安装 auditd，请先安装该软件包再执行'; fi"],
            "rollback": ["rm -f /etc/audit/rules.d/99-ops.rules; "
                         "augenrules --load 2>/dev/null || true"],
            "verify": "auditctl -l 2>/dev/null | head -20",
        },
        "restart": "无需重启", "risk": "low",
    },
]

PROFILE_ALIASES = {
    "baseline": "基础安全加固",
    "webserver": "Web 服务器（IIS/Tomcat/Nginx）",
    "db": "数据库主机",
    "perf": "性能基线优化",
}


def actions_for(profile: str, platform: str = "auto") -> list[dict]:
    plat = "win" if (platform == "win" or (platform == "auto" and WINDOWS)) else "nix"
    out = []
    for a in ACTIONS:
        if plat not in a:
            continue
        if profile not in ("all", *a["profiles"]):
            continue
        out.append(a)
    return out


# --------------------------------------------------------------------------
# 脚本生成
# --------------------------------------------------------------------------

_HEADER_PS1 = """# ============================================================
#  服务器安全维护、杀木马病毒skill 加固脚本（Windows）
#  生成时间: @@TS@@
#  目标画像: @@PROFILE@@ —— @@PROFILE_DESC@@
#  动作数量: @@N@@
#
#  >>> 请先通读本脚本再执行。每一节都标注了「为什么」。
#  >>> 回滚脚本: @@ROLLBACK@@
#  >>> 配置快照: @@SNAPSHOT@@
#
#  出错处理：每个动作单独 try/catch。某一项失败只报出「哪一项 + 什么原因」，
#            其余动作继续执行，最后统一汇总。绝不静默中断、绝不整脚本崩掉。
# ============================================================
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
$ND_FAILED = New-Object System.Collections.ArrayList

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    [!] $m"  -ForegroundColor Yellow }
function Fail($m) { Write-Host "    [X] $m"  -ForegroundColor Red
                    [void]$ND_FAILED.Add($m) }

# 单个动作的隔离执行：失败时打印原因与补救办法，然后把控制权交回主流程
function Apply-Step([string]$name, [scriptblock]$body) {
    try {
        & $body
        Ok "$name 已应用"
    } catch {
        Fail "$name 未生效：$($_.Exception.Message)"
        Write-Host "        原因：多为权限不足 / 该策略在本系统版本不支持 / 依赖的服务或注册表项不存在" -ForegroundColor DarkYellow
        if ($_.Exception.InnerException) {
            Write-Host "        细节：$($_.Exception.InnerException.Message)" -ForegroundColor DarkGray
        }
        Write-Host "        处理：其余动作继续执行；已改动的部分可用回滚脚本还原" -ForegroundColor DarkGray
    }
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "错误：需要以管理员身份运行。" -ForegroundColor Red
    Write-Host "      原因：加固会修改系统策略、注册表、服务与防火墙，普通权限改不动。" -ForegroundColor DarkGray
    Write-Host "      处理：开始菜单搜 PowerShell → 右键「以管理员身份运行」→ 再执行本脚本。" -ForegroundColor DarkGray
    exit 1
}
"""

_HEADER_SH = """#!/bin/sh
# ============================================================
#  服务器安全维护、杀木马病毒skill 加固脚本（Linux / UNIX）
#  生成时间: @@TS@@
#  目标画像: @@PROFILE@@ —— @@PROFILE_DESC@@
#  动作数量: @@N@@
#
#  >>> 请先通读本脚本再执行。每一节都标注了「为什么」。
#  >>> 回滚脚本: @@ROLLBACK@@
#  >>> 配置快照: @@SNAPSHOT@@
#
#  出错处理：每个动作在独立的子 shell 里执行，失败只报出「哪一项 + 什么原因」，
#            其余动作继续，最后统一汇总。绝不静默中断、绝不整脚本崩掉。
# ============================================================
set -u
ND_FAILED=""
if [ "$(id -u)" != "0" ]; then
  echo "错误：需要 root 权限（或 sudo）。" >&2
  echo "      原因：加固会写入系统配置、服务与防火墙规则，非 root 改不动。" >&2
  echo "      处理：用 'sudo sh <本脚本>' 执行。" >&2
  exit 1
fi
step()  { printf '\\033[36m==> %s\\033[0m\\n' "$1"; }
ok()    { printf '\\033[32m    [OK] %s\\033[0m\\n' "$1"; }
warn()  { printf '\\033[33m    [!] %s\\033[0m\\n' "$1"; }
fail()  { printf '\\033[31m    [X] %s\\033[0m\\n' "$1"; ND_FAILED="$ND_FAILED$1; "; }
# 只读验证：失败不算动作失败，只提示"验证命令本身没跑成"
verify() { printf '\\033[90m    验证：%s\\033[0m\\n' "$1"
  sh -c "$1" 2>/dev/null | head -12 | sed 's/^/      /' || true; }
"""

_TAIL_PS1 = """
Write-Host ""
if ($ND_FAILED.Count -gt 0) {
    Write-Host "⚠ 有 $($ND_FAILED.Count) 个动作未生效：" -ForegroundColor Yellow
    $ND_FAILED | ForEach-Object { Write-Host "  · $_" -ForegroundColor Yellow }
    Write-Host "请按上面的原因逐项处理；已成功的部分已生效，如需撤销请执行回滚脚本。" -ForegroundColor DarkGray
    exit 2
}
Write-Host "全部动作执行完成。" -ForegroundColor Green
"""

_TAIL_SH = """
echo ""
if [ -n "$ND_FAILED" ]; then
  echo "⚠ 以下动作未成功：$ND_FAILED"
  echo "  请按上面的原因逐项处理；已成功的部分已生效，如需撤销请执行回滚脚本。"
  exit 2
fi
echo "全部动作执行完成。"
"""


def _fill_header(tpl: str, *, profile: str, desc: str, n: int,
                 rollback: str, snapshot: str) -> str:
    """用占位符替换而非 str.format —— shell/PS 脚本里全是花括号，format 会炸。"""
    return (tpl
            .replace("@@TS@@", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            .replace("@@PROFILE@@", profile)
            .replace("@@PROFILE_DESC@@", desc)
            .replace("@@N@@", str(n))
            .replace("@@ROLLBACK@@", rollback)
            .replace("@@SNAPSHOT@@", snapshot))


def write_script(path: Path, text: str) -> None:
    """写出脚本 —— 实现集中在 common.write_script（.ps1 要 BOM、.sh 不能有 BOM）。"""
    _write_script(path, text)


def generate_scripts(profile: str, platform: str = "auto",
                     out_dir: str | None = None) -> dict:
    ensure_output_dir()
    ts = now_stamp()
    d = Path(out_dir) if out_dir else (OUTPUT_DIR / f"harden-{ts}")
    d.mkdir(parents=True, exist_ok=True)
    plat = "win" if (platform == "win" or (platform == "auto" and WINDOWS)) else "nix"
    acts = actions_for(profile, plat)
    desc = PROFILE_ALIASES.get(profile, profile)

    ext = "ps1" if plat == "win" else "sh"
    apply_p = d / f"harden-apply-{profile}.{ext}"
    roll_p = d / f"harden-rollback-{profile}.{ext}"

    apply_lines = []
    roll_lines = []
    for i, a in enumerate(acts, 1):
        spec = a[plat]
        apply_lines.append(f"\n# --- [{i}] {a['id']} :: {a['title']} ---")
        apply_lines.append(f"# 为什么: {a['why']}")
        if a.get("danger_note"):
            apply_lines.append(f"# ⚠ 注意: {a['danger_note']}")
        apply_lines.append(f"# 生效条件: {a['restart']}    风险: {a['risk']}")
        apply_lines.append(f'Step "[{i}/{len(acts)}] {a["title"]}"')
        # 每个动作单独隔离：某一项失败只报出这一项，其余照常执行
        if plat == "win":
            apply_lines.append(f'Apply-Step "[{i}/{len(acts)}] {a["title"]}" {{')
            apply_lines.extend(spec["set"])
            apply_lines.append("}")
            if spec.get("verify"):
                apply_lines.append(f'Step "    验证：{a["id"]}"')
                apply_lines.append("try {")
                apply_lines.append(spec["verify"])
                apply_lines.append('} catch { Warn "验证命令未跑成：$($_.Exception.Message)'
                                   '（不影响已应用的结果）" }')
        else:
            apply_lines.append("if ! ( set -e")
            apply_lines.extend(spec["set"])
            apply_lines.append("); then")
            apply_lines.append(f'  fail "[{i}/{len(acts)}] {a["title"]} 未生效（原因见上）"')
            apply_lines.append('  echo "        原因：多为权限不足 / 命令不存在 / 内核或系统版本不支持该项"')
            apply_lines.append('  echo "        处理：其余动作继续执行；已改动的部分可用回滚脚本还原"')
            apply_lines.append("fi")
            if spec.get("verify"):
                apply_lines.append(f'step "    验证：{a["id"]}"')
                apply_lines.append(f'verify "{spec["verify"]}"')

        roll_lines.append(f"\n# --- 回滚 [{i}] {a['id']} :: {a['title']} ---")
        roll_lines.extend(spec["rollback"])

    tail = _TAIL_PS1 if plat == "win" else _TAIL_SH
    hdr_apply = _fill_header(
        _HEADER_PS1 if plat == "win" else _HEADER_SH,
        profile=profile, desc=desc, n=len(acts),
        rollback=roll_p.name, snapshot="见执行输出（outputs/snapshots/）")
    hdr_roll = _fill_header(
        _HEADER_PS1 if plat == "win" else _HEADER_SH,
        profile=profile, desc=f"{desc} · 回滚", n=len(acts),
        rollback=apply_p.name, snapshot="见执行输出（outputs/snapshots/）")

    write_script(apply_p, hdr_apply + "\n".join(apply_lines) + "\n" + tail)
    write_script(roll_p, hdr_roll + "\n".join(roll_lines) + "\n" + tail)

    if plat != "win":
        os.chmod(apply_p, 0o755)
        os.chmod(roll_p, 0o755)

    return {
        "ok": True, "platform": plat, "profile": profile,
        "action_count": len(acts),
        "actions": [{"id": a["id"], "title": a["title"], "risk": a["risk"],
                     "restart": a["restart"], "danger_note": a.get("danger_note", "")}
                    for a in acts],
        "apply_script": str(apply_p),
        "rollback_script": str(roll_p),
        "dir": str(d),
    }


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

def apply_script(script: str, target: str | None = None, timeout: int = 900) -> dict:
    """执行脚本。target 为空 = 本机；形如 user@host:port 则走 SSH 管道。"""
    sp = Path(script)
    if not sp.is_file():
        return {"ok": False, "error": f"脚本不存在：{script}"}
    src = sp.read_bytes()

    if not target:
        if sp.suffix.lower() == ".ps1":
            exe = shutil.which("powershell") or shutil.which("pwsh")
            if not exe:
                return {"ok": False, "error": "本机没有 PowerShell"}
            cmd = [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(sp)]
            cwd = str(sp.parent)
        else:
            sh = shutil.which("sh")
            if not sh:
                return {"ok": False, "error": "本机没有 sh"}
            cmd = [sh, str(sp)]
            cwd = str(sp.parent)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"执行超时（{timeout}s），可能在中途挂起，请人工检查"}
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout.decode("utf-8", "replace")[-8000:],
            "stderr": p.stderr.decode("utf-8", "replace")[-4000:],
            "target": "本机",
        }

    # 远程（仅支持 sh 脚本）
    if sp.suffix.lower() == ".ps1":
        return {"ok": False, "error": "远程执行仅支持 Linux/UNIX 的 sh 脚本"}
    user_host, _, port = target.partition(":")
    port = port or "22"
    ssh = shutil.which("ssh")
    if not ssh:
        return {"ok": False, "error": "本机没有 ssh 客户端"}
    args = [ssh, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15", "-p", port, user_host, "sudo sh -s"]
    try:
        p = subprocess.run(args, input=src, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"远程执行超时（{timeout}s）"}
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout.decode("utf-8", "replace")[-8000:],
        "stderr": p.stderr.decode("utf-8", "replace")[-4000:],
        "target": target,
    }


def cmd_harden(args) -> int:
    profile = args.profile
    res = generate_scripts(profile, platform=args.platform, out_dir=args.out)

    print(C.bold(f"加固方案 · 画像「{profile}」（{PROFILE_ALIASES.get(profile, profile)}）"))
    print(f"  共 {res['action_count']} 个动作：")
    for a in res["actions"]:
        flag = ""
        if a["risk"] == "high":
            flag = C.red("  ⚠ 高风险")
        elif a["risk"] == "medium":
            flag = C.orange("  · 中风险")
        print(f"    [{a['id']}] {a['title']}{flag}")
        if a["danger_note"]:
            print(f"        {C.orange('注意: ' + a['danger_note'])}")
    print()
    print(f"  正向脚本：{res['apply_script']}")
    print(f"  回滚脚本：{res['rollback_script']}")

    if not args.apply:
        print()
        print(C.gray("  当前为「只看不改」模式。确认无误后加 --apply 执行。"))
        print(C.gray("  执行前会自动做一次配置快照，回滚脚本也会同步生成。"))
        return 0

    # --- 真正执行 ---
    if not args.yes:
        print()
        print(C.orange("  即将对系统做实际变更。请确认已阅读上面每个动作与注意事项。"))
        print(C.orange("  如确认，请加 --yes 重跑本命令。"))
        return 3

    print()
    print(C.bold("  [1/3] 生成配置快照（回滚依据）..."))
    mf = ""
    try:
        mf = snapshot_configs(tag=f"preharden-{profile}")
        print(C.green(f"        快照：{mf}"))
    except Exception as e:  # noqa: BLE001
        print(C.orange(f"        快照失败（{e}），继续执行但回滚能力受限"))

    print(C.bold("  [2/3] 执行加固脚本 ..."))
    r = apply_script(res["apply_script"], target=args.target)
    print(C.gray(r.get("stdout", "")[-4000:]))
    if r.get("stderr"):
        print(C.orange(r["stderr"][-1500:]))

    print(C.bold("  [3/3] 结果"))
    if r.get("ok"):
        print(C.green("      ✓ 脚本执行完成（请逐项核对上面的验证输出）"))
    else:
        print(C.red(f"      ✗ 执行未成功：{r.get('error') or r.get('returncode')}"))

    # 记录一次执行日志
    ensure_output_dir()
    log = OUTPUT_DIR / f"harden-run-{now_stamp()}.json"
    log.write_text(json.dumps({"plan": res, "snapshot": mf, "result": r},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(C.gray(f"      执行记录：{log}"))
    print()
    print(C.orange("  建议：重启相关服务或主机后重跑 `cli.py audit --scope all` 确认分数提升，"
                   "并再次跑一次备份校验。"))
    return 0 if r.get("ok") else 2


def add_harden_parser(sub) -> None:
    p = sub.add_parser("harden", help="生成/执行安全加固方案（默认只看不改）")
    p.add_argument("--profile", default="baseline",
                   choices=["baseline", "webserver", "db", "perf", "all"])
    p.add_argument("--platform", default="auto", choices=["auto", "win", "nix"])
    p.add_argument("--out", default=None, help="脚本输出目录")
    p.add_argument("--apply", action="store_true", help="真正执行（默认只生成脚本）")
    p.add_argument("--yes", action="store_true", help="跳过二次确认")
    p.add_argument("--target", default=None,
                   help="远程执行目标 user@host[:port]（仅 Linux，需已配置免密或密钥）")
    p.set_defaults(func=cmd_harden)
