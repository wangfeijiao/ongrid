#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan.py — 把散落的 Finding 收敛成「按优先级排好的可执行修复计划」。

一条好的运维结论必须回答四个问题：
  1. 先做什么（优先级 / 时间窗）
  2. 怎么做（具体命令或操作路径）
  3. 做完怎么验（验收判据）
  4. 出问题怎么退（回滚）

本模块只做规划，不执行任何变更。
"""

from __future__ import annotations

from common import Finding, Report, SEV_ORDER

# 优先级分档：把严重度 + 类别映射到处置时间窗
PHASES = [
    ("P0", "立即处置", "🔴", "入侵迹象或可直接被利用的严重缺陷；建议先隔离/止损再排查"),
    ("P1", "24 小时内", "🟠", "高危配置缺陷，攻击面明显"),
    ("P2", "本周内", "🟡", "中危问题与稳定性隐患"),
    ("P3", "择机优化", "🔵", "低危/提示类，属持续改进项"),
]

# 类别 → 该项是否需要停机/重启（影响面提示）
NEEDS_RESTART = {"sec", "config"}
_INTRUSION_IDS = {
    "AUDIT_LOG_CLEARED", "LD_PRELOAD_HIJACK", "PROC_DELETED_EXE",
    "WMI_SUBSCRIPTION", "MULTI_UID0", "DEFENDER_THREAT_HISTORY",
    "EMPTY_PASSWORD_ACCOUNT", "USER_NO_PASSWORD",
}


def _phase_of(f: Finding) -> str:
    if f.severity == "CRITICAL" or f.id in _INTRUSION_IDS:
        return "P0"
    if f.severity == "HIGH":
        return "P1"
    if f.severity == "MEDIUM":
        return "P2"
    return "P3"


def _verify_of(f: Finding) -> str:
    """给每类问题一个可复现的验收判据。"""
    table = {
        "DISK_FULL": "复查分区使用率 < 85%，且服务日志能正常写入",
        "MEM_PRESSURE": "可用内存回升到总量 20% 以上，Pages/sec 回落",
        "CPU_SATURATED": "采样 CPU 稳定 < 70%，请求 P95 延迟恢复基线",
        "DISK_BOTTLENECK": "磁盘队列 < 2，%Disk Time < 100%",
        "SWAP_THRASH": "Pages/sec < 200，换页活动基本消失",
        "POWER_BALANCED": "powercfg /getactivescheme 显示高性能方案",
        "FIREWALL_OFF": "Get-NetFirewallProfile 三个 profile 均为 Enabled=True",
        "FW_INACTIVE": "ufw status / firewall-cmd --state 显示 active",
        "SMB1_ENABLED": "Get-SmbServerConfiguration 的 EnableSMB1Protocol=False",
        "RDP_EXPOSED": "公网扫描 3389 不可达；VPN 内可正常登录",
        "RDP_NO_NLA": "UserAuthentication=1，未启用 NLA 的客户端无法连接",
        "WEAK_PASSWORD_POLICY": "net accounts 显示最小长度 ≥ 12 且锁定阈值非 Never",
        "NO_LOCKOUT": "连续输错 5 次后账户被临时锁定",
        "SSH_ROOT_LOGIN": "ssh root@host 被拒绝；普通用户 sudo 正常",
        "SSH_PASSWORD_AUTH": "ssh -o PreferredAuthentications=password 被拒绝",
        "SSH_BRUTEFORCE": "fail2ban 客户端列表有封禁记录，失败速率下降",
        "DB_BIND_ALL": "netstat/ss 中该端口只绑定 127.0.0.1 或内网地址",
        "DEFENDER_RTP_OFF": "Get-MpComputerStatus 的 RealTimeProtectionEnabled=True",
        "DEFENDER_BROAD_EXCLUSION": "Get-MpPreference 的排除列表不再含整个盘符",
        "AUTORUN_SUSPICIOUS": "该项从 Run 键消失，且重启后无相关进程",
        "TASK_SUSPICIOUS": "任务已禁用/删除，Get-ScheduledTask 不再出现",
        "CRON_SUSPICIOUS": "crontab -l 中该行消失，且无对应进程",
        "SYSTEMD_SUSPICIOUS": "systemctl is-enabled 显示 disabled/not-found",
        "LD_PRELOAD_HIJACK": "/etc/ld.so.preload 为空，ps/netstat 输出与 busybox 版本一致",
        "PROC_DELETED_EXE": "该 PID 已消失，且重启后不再出现",
        "WMI_SUBSCRIPTION": "root\\subscription 下三个类的实例均已清空",
        "HOSTS_MODIFIED": "hosts 仅剩注释与业务必要记录",
        "VSS": "vssadmin list shadows 能列出副本",
        "PATCH_STALE": "Get-HotFix 最近安装时间在 30 天内",
        "PKG_UPDATES_PENDING": "yum/apt 检查无安全更新待打",
        "TOMCAT_NO_XMX": "进程命令行含显式 -Xmx；压测下 FullGC 后堆回落",
        "TOMCAT_NO_ENCODING": "中文接口返回无乱码；-Dfile.encoding=UTF-8 生效",
        "DUMP_DISABLED": "CrashDumpEnabled ≠ 0，重启后 %SystemRoot%\\Minidump 有产出",
        "REPEATED_BLUESCREEN": "连续运行 7 天无新 dump 生成",
        "DISK_UNHEALTHY": "更换磁盘后 HealthStatus=Healthy",
        "LOAD_HIGH": "loadavg 稳定 < CPU 核数",
        "ULIMIT_LOW": "服务进程 cat /proc/<pid>/limits 显示 nofile=65535",
        "OOM_KILL": "连续运行 72h 无新 oom-kill 记录",
    }
    return table.get(f.id, "重跑 `cli.py audit --scope all`，该项不再出现在报告中")


def _restart_note(f: Finding) -> str:
    if f.id.startswith(("SVC_", "POWER_")):
        return "无需重启（即时生效）"
    if f.category in NEEDS_RESTART:
        return "部分变更需重启相关服务或主机才能完全生效"
    return "无需重启"


def build_plan(report: Report, max_per_phase: int | None = None) -> dict:
    """生成修复计划。返回 {'phases': [...], 'totals': {...}}"""
    buckets: dict[str, list[Finding]] = {p[0]: [] for p in PHASES}
    for f in report.findings:
        buckets[_phase_of(f)].append(f)

    phases_out = []
    for code, name, icon, desc in PHASES:
        items = sorted(buckets[code], key=lambda x: (SEV_ORDER[x.severity], x.category))
        if max_per_phase:
            items = items[:max_per_phase]
        if not items:
            continue
        phases_out.append({
            "code": code,
            "name": name,
            "icon": icon,
            "desc": desc,
            "count": len(buckets[code]),
            "items": [
                {
                    "id": f.id,
                    "title": f.title,
                    "severity": f.severity,
                    "category": f.category,
                    "action": f.fix,
                    "verify": _verify_of(f),
                    "rollback": _rollback_hint(f),
                    "restart": _restart_note(f),
                    "auto_fixable": f.auto_fixable,
                    "evidence": f.evidence[:3],
                    "ref": f.ref,
                }
                for f in items
            ],
        })

    return {
        "phases": phases_out,
        "totals": {
            "P0": len(buckets["P0"]),
            "P1": len(buckets["P1"]),
            "P2": len(buckets["P2"]),
            "P3": len(buckets["P3"]),
            "auto_fixable": sum(1 for f in report.findings if f.auto_fixable),
        },
    }


def _rollback_hint(f: Finding) -> str:
    if f.auto_fixable:
        return ("执行前用 `harden` 生成的 rollback 脚本即可一键还原；"
                "该类改动均为可逆的注册表/服务/策略项")
    if f.category == "malware":
        return "涉证据保留：先导出注册表键值 / 复制样本到隔离目录，再删除；不要直接清空回收站"
    if f.category == "perf":
        return "调优参数逐项改、逐项验；保留修改前的原值（harden 会写入 rollback 脚本）"
    return "变更前导出当前配置（reg export / cp -a 配置文件），确认可用后再清理备份"


def render_plan_text(plan: dict) -> str:
    lines = []
    t = plan["totals"]
    lines.append("修复计划 —— 自动可修 %d 项 / 共 %d 项"
                 % (t["auto_fixable"], sum(t[k] for k in ("P0", "P1", "P2", "P3"))))
    lines.append("")
    for ph in plan["phases"]:
        lines.append(f"{ph['icon']} {ph['code']} {ph['name']}（{ph['count']} 项）— {ph['desc']}")
        for it in ph["items"]:
            flag = " [可自动修复]" if it["auto_fixable"] else ""
            lines.append(f"   • {it['title']}{flag}")
            if it["action"]:
                lines.append(f"     动作: {it['action'].splitlines()[0][:160]}")
            lines.append(f"     验收: {it['verify']}")
            lines.append(f"     回滚: {it['rollback']}")
        lines.append("")
    return "\n".join(lines)
