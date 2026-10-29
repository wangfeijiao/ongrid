#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_win.py — Windows 采集器。

原则：
  * 只用「读」操作，绝不修改系统。
  * 优先用 Python 原生（winreg / ctypes / socket），其次是 CIM(PowerShell)，
    最后才是命令行工具。刻意不用 wmic（在新系统上已被弃用且常被安全软件拦截）。
  * 任何一项采集失败 → 记入 CheckResult.errors 并降级，不中断整体体检。

对外只有一个入口：collect_all(report, scope)
"""

from __future__ import annotations

import ctypes
import glob as _glob
import os
import re
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path

from common import (
    C,
    CheckResult,
    Finding,
    Report,
    human_bytes,
    pct,
    ps,
    ps_json,
    run,
)

# --------------------------------------------------------------------------
# 基础信息
# --------------------------------------------------------------------------

_WIN_VER_NAME = {
    "10.0": "Windows 10 / Server 2016+",
    "6.3": "Windows 8.1 / Server 2012 R2",
    "6.2": "Windows 8 / Server 2012",
    "6.1": "Windows 7 / Server 2008 R2",
}


def _memory_status() -> dict:
    """用 GlobalMemoryStatusEx 直接读内存，比 CIM 快且不受编码影响。"""

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    except Exception:  # noqa: BLE001
        return {}
    return {
        "total_phys": st.ullTotalPhys,
        "avail_phys": st.ullAvailPhys,
        "load_pct": st.dwMemoryLoad,
        "total_pagefile": st.ullTotalPageFile,
        "avail_pagefile": st.ullAvailPageFile,
    }


def _uptime() -> float:
    try:
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:  # noqa: BLE001
        return 0.0


def _cpu_sample(interval: float = 1.0) -> float:
    """两次取 GetSystemTimes 差值算总体 CPU 占用率（0-100）。"""
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    def snap():
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)
        )

        def v(f):
            return (f.dwHighDateTime << 32) | f.dwLowDateTime

        return v(idle), v(kern) + v(user)

    try:
        i1, t1 = snap()
        time.sleep(interval)
        i2, t2 = snap()
        dt = t2 - t1
        if dt <= 0:
            return 0.0
        return round((1.0 - (i2 - i1) / dt) * 100.0, 1)
    except Exception:  # noqa: BLE001
        return -1.0


# --------------------------------------------------------------------------
# 1. 系统清单
# --------------------------------------------------------------------------

def collect_system() -> CheckResult:
    r = CheckResult(name="系统清单", category="config")

    os_info = ps_json(
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object Caption,Version,BuildNumber,OSArchitecture,"
        "InstallDate,LastBootUpTime,TotalVisibleMemorySize,FreePhysicalMemory,"
        "SystemDrive,WindowsDirectory,CSName",
        timeout=40,
    ) or []
    if os_info:
        o = os_info[0]
        r.fact("os", o.get("Caption"))
        r.fact("version", o.get("Version"))
        r.fact("build", o.get("BuildNumber"))
        r.fact("arch", o.get("OSArchitecture"))
        r.fact("windows_dir", o.get("WindowsDirectory"))
        r.fact("system_drive", o.get("SystemDrive"))
    else:
        r.fail("无法读取 Win32_OperatingSystem")

    cpus = ps_json(
        "Get-CimInstance Win32_Processor | "
        "Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,LoadPercentage",
        timeout=40,
    ) or []
    if cpus:
        c0 = cpus[0]
        r.fact("cpu", c0.get("Name"))
        r.fact("cores", c0.get("NumberOfCores"))
        r.fact("logical_cpus", c0.get("NumberOfLogicalProcessors"))
        r.fact("cpu_mhz", c0.get("MaxClockSpeed"))
        r.fact("cpu_count", len(cpus))

    mem = _memory_status()
    if mem:
        r.fact("mem_total", mem["total_phys"])
        r.fact("mem_avail", mem["avail_phys"])
        r.fact("mem_load_pct", mem["load_pct"])
        r.fact("pf_total", mem["total_pagefile"])
        r.fact("pf_avail", mem["avail_pagefile"])

    r.fact("uptime_s", round(_uptime(), 0))

    # 物理盘与分区
    disks = ps_json(
        "Get-CimInstance Win32_DiskDrive | "
        "Select-Object Model,MediaType,Size,InterfaceType,SerialNumber",
        timeout=40,
    ) or []
    r.fact("disks", disks)

    vols = ps_json(
        "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
        "Select-Object DeviceID,VolumeName,FileSystem,Size,FreeSpace",
        timeout=40,
    ) or []
    vol_list = []
    for v in vols:
        size = int(v.get("Size") or 0)
        free = int(v.get("FreeSpace") or 0)
        vol_list.append(
            {
                "drive": v.get("DeviceID"),
                "label": v.get("VolumeName"),
                "fs": v.get("FileSystem"),
                "size": size,
                "free": free,
                "used_pct": pct(size - free, size),
            }
        )
    r.fact("volumes", vol_list)

    # 页面文件
    pf = ps_json(
        "Get-CimInstance Win32_PageFileUsage | "
        "Select-Object Name,AllocatedBaseSize,CurrentUsage,PeakUsage",
        timeout=30,
    ) or []
    r.fact("pagefile", pf)

    # 启动模式：判断是不是「自动管理」
    auto_pf = ps(
        "try { (Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile } "
        "catch { 'unknown' }",
        timeout=30,
    )[1]
    r.fact("pagefile_auto_managed", auto_pf.strip())

    for v in vol_list:
        if v["size"] and v["used_pct"] >= 90:
            r.add(
                Finding(
                    id="DISK_FULL",
                    title=f"系统分区 {v['drive']} 使用率 {v['used_pct']}%，空间告急",
                    severity="HIGH" if v["used_pct"] >= 95 else "MEDIUM",
                    category="perf",
                    evidence=[
                        f"{v['drive']} 总 {human_bytes(v['size'])}，剩余 {human_bytes(v['free'])}"
                    ],
                    impact="分区写满会导致数据库无法写入、IIS/Tomcat 日志写失败、系统更新失败，"
                    "严重时整个服务不可用。",
                    fix="清理临时文件与旧日志（%TEMP%、C:\\Windows\\Temp、IIS logs、Tomcat logs）；"
                    "把日志/备份目录迁到独立数据盘；必要时扩容。",
                    ref="references/perf-tuning.md#磁盘空间治理",
                )
            )
    return r


# --------------------------------------------------------------------------
# 2. 性能
# --------------------------------------------------------------------------

def collect_perf(quick: bool = False) -> CheckResult:
    r = CheckResult(name="性能体检", category="perf")

    cpu = _cpu_sample(0.6 if quick else 1.2)
    r.fact("cpu_load_pct", cpu)
    mem = _memory_status()
    if mem:
        r.fact("mem_load_pct", mem["load_pct"])

    if mem and mem["load_pct"] >= 92:
        r.add(
            Finding(
                id="MEM_PRESSURE",
                title=f"物理内存吃紧：已用 {mem['load_pct']}%",
                severity="CRITICAL" if mem["load_pct"] >= 96 else "HIGH",
                category="perf",
                evidence=[
                    f"总 {human_bytes(mem['total_phys'])}，可用 {human_bytes(mem['avail_phys'])}"
                ],
                impact="内存不足会触发频繁换页，磁盘队列飙升，表现为整机卡顿、服务超时。",
                fix="定位内存大户（进程/连接池/缓存），收敛 JVM 堆与连接池上限；"
                "关闭无用服务；长期方案是扩容内存。",
                ref="references/perf-tuning.md#内存",
            )
        )
    if cpu >= 90:
        r.add(
            Finding(
                id="CPU_SATURATED",
                title=f"CPU 持续高位：{cpu}%",
                severity="HIGH",
                category="perf",
                evidence=[f"调度器采样占用 {cpu}%"],
                impact="CPU 打满会放大所有请求延迟，并可能触发监控告警。",
                fix="用 top 进程定位热点；检查是否有死循环/正则回溯/加密挖矿进程；"
                "必要时做亲和性与优先级调整、扩容 vCPU。",
                ref="references/perf-tuning.md#cpu",
            )
        )

    # 磁盘队列（性能核心指标）
    counters = ps_json(
        "Get-Counter -Counter "
        "'\\PhysicalDisk(_Total)\\Current Disk Queue Length',"
        "'\\PhysicalDisk(_Total)\\% Disk Time',"
        "'\\PhysicalDisk(_Total)\\Avg. Disk sec/Read',"
        "'\\PhysicalDisk(_Total)\\Avg. Disk sec/Write',"
        "'\\Memory\\Pages/sec',"
        "'\\Memory\\Available MBytes',"
        "'\\System\\Processor Queue Length' "
        "-SampleInterval 1 -MaxSamples 3 -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.CounterSamples } | "
        "Group-Object Path | ForEach-Object { "
        "[pscustomobject]@{ Counter=$_.Name; Avg=[math]::Round(($_.Group | "
        "Measure-Object CookedValue -Average).Average,4) } }",
        timeout=90,
    ) or []
    perf = {}
    for c in counters:
        name = (c.get("Counter") or "").split("\\")[-1]
        try:
            perf[name] = round(float(c.get("Avg") or 0), 4)
        except (TypeError, ValueError):
            pass
    r.fact("counters", perf)

    q = perf.get("Current Disk Queue Length", 0)
    dt = perf.get("% Disk Time", 0)
    if q >= 2 or dt >= 400:
        r.add(
            Finding(
                id="DISK_BOTTLENECK",
                title=f"磁盘是瓶颈：队列 {q}，%Disk Time {dt:.0f}%",
                severity="HIGH" if (q >= 4 or dt >= 800) else "MEDIUM",
                category="perf",
                evidence=[
                    f"Current Disk Queue Length = {q}（健康应 < 2 × 主轴数）",
                    f"%%Disk Time = {dt:.0f}%（> 100% 表示队列堆积）",
                    f"Avg Disk sec/Read = {perf.get('Avg. Disk sec/Read', 0)} s",
                ],
                impact="机械盘随机 IOPS 很低，突发读写会把队列打满，表现为间歇性卡顿"
                "（时好时坏），并连带拖慢输入法、数据库、编译等所有小文件操作。",
                fix="根治是换 SSD；过渡手段：把高频读写目录（日志、临时表、pagefile）"
                "迁到独立盘、关掉 SysMain/索引、给实时扫描做排除。",
                ref="references/perf-tuning.md#磁盘",
            )
        )
    pages = perf.get("Pages/sec", 0)
    if pages >= 1000:
        r.add(
            Finding(
                id="SWAP_THRASH",
                title=f"换页频繁：Pages/sec = {pages:,.0f}",
                severity="MEDIUM",
                category="perf",
                evidence=[f"Pages/sec = {pages:,.0f}（持续 > 1000 视为抖动）"],
                impact="说明物理内存不足，系统靠换页维持，磁盘被拖垮。",
                fix="压降常驻内存占用；确认页面文件所在盘不是系统盘；扩容内存。",
                ref="references/perf-tuning.md#内存",
            )
        )

    # Top 进程（内存 / CPU）
    procs = ps_json(
        "Get-Process | Sort-Object -Descending WorkingSet64 | Select-Object -First 20 "
        "Name,Id,@{n='MB';e={[math]::Round($_.WorkingSet64/1MB,1)}},"
        "@{n='CPU_s';e={[math]::Round($_.CPU,1)}},Path",
        timeout=60,
    ) or []
    r.fact("top_processes", procs)

    # 服务启动类型（自动 + 正在运行之外的可疑组合）
    svcs = ps_json(
        "Get-CimInstance Win32_Service | "
        "Select-Object Name,DisplayName,State,StartMode,StartName,PathName",
        timeout=70,
    ) or []
    r.fact("service_count", len(svcs))
    auto = [s for s in svcs if (s.get("StartMode") or "").lower() in ("auto", "automatic")]
    r.fact("auto_service_count", len(auto))

    # 电源方案
    po = ps("powercfg /getactivescheme", timeout=20)[1]
    r.fact("power_plan", po)
    if "381b4222" in po:  # 平衡
        r.add(
            Finding(
                id="POWER_BALANCED",
                title="电源方案为「平衡」，服务器/常开主机建议「高性能」",
                severity="LOW",
                category="perf",
                evidence=[po],
                impact="平衡方案会在低负载时降频，导致突发请求响应变慢。",
                fix="powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                auto_fixable=True,
                ref="references/perf-tuning.md#电源",
            )
        )

    # 预读 / 索引 / 视觉特效等「可关掉的常驻 I/O」
    checks = {
        "SysMain": "SysMain / Superfetch 预读服务",
        "WSearch": "Windows Search 索引服务",
    }
    for name, desc in checks.items():
        info = ps(
            f"try {{ $s = Get-Service -Name {name} -ErrorAction Stop; "
            f"'{{0}}|{{1}}' -f $s.Status, $s.StartType }} catch {{ 'missing|missing' }}",
            timeout=20,
        )[1]
        state, start = (info.split("|") + ["?", "?"])[:2]
        r.fact(f"service_{name}", {"state": state, "start": start})
        if state.lower() == "running" and name in ("SysMain", "WSearch"):
            r.add(
                Finding(
                    id=f"SVC_{name.upper()}_RUNNING",
                    title=f"{desc}正在运行，持续产生后台磁盘 I/O",
                    severity="LOW",
                    category="perf",
                    evidence=[f"{name}: {state} / {start}"],
                    impact="在机械盘或高负载主机上，预读与索引会与业务抢 IOPS，"
                    "造成间歇性卡顿。",
                    fix=f"Stop-Service {name}; Set-Service {name} -StartupType Disabled",
                    auto_fixable=True,
                    ref="references/perf-tuning.md#后台服务",
                )
            )

    return r


# --------------------------------------------------------------------------
# 3. 安全配置
# --------------------------------------------------------------------------

def collect_security() -> CheckResult:
    r = CheckResult(name="安全基线", category="sec")

    # --- 远程桌面 / 弱口令暴露面 ---
    rdp = ps_json(
        "Get-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' "
        "-Name fDenyTSConnections -ErrorAction SilentlyContinue | "
        "Select-Object fDenyTSConnections", timeout=25
    )
    rdp_on = None
    if rdp:
        v = rdp[0].get("fDenyTSConnections")
        rdp_on = str(v) in ("0", "False", "false")
    r.fact("rdp_enabled", rdp_on)
    r.fact("rdp_port", _reg_read(
        r"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp", "PortNumber"
    ))

    if rdp_on:
        nla = _reg_read(
            r"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp",
            "UserAuthentication",
        )
        r.fact("rdp_nla", nla)
        if nla in (0, None):
            r.add(
                Finding(
                    id="RDP_NO_NLA",
                    title="远程桌面未强制网络级身份验证（NLA）",
                    severity="HIGH",
                    category="sec",
                    evidence=[f"UserAuthentication={nla}（0=关闭）"],
                    impact="未启用 NLA 时，未认证连接就能建立 RDP 会话，"
                    "是 BlueKeep 类漏洞与暴力破解的入口。",
                    fix="系统属性→远程→勾选「仅允许运行使用网络级别身份验证的远程桌面的计算机连接」；"
                    "并把 RDP 限制到内网/VPN + 防火墙白名单。",
                    auto_fixable=True,
                    ref="references/hardening-windows.md#远程桌面",
                )
            )
        # 3389 直接暴露在公网
        if _port_listening(3389) and _public_ip_reachable():
            r.add(
                Finding(
                    id="RDP_EXPOSED",
                    title="RDP(3389) 正在监听，且主机存在公网出口",
                    severity="CRITICAL",
                    category="sec",
                    evidence=["3389 处于 LISTEN 状态"],
                    impact="公网可达的 RDP 是被爆破/勒索的首要目标，平均数小时内就会被打。",
                    fix="改为 VPN/堡垒机接入；防火墙只放行固定来源 IP；"
                    "启用账户锁定策略 + NLA + 强密码；必要时改默认端口（仅降噪，非根治）。",
                    ref="references/hardening-windows.md#远程桌面",
                )
            )

    # --- 账户与口令策略 ---
    users = ps_json(
        "Get-LocalUser | Select-Object Name,Enabled,LastLogon,PasswordRequired,"
        "PasswordLastSet,UserMayChangePassword", timeout=40
    ) or []
    r.fact("local_users", [
        {k: (str(v) if isinstance(v, (datetime,)) else v) for k, v in u.items()} for u in users
    ])
    enabled_no_pwd = [u for u in users if u.get("Enabled") and u.get("PasswordRequired") is False]
    for u in enabled_no_pwd:
        r.add(
            Finding(
                id="USER_NO_PASSWORD",
                title=f"本地账户 {u.get('Name')} 已启用且允许空密码",
                severity="CRITICAL",
                category="sec",
                evidence=["PasswordRequired=False, Enabled=True"],
                impact="空口令账户可被直接远程登录，等于敞开大门。",
                fix=f"net user {u.get('Name')} <强密码>；或 net user {u.get('Name')} /active:no",
                ref="references/hardening-windows.md#账户",
            )
        )

    admins = ps_json(
        "Get-LocalGroupMember -Group Administrators -ErrorAction SilentlyContinue | "
        "Select-Object Name,ObjectClass,PrincipalSource", timeout=40
    ) or []
    r.fact("administrators", admins)
    if len(admins) > 5:
        r.add(
            Finding(
                id="TOO_MANY_ADMINS",
                title=f"管理员组成员过多（{len(admins)} 个）",
                severity="MEDIUM",
                category="sec",
                evidence=[a.get("Name", "") for a in admins],
                impact="权限扩散，任一账户被攻破即等于拿到系统最高权限。",
                fix="按最小权限原则清理，改用受限账户 + 按需提权（UAC）。",
                ref="references/hardening-windows.md#账户",
            )
        )

    net_accounts = ps("net accounts", timeout=25)[1]
    r.fact("net_accounts", net_accounts)

    def _account_field(blob: str, *labels: str):
        """从 net accounts 输出里取字段值。支持中英文标签，取不到返回 None。

        取不到时**绝不返回默认值** —— 中文系统上 'Minimum password length' 匹配不到，
        如果这时返回 0，就会凭空造出一条"最小密码长度 0 位"的高危误报。
        """
        for lab in labels:
            m = re.search(rf"{re.escape(lab)}[^:\r\n]*[:：]\s*([^\r\n]+)", blob or "", re.I)
            if m:
                return m.group(1).strip()
        return None

    min_len_raw = _account_field(net_accounts, "密码长度最小值", "Minimum password length")
    lock_raw = _account_field(net_accounts, "锁定阈值", "Lockout threshold")
    max_age_raw = _account_field(net_accounts, "密码最长使用期限", "Maximum password age")

    r.fact("password_policy_raw", {
        "min_length": min_len_raw, "lockout": lock_raw, "max_age": max_age_raw})

    if min_len_raw is None:
        r.add(Finding(
            id="NET_ACCOUNTS_UNPARSED",
            title="无法解析账户策略输出，口令策略未能判定",
            severity="INFO",
            category="config",
            evidence=[(net_accounts or "net accounts 无输出")[:200]],
            impact="该机器上的口令策略这一项**没有被检查**，不能据此认为合规或不合适。",
            fix="手工核对：`net accounts`，或本地安全策略 → 账户策略 → 密码策略。",
            ref="references/hardening-windows.md#账户",
        ))
    else:
        m = re.search(r"\d+", min_len_raw)
        if m is None:
            r.fact("min_password_length", min_len_raw)
        else:
            min_len = int(m.group(0))
            r.fact("min_password_length", min_len)
            if min_len < 8:
                r.add(Finding(
                    id="WEAK_PASSWORD_POLICY",
                    title=f"最小密码长度仅 {min_len} 位",
                    severity="HIGH",
                    category="sec",
                    evidence=[f"密码长度最小值 = {min_len}"],
                    impact="弱口令策略下账户极易被字典爆破，配合无锁定阈值几乎必破。",
                    fix="net accounts /minpwlen:12 /maxpwage:90 /lockoutthreshold:5 "
                    "/lockoutduration:15 /lockoutwindow:15",
                    auto_fixable=True,
                    ref="references/hardening-windows.md#账户",
                ))

    if lock_raw is not None:
        r.fact("lockout_threshold_raw", lock_raw)
        if re.search(r"never|从不|无", lock_raw, re.I):
            r.add(Finding(
                id="NO_LOCKOUT",
                title="账户锁定策略未设置，可无限次尝试密码",
                severity="HIGH",
                category="sec",
                evidence=[f"锁定阈值 = {lock_raw}"],
                impact="在线口令爆破没有任何阻力，配合弱口令可在数小时内攻破。",
                fix="net accounts /lockoutthreshold:5 /lockoutduration:15 /lockoutwindow:15",
                auto_fixable=True,
                ref="references/hardening-windows.md#账户",
            ))

    # --- 防火墙 ---
    fw = ps_json(
        "Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,"
        "DefaultOutboundAction", timeout=40
    ) or []
    r.fact("firewall", fw)
    for p in fw:
        if not p.get("Enabled"):
            r.add(
                Finding(
                    id="FIREWALL_OFF",
                    title=f"防火墙配置「{p.get('Name')}」未启用",
                    severity="CRITICAL",
                    category="sec",
                    evidence=[f"{p.get('Name')}: Enabled=False"],
                    impact="主机失去最后的网络访问控制层，任何监听端口直接暴露。",
                    fix=f"Set-NetFirewallProfile -Profile {p.get('Name')} -Enabled True",
                    auto_fixable=True,
                    ref="references/hardening-windows.md#防火墙",
                )
            )

    # --- Defender ---
    mp = ps_json(
        "Get-MpComputerStatus | Select-Object AMServiceEnabled,AntivirusEnabled,"
        "RealTimeProtectionEnabled,AntispywareEnabled,BehaviorMonitorEnabled,"
        "IoavProtectionEnabled,AntivirusSignatureAge,AntivirusSignatureLastUpdated",
        timeout=50
    )
    if mp:
        m0 = mp[0]
        r.fact("defender", {k: str(v) for k, v in m0.items()})
        if not m0.get("RealTimeProtectionEnabled"):
            r.add(
                Finding(
                    id="DEFENDER_RTP_OFF",
                    title="Defender 实时保护被关闭",
                    severity="HIGH",
                    category="malware",
                    evidence=["RealTimeProtectionEnabled=False"],
                    impact="新落地的木马/勒索样本不会被拦截。",
                    fix="Set-MpPreference -DisableRealtimeMonitoring $false",
                    auto_fixable=True,
                    ref="references/malware-response.md#防护前置",
                )
            )
        age = m0.get("AntivirusSignatureAge")
        if isinstance(age, (int, float)) and age > 3:
            r.add(
                Finding(
                    id="DEFENDER_OUTDATED",
                    title=f"杀毒特征库已 {age} 天未更新",
                    severity="MEDIUM",
                    category="malware",
                    evidence=[f"AntivirusSignatureAge={age}"],
                    impact="旧特征库对近期样本近乎失明。",
                    fix="Update-MpSignature",
                    auto_fixable=True,
                    ref="references/malware-response.md",
                )
            )
    else:
        pref = ps_json("Get-MpPreference | Select-Object ExclusionPath,ExclusionExtension,"
                       "ExclusionProcess,DisableRealtimeMonitoring", timeout=40)
        r.fact("defender_preference", pref)
        r.fail("Get-MpComputerStatus 不可用（可能装了第三方杀软或已被卸载）")

    # Defender 排除项：过宽或被恶意软件添加
    pref = ps_json(
        "Get-MpPreference | Select-Object -ExpandProperty ExclusionPath", timeout=40
    )
    ex_paths = pref or []
    r.fact("defender_exclusions_path", ex_paths)
    bad = [p for p in ex_paths if isinstance(p, str) and
           p.strip() in ("C:\\", "C:\\*", "*", "C:\\Windows", "C:\\Users")]
    if bad:
        r.add(
            Finding(
                id="DEFENDER_BROAD_EXCLUSION",
                title=f"Defender 排除目录过于宽松：{', '.join(bad)}",
                severity="HIGH",
                category="malware",
                evidence=[f"ExclusionPath 含 {b}" for b in bad],
                impact="恶意软件常把自己所在目录加进排除列表来长期驻留。",
                fix="Remove-MpPreference -ExclusionPath '<该路径>'，只保留确有需要的具体子目录。",
                auto_fixable=True,
                ref="references/malware-response.md#排除项",
            )
        )

    # --- SMB / 共享 ---
    smb1 = ps_json(
        "Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol,"
        "EnableSMB2Protocol,RequireSecuritySignature,EncryptData,EnableInsecureGuestLogons",
        timeout=40
    )
    if smb1:
        s = smb1[0]
        r.fact("smb_server", s)
        if s.get("EnableSMB1Protocol"):
            r.add(
                Finding(
                    id="SMB1_ENABLED",
                    title="SMBv1 协议仍启用（永恒之蓝等漏洞载体）",
                    severity="CRITICAL",
                    category="sec",
                    evidence=["EnableSMB1Protocol=True"],
                    impact="WannaCry/NotPetya 正是通过 SMBv1 传播，且该协议本身无现代加固。",
                    fix="Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force；"
                    "并 Remove-WindowsFeature FS-SMB1。",
                    auto_fixable=True,
                    ref="references/hardening-windows.md#SMB",
                )
            )
        if s.get("EnableInsecureGuestLogons"):
            r.add(
                Finding(
                    id="SMB_INSECURE_GUEST",
                    title="允许不安全的来宾登录（InsecureGuestLogons）",
                    severity="MEDIUM",
                    category="sec",
                    evidence=["EnableInsecureGuestLogons=True"],
                    impact="可被中间人利用做凭据中继。",
                    fix="Set-SmbClientConfiguration -EnableInsecureGuestLogons $false -Force",
                    auto_fixable=True,
                    ref="references/hardening-windows.md#SMB",
                )
            )
        if not s.get("RequireSecuritySignature"):
            r.add(
                Finding(
                    id="SMB_SIGNING_OFF",
                    title="SMB 未强制签名，存在中继攻击风险",
                    severity="MEDIUM",
                    category="sec",
                    evidence=["RequireSecuritySignature=False"],
                    impact="攻击者可在中间人位置做 SMB 中继，横向移动到其他主机。",
                    fix="Set-SmbServerConfiguration -RequireSecuritySignature $true -Force",
                    auto_fixable=True,
                    ref="references/hardening-windows.md#SMB",
                )
            )

    shares = ps_json(
        "Get-SmbShare | Where-Object { $_.Name -notlike '*$' } | "
        "Select-Object Name,Path,Description", timeout=40
    ) or []
    r.fact("shares", shares)
    for sh in shares:
        p = (sh.get("Path") or "").rstrip("\\").lower()
        if p in ("c:\\", "d:\\", "e:\\"):
            r.add(
                Finding(
                    id="SHARE_ROOT_EXPOSED",
                    title=f"共享「{sh.get('Name')}」直接暴露整个磁盘根目录",
                    severity="HIGH",
                    category="sec",
                    evidence=[f"{sh.get('Name')} → {sh.get('Path')}"],
                    impact="盘符级共享一旦凭据泄露，整盘数据可被拖走。",
                    fix="改为共享具体子目录，并在 NTFS 上收紧 ACL。",
                    ref="references/hardening-windows.md#共享",
                )
            )

    # --- 更新 ---
    hf = ps_json(
        "Get-HotFix | Sort-Object InstalledOn -Descending | "
        "Select-Object -First 1 HotFixID,InstalledOn", timeout=50
    )
    if hf:
        r.fact("last_hotfix", hf[0])
        try:
            d = hf[0].get("InstalledOn")
            if isinstance(d, str) and d:
                dt = datetime.strptime(d.split("T")[0], "%Y-%m-%d")
                days = (datetime.now() - dt).days
                r.fact("days_since_hotfix", days)
                if days > 60:
                    r.add(
                        Finding(
                            id="PATCH_STALE",
                            title=f"距上次系统补丁已 {days} 天",
                            severity="MEDIUM",
                            category="sec",
                            evidence=[f"最近补丁 {hf[0].get('HotFixID')} 安装于 {d[:10]}"],
                            impact="未打补丁意味着公开 EXP 可直接命中。",
                            fix="启用自动更新并尽快重启；或定期用 Windows Update 手动检查。",
                            ref="references/hardening-windows.md#补丁",
                        )
                    )
        except Exception as e:  # noqa: BLE001 - 采集器必须健壮，但失败要留痕
            # 日期格式不认识时只跳过「补丁陈旧」这一项，不能静默吞掉
            r.note(f"补丁陈旧度判定跳过：{type(e).__name__}: {e}")

    # --- UAC ---
    uac = _reg_read(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
                    "EnableLUA")
    r.fact("uac_enable_lua", uac)
    if uac in (0, None):
        r.add(
            Finding(
                id="UAC_OFF",
                title="UAC（用户账户控制）已关闭",
                severity="HIGH",
                category="sec",
                evidence=[f"EnableLUA={uac}"],
                impact="进程默认全权限运行，恶意代码可静默提权。",
                fix="把 HKLM\\...\\Policies\\System\\EnableLUA 设为 1 并重启。",
                ref="references/hardening-windows.md#UAC",
            )
        )

    # --- 监听端口清单 ---
    listeners = _listening_ports()
    r.fact("listeners", listeners)

    # 同一端口的严重度取决于这台机器是什么角色：
    # 客户端系统（笔记本/办公机）默认开着 135/139/445 是完全正常的；
    # 服务器系统上这些端口暴露就是实打实的攻击面。
    is_server = _is_server_os()
    r.fact("os_is_server", is_server)

    # 绝不该出现的明文/遗留服务
    for l in listeners:
        if l["port"] in (23, 21, 512, 513, 514):
            r.add(Finding(
                id="LEGACY_SERVICE_LISTENING",
                title=f"明文遗留服务端口正在监听：{l['port']}",
                severity="MEDIUM",
                category="sec",
                evidence=[f"{l['port']}/{l['proto']} ← {l.get('process')} (pid {l.get('pid')})"],
                impact="Telnet/FTP/rsh 明文传输凭据，可被直接嗅探，且这些守护进程历史漏洞密集。",
                fix="确认无业务依赖后卸载或停用对应服务（TlntSvr / FTPSVC 等），"
                "并在防火墙上阻断该端口。",
                ref="references/hardening-windows.md#端口",
            ))

    # 数据库 / 缓存类：本应只监听 127.0.0.1，绑 0.0.0.0 即为暴露
    for l in listeners:
        if l["port"] in (1433, 3306, 5432, 6379, 27017, 9200, 11211) and l.get("bind_all"):
            r.add(Finding(
                id=f"DB_PORT_EXPOSED_{l['port']}",
                title=f"数据库/缓存端口 {l['port']} 监听在所有网卡",
                severity="HIGH" if l["port"] in (6379, 27017, 11211, 9200) else "MEDIUM",
                category="sec",
                evidence=[f"{l['port']}/{l['proto']} 监听 0.0.0.0 ← {l.get('process')}"],
                impact="Redis/MongoDB/ES/Memcached 若未鉴权且网络可达，可直接被写入或拖库"
                "（Redis 未授权写 SSH key 是经典利用链）。",
                fix="服务侧改为只监听 127.0.0.1 或内网地址并强制口令；"
                "防火墙侧用 New-NetFirewallRule 限制来源 IP。",
                ref="references/hardening-windows.md#数据库",
            ))

    # SMB / RPC：按主机角色定级
    smb_rpc = [l for l in listeners if l["port"] in (135, 139, 445)]
    if smb_rpc:
        sev = "MEDIUM" if is_server else "INFO"
        r.add(Finding(
            id="SMB_RPC_LISTENING",
            title=f"文件共享 / RPC 端口监听中（{', '.join(str(l['port']) for l in smb_rpc)}）",
            severity=sev,
            category="sec",
            evidence=[f"{l['port']} ← {l.get('process')}" for l in smb_rpc],
            impact=("服务器角色下，445/139 是勒索软件横向传播的首选入口；"
                    "135 常被用于 RPC 侦察。"
                    if is_server else
                    "当前为客户端系统且未检测到公网可达路径，属默认行为；"
                    "但若这台机器会接入不受信网络，仍需按下列方式收敛。"),
            fix="防火墙只放行内网来源；不需要文件共享就彻底关闭 SMB 服务；"
            "同时确保 SMBv1 已关闭、SMB 签名已强制。",
            ref="references/hardening-windows.md#端口",
        ))
    return r


def _exe_path(raw) -> str:
    """从服务路径/命令行里提取可执行文件路径。

    Win32_Service 的 PathName 常见形态：
        "C:\\Program Files\\App\\svc.exe" -k arg
        C:\\Windows\\system32\\svchost.exe -k netsvcs
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith('"'):
        end = s.find('"', 1)
        if end > 0:
            return s[1:end]
    # 无引号：取到第一个 ".exe" 为止
    m = re.match(r"^(.*?\.exe)\b", s, re.I)
    return m.group(1) if m else s.split(" ")[0]


def _is_server_os() -> bool:
    """判断是否为 Windows Server（决定若干基线的严格程度）。"""
    name = str(_reg_read(
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName") or "")
    if not name:
        name = str(_reg_read(
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "EditionID") or "")
    return "server" in name.lower()


# --------------------------------------------------------------------------
# 4. 恶意代码 / 持久化 / 后门
# --------------------------------------------------------------------------

_WINLOGGON_SUSPICIOUS = re.compile(
    r"(powershell|pwsh|cmd\.exe|wscript|cscript|mshta|rundll32|regsvr32|curl|wget|bitsadmin|"
    r"certutil|msiexec|/tmp/|%temp%|\\temp\\|\\appdata\\|base64|enc | -enc)",
    re.I,
)


def collect_malware(deep: bool = False) -> CheckResult:
    r = CheckResult(name="恶意代码与持久化", category="malware")

    # --- 注册表自启动 ---
    autoruns: list[dict] = []
    run_keys = [
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM Run"),
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM RunOnce"),
        (r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM Run(x86)"),
    ]
    user_run = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    for hive, path, label in (
        ("HKLM", k, l) for k, l in run_keys
    ):
        for name, val in _reg_enum(hive, path).items():
            autoruns.append({"location": label, "name": name, "value": val})
    for name, val in _reg_enum("HKCU", user_run).items():
        autoruns.append({"location": "HKCU Run", "name": name, "value": val})

    # Winlogon Shell / Userinit（后门高频位置）
    for key in ("Shell", "Userinit", "AppSetup", "Taskman", "VMApplet"):
        v = _reg_read(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", key)
        if v is None:
            continue
        base = {"Shell": "explorer.exe", "Userinit": "userinit.exe",
                "Taskman": "", "AppSetup": ""}.get(key, "")
        if v and v.strip().lower() not in (base.lower(), base.lower() + ","):
            autoruns.append({"location": f"Winlogon\\{key}", "name": key, "value": v})

    # AppInit_DLLs / AppCertDlls（经典注入点）
    for path, label in (
        (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows", "AppInit_DLLs"),
        (r"SOFTWARE\Wow6432Node\Microsoft\Windows NT\CurrentVersion\Windows", "AppInit_DLLs(x86)"),
    ):
        v = _reg_read(path, "AppInit_DLLs")
        if v and str(v).strip():
            autoruns.append({"location": label, "name": "AppInit_DLLs", "value": v})

    # IFEO 劫持
    ifeo_root = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
    for sub in _reg_subkeys("HKLM", ifeo_root):
        dbg = _reg_read(f"{ifeo_root}\\{sub}", "Debugger")
        if dbg:
            autoruns.append({"location": f"IFEO\\{sub}", "name": "Debugger", "value": dbg})

    r.fact("autoruns", autoruns)
    for a in autoruns:
        blob = f"{a['name']} = {a['value']}"
        if _WINLOGGON_SUSPICIOUS.search(str(a["value"])):
            r.add(
                Finding(
                    id="AUTORUN_SUSPICIOUS",
                    title=f"可疑自启动项：{a['location']} → {a['name']}",
                    severity="HIGH",
                    category="malware",
                    evidence=[blob],
                    impact="含脚本解释器/编码命令/临时目录路径的自启动项是后门驻留的典型特征。",
                    fix="先导出该键值留证，再删除：reg export 备份后 reg delete；"
                    "同时到对应文件路径做样本取证。",
                    ref="references/malware-response.md#持久化位置",
                )
            )
        elif a["location"].startswith("IFEO"):
            r.add(
                Finding(
                    id="IFEO_HIJACK",
                    title=f"IFEO 劫持：{a['name']} → {a['value']}",
                    severity="HIGH",
                    category="malware",
                    evidence=[blob],
                    impact="劫持程序映像执行是提权与持久化的经典手法（粘滞键后门等）。",
                    fix="删除该 IFEO 子键；核对是否为运维有意配置的调试器。",
                    ref="references/malware-response.md#持久化位置",
                )
            )

    # --- 计划任务 ---
    # 关键：微软自带的系统任务大量使用 rundll32 / powershell，且路径在 system32。
    # 把它们报成"可疑"是典型的狼来了，会让真正的后门淹没在噪音里。
    tasks = ps_json(
        "Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' } | ForEach-Object { "
        "$a = $_.Actions | Select-Object -First 1; "
        "[pscustomobject]@{ TaskPath=$_.TaskPath; TaskName=$_.TaskName; "
        "Execute=$a.Execute; Arguments=$a.Arguments; "
        "Author=$_.Author; UserId=$_.Principal.UserId } }", timeout=90
    ) or []
    r.fact("scheduled_tasks_count", len(tasks))

    _SYS_EXE = re.compile(r"(%windir%|%systemroot%|\\windows)\\[^\\]*\\?system32\\", re.I)
    sus_tasks = []
    for t in tasks:
        exe = str(t.get("Execute") or "")
        args = str(t.get("Arguments") or "")
        blob = f"{exe} {args}"
        low = exe.lower()
        tpath = str(t.get("TaskPath") or "").lower()

        # 微软自带任务：位于 \Microsoft\ 下且执行体在 system32 → 直接放行
        if tpath.startswith("\\microsoft\\") and _SYS_EXE.search(low):
            continue
        if not exe:
            continue

        if _WRITABLE_DIR.search(low) and not _TRUSTED_SUBTREE.search(low):
            sus_tasks.append((t, "HIGH", "任务执行体位于用户可写目录（Temp/AppData/ProgramData）"))
        elif re.search(r"-e(nc(odedcommand)?)?\s+[A-Za-z0-9+/=]{60,}", blob, re.I):
            sus_tasks.append((t, "HIGH", "使用超长 base64 编码命令（典型无文件攻击特征）"))
        elif re.search(r"(curl|wget|mshta|certutil\s+-urlcache|bitsadmin|"
                       r"invoke-webrequest|downloadstring|downloadfile)", blob, re.I):
            sus_tasks.append((t, "HIGH", "含下载并执行特征"))
        elif re.search(r"powershell|pwsh|wscript|cscript|mshta", low, re.I) \
                and not _SYS_EXE.search(low) and not _TRUSTED_SUBTREE.search(low):
            sus_tasks.append((t, "MEDIUM", "非系统目录的脚本解释器任务"))

    r.fact("suspicious_tasks", [
        {"path": f"{t.get('TaskPath')}{t.get('TaskName')}", "why": why,
         "exec": f"{t.get('Execute')} {t.get('Arguments')}"[:250]}
        for t, _sev, why in sus_tasks
    ])
    for t, sev, why in sus_tasks[:20]:
        r.add(
            Finding(
                id="TASK_SUSPICIOUS",
                title=f"可疑计划任务：{t.get('TaskPath')}{t.get('TaskName')}",
                severity=sev,
                category="malware",
                evidence=[f"判定：{why}",
                          f"{t.get('Execute')} {t.get('Arguments')}"[:250],
                          f"创建者={t.get('Author') or '未知'}  运行身份={t.get('UserId') or '未知'}"],
                impact="计划任务是常见驻留点。合法的系统任务不会把执行体放在用户可写目录，"
                "也不会用超长编码命令。",
                fix="先核对创建者与文件签名（Get-AuthenticodeSignature），"
                "确认非业务/非微软后：Disable-ScheduledTask 再删除，并检查对应脚本文件。",
                ref="references/malware-response.md#持久化位置",
            )
        )

    # --- 服务的可执行路径是否在异常目录 ---
    svcs = ps_json(
        "Get-CimInstance Win32_Service | "
        "Select-Object Name,DisplayName,State,StartMode,PathName,StartName", timeout=70
    ) or []
    candidates = []
    for s in svcs:
        p = str(s.get("PathName") or "")
        if not p:
            continue
        if _TRUSTED_SUBTREE.search(p):
            continue
        if _WRITABLE_DIR.search(p):
            candidates.append(s)

    sigmap = _signature_status([_exe_path(s.get("PathName")) for s in candidates])
    bad_svc = [s for s in candidates
               if not _is_trusted_signed(_exe_path(s.get("PathName")), sigmap)]
    r.fact("suspicious_services", [
        {"name": s.get("Name"), "path": s.get("PathName"),
         "signature": sigmap.get(_exe_path(s.get("PathName")), "未查到")} for s in bad_svc])
    for s in bad_svc:
        p = _exe_path(s.get("PathName"))
        r.add(
            Finding(
                id="SVC_BAD_PATH",
                title=f"服务「{s.get('Name')}」可执行文件位于用户可写目录且无有效签名",
                severity="HIGH",
                category="malware",
                evidence=[str(s.get("PathName"))[:220],
                          f"状态 {s.get('State')}/{s.get('StartMode')}，"
                          f"签名 {sigmap.get(p, '未查到')}"],
                impact="正版软件的服务主体不会放在 Temp/AppData，且必然有有效数字签名。"
                "两者同时不满足，是落地型木马与挖矿程序的典型特征。",
                fix="先取证（复制该目录与文件、记录哈希 sha256），"
                "再 Stop-Service + Set-Service -StartupType Disabled，"
                "最后用 Defender 全盘扫描并排查它的落地途径。",
                ref="references/malware-response.md#驻留型服务",
            )
        )

    # --- 高危路径进程 ---
    procs = ps_json(
        "Get-CimInstance Win32_Process | "
        "Select-Object Name,ProcessId,ExecutablePath,CommandLine,ParentProcessId", timeout=70
    ) or []
    r.fact("process_count", len(procs))

    proc_cand = []
    for p in procs:
        path = str(p.get("ExecutablePath") or "")
        cl = str(p.get("CommandLine") or "")
        if not path:
            continue
        low = path.lower()
        if _TRUSTED_SUBTREE.search(path):
            continue
        # 系统关键进程伪装：名字对但路径不在 system32
        if re.search(r"\\(svchost|lsass|csrss|winlogon|services|smss)\.exe$", low) \
                and not re.search(r"[\\/]windows[\\/](system32|syswow64)[\\/]", low):
            proc_cand.append((p, "CRITICAL", "系统进程名伪装（路径不在 system32）"))
            continue
        if _WRITABLE_DIR.search(low):
            proc_cand.append((p, "HIGH", "可执行文件位于用户可写目录"))
            continue
        if re.search(r"-e(nc(odedcommand)?)?\s+[A-Za-z0-9+/=]{60,}", cl, re.I):
            proc_cand.append((p, "HIGH", "命令行含超长 base64 编码（无文件攻击特征）"))

    psig = _signature_status([_exe_path(p.get("ExecutablePath")) for p, _s, _w in proc_cand])
    sus_proc = []
    for p, sev, why in proc_cand:
        if why.startswith("系统进程名伪装"):
            sus_proc.append((p, sev, why))
            continue
        if not _is_trusted_signed(_exe_path(p.get("ExecutablePath")), psig):
            sus_proc.append((p, sev, why))

    r.fact("suspicious_processes", [
        {"pid": p.get("ProcessId"), "name": p.get("Name"), "path": p.get("ExecutablePath"),
         "why": why, "signature": psig.get(_exe_path(p.get("ExecutablePath")), "未查到")}
        for p, _s, why in sus_proc])
    for p, sev, why in sus_proc[:20]:
        exe = _exe_path(p.get("ExecutablePath"))
        r.add(
            Finding(
                id="PROC_SUSPICIOUS",
                title=f"可疑进程：{p.get('Name')} (PID {p.get('ProcessId')}) — {why}",
                severity=sev,
                category="malware",
                evidence=[str(p.get("ExecutablePath") or "")[:220],
                          str(p.get("CommandLine") or "")[:220],
                          f"父进程 PID={p.get('ParentProcessId')}，"
                          f"签名 {psig.get(exe, '未查到')}"],
                impact="从用户临时目录运行、或伪装系统进程名、或带长 base64 编码命令的进程，"
                "常见于木马加载器与内存马。",
                fix="先抓证据（进程哈希、/proc 或 Get-Process 的内存镜像、父子进程链），"
                "再终止进程并排查它的持久化来源。",
                ref="references/malware-response.md#可疑进程",
            )
        )

    # --- hosts 文件与 DNS 劫持 ---
    hosts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    if hosts.exists():
        try:
            lines = [
                l.strip() for l in hosts.read_text(encoding="utf-8", errors="replace").splitlines()
                if l.strip() and not l.strip().startswith("#")
            ]
            r.fact("hosts_entries", lines)
            if lines:
                r.add(
                    Finding(
                        id="HOSTS_MODIFIED",
                        title=f"hosts 文件有 {len(lines)} 条静态解析记录",
                        severity="MEDIUM",
                        category="malware",
                        evidence=lines[:10],
                        impact="hosts 劫持常用于把杀软更新域名/银行域名指向攻击者，"
                        "或阻断安全软件联网。",
                        fix="逐条核对来源；与基线比对；非业务需要则清空并恢复为默认。",
                        ref="references/malware-response.md#hosts",
                    )
                )
        except Exception as e:  # noqa: BLE001
            r.fail(f"读取 hosts 失败: {e}")

    # --- 影子副本/备份是否被删（勒索前置动作）---
    vss = ps("vssadmin list shadows 2>&1", timeout=40)[1]
    r.fact("vssadmin_output_head", (vss or "")[:400])
    if vss and re.search(r"没有找到|No items found|未找到", vss, re.I):
        r.fact("vss_no_shadows", True)

    if deep:
        _malware_deep(r)
    return r


def _malware_deep(r: CheckResult) -> None:
    """深扫：WMI 事件订阅、LSA 包、BITS 任务、最近落地可执行文件、Defender 威胁历史。"""
    # WMI 永久事件订阅（无文件持久化）
    subs = ps_json(
        "Get-CimInstance -Namespace root\\subscription -ClassName __EventFilter "
        "-ErrorAction SilentlyContinue | Select-Object Name,Query", timeout=50
    ) or []
    r.fact("wmi_event_filters", subs)
    if subs:
        r.add(
            Finding(
                id="WMI_SUBSCRIPTION",
                title=f"存在 {len(subs)} 个 WMI 永久事件订阅",
                severity="HIGH",
                category="malware",
                evidence=[f"{s.get('Name')}: {str(s.get('Query'))[:160]}" for s in subs],
                impact="WMI 事件订阅可在无文件、无计划任务的情况下持久化执行代码，"
                "是高级后门常用手法。",
                fix="确认非业务组件后：Get-CimInstance __EventFilter/CommandLineEventConsumer/"
                "__FilterToConsumerBinding 三者一并删除。",
                ref="references/malware-response.md#WMI驻留",
            )
        )

    # LSA 认证包（密码抓取）
    lsa = _reg_read(r"SYSTEM\CurrentControlSet\Control\Lsa", "Authentication Packages")
    lsa_pkgs = _reg_enum("HKLM", r"SYSTEM\CurrentControlSet\Control\Lsa")
    known = {"msv1_0", "kerberos", "schannel", "wdigest", "tspkg", "pku2u", "cloudap",
             "negoexts", "livessp", "ap", "security packages", "authentication packages",
             "notification packages", "osconfig", "sd", "jd", "skew1", "gbg", "data",
             "limitblankpassworduse", "restrictanonymous", "lmcompatibilitylevel",
             "nolmhash", "runasppl", "disabledomaincreds", "auditbaseobjects",
             "crashonauditfail", "fullprivilegeauditing", "noLMHash", "forceuserlogoff",
             "lsa_packages"}
    extra = [k for k in lsa_pkgs if k.lower() not in known]
    r.fact("lsa_extra_values", extra)
    for k in extra:
        if "dll" in str(lsa_pkgs[k]).lower() or "\\" in str(lsa_pkgs[k]):
            r.add(
                Finding(
                    id="LSA_PACKAGE_EXTRA",
                    title=f"LSA 下存在非标准项：{k}",
                    severity="MEDIUM",
                    category="malware",
                    evidence=[f"{k} = {lsa_pkgs[k]}"],
                    impact="伪装成 LSA 认证包的 DLL 可在 lsass 进程内窃取明文/哈希凭据。",
                    fix="核对 DLL 签名与厂商；非微软组件需评估来源，可疑则删除。",
                    ref="references/malware-response.md#凭据窃取",
                )
            )

    # Authentication Packages 的「值」本身：lsass 会加载这里列出的每个 DLL。
    # 追加一个非微软 DLL 即为经典抓密码后门（如 mimilib），必须单独判。
    _pkgs = lsa if isinstance(lsa, (list, tuple)) else ([lsa] if lsa else [])
    pkgs = [str(x) for x in _pkgs if str(x).strip()]
    if pkgs:
        r.fact("lsa_auth_packages", pkgs)
    _ms_pkgs = {
        "msv1_0.dll", "kerberos.dll", "schannel.dll", "wdigest.dll", "tspkg.dll",
        "pku2u.dll", "cloudap.dll", "negoexts.dll", "livessp.dll", "security.dll",
        "cryptdll.dll", "dpapisrv.dll", "kdcsvc.dll", "negoexts",
        "msv1_0", "kerberos", "schannel", "wdigest", "tspkg", "pku2u",
        "cloudap", "livessp",
    }
    rogue = [p for p in pkgs if p.lower() not in _ms_pkgs]
    if rogue:
        r.add(
            Finding(
                id="LSA_AUTH_PACKAGE_ROGUE",
                title="LSA 认证包列表被插入了非微软 DLL",
                severity="CRITICAL",
                category="malware",
                evidence=rogue[:10],
                impact="Authentication Packages 里的 DLL 由 lsass 在登录时直接加载，"
                "注入其中一个即可在进程内抓取明文口令与 NTLM 哈希。",
                fix="先备份注册表，确认该 DLL 来源非业务组件后移除该项并重启。"
                "查看命令：reg query \"HKLM\\SYSTEM\\CurrentControlSet\\Control\\Lsa\" "
                "/v \"Authentication Packages\"；"
                "删除命令：reg add \"HKLM\\SYSTEM\\CurrentControlSet\\Control\\Lsa\" "
                "/v \"Authentication Packages\" /t REG_MULTI_SZ /d \"msv1_0.dll\\0kerberos.dll\" /f",
                ref="references/malware-response.md#凭据窃取",
            )
        )

    # BITS 任务
    bits = ps_json(
        "Get-BitsTransfer -AllUsers -ErrorAction SilentlyContinue | "
        "Select-Object DisplayName,OwnerAccount,JobState,FileList", timeout=50
    ) or []
    if bits:
        r.fact("bits_jobs", bits)
        r.add(
            Finding(
                id="BITS_JOBS",
                title=f"存在 {len(bits)} 个 BITS 传输任务",
                severity="LOW",
                category="malware",
                evidence=[f"{b.get('DisplayName')} ({b.get('JobState')})" for b in bits[:10]],
                impact="BITS 常被滥用做隐蔽下载。",
                fix="确认非业务后 Complete/Remove-BitsTransfer 清理。",
                ref="references/malware-response.md",
            )
        )

    # 最近 7 天落地的可执行文件（常见目录）
    roots = [
        os.environ.get("SystemRoot", r"C:\Windows") + r"\Temp",
        os.environ.get("TEMP", ""),
        os.environ.get("ProgramData", r"C:\ProgramData"),
        os.environ.get("PUBLIC", r"C:\Users\Public"),
    ]
    recent: list[dict] = []
    cutoff = time.time() - 7 * 86400
    exts = {".exe", ".dll", ".scr", ".ps1", ".vbs", ".js", ".bat", ".cmd", ".hta", ".jar"}
    for root in filter(None, roots):
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            if dirpath.count(os.sep) - root.count(os.sep) > 3:
                _dirs[:] = []
                continue
            for fn in files:
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                if st.st_mtime >= cutoff and Path(fn).suffix.lower() in exts:
                    recent.append({
                        "path": fp,
                        "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
    recent.sort(key=lambda x: x["mtime"], reverse=True)
    r.fact("recent_executables", recent[:60])
    if len(recent) >= 20:
        r.add(
            Finding(
                id="RECENT_DROPPED_BINARIES",
                title=f"近 7 天在临时/公共目录落地了 {len(recent)} 个可执行文件",
                severity="MEDIUM",
                category="malware",
                evidence=[f"{x['mtime']}  {x['path']}  ({x['size']}B)" for x in recent[:12]],
                impact="临时目录是 dropper 的主要落地点，正常软件很少在此长期驻留。",
                fix="逐个核对签名与来源（Get-AuthenticodeSignature）；无签名且非业务的一律隔离。",
                ref="references/malware-response.md#落地样本",
            )
        )

    # Defender 威胁历史
    threats = ps_json(
        "Get-MpThreatDetection -ErrorAction SilentlyContinue | "
        "Sort-Object InitialDetectionTime -Descending | Select-Object -First 30 "
        "ThreatID,InitialDetectionTime,Resources,ActionSuccess", timeout=60
    ) or []
    r.fact("defender_threats", threats)
    if threats:
        r.add(
            Finding(
                id="DEFENDER_THREAT_HISTORY",
                title=f"Defender 历史检出 {len(threats)} 条威胁记录",
                severity="HIGH",
                category="malware",
                evidence=[f"{t.get('InitialDetectionTime')} → {t.get('Resources')}"
                          for t in threats[:8]],
                impact="有检出说明曾发生实际感染；若处置失败（ActionSuccess=False）则可能仍在。",
                fix="逐条核对处置结果；失败项做全盘扫描 + 手工清除；确认根因入口（U盘/弱口令/漏洞）。",
                ref="references/malware-response.md",
            )
        )


# --------------------------------------------------------------------------
# 5. 日志与崩溃
# --------------------------------------------------------------------------

_CRASH_EVENT_IDS = {
    41: ("CRITICAL", "内核非正常关机（电源/蓝屏后无干净关闭）"),
    1001: ("HIGH", "Windows Error Reporting 记录了崩溃报告（BugCheck）"),
    6008: ("HIGH", "系统意外关机"),
    7031: ("MEDIUM", "服务异常终止（SCM 将按失败恢复策略重启）"),
    7034: ("MEDIUM", "服务多次异常终止"),
    1000: ("HIGH", "应用程序崩溃（Application Error）"),
    1026: ("HIGH", ".NET 运行时未处理异常导致进程终止"),
}


def collect_logs(hours: int = 48) -> CheckResult:
    r = CheckResult(name="事件日志", category="log")
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")

    # Level 1=Critical、2=Error；Warning(3) 量大且噪音多，不纳入
    data = ps_json(
        "Get-WinEvent -FilterHashtable "
        f"@{{LogName=@('System','Application'); Level=@(1,2); StartTime='{since}'}} "
        "-MaxEvents 400 -ErrorAction SilentlyContinue | "
        "Select-Object LogName,Id,LevelDisplayName,ProviderName,TimeCreated,"
        "@{n='Msg';e={($_.Message -split \"`n\")[0]}}", timeout=120
    ) or []
    r.fact("window_hours", hours)
    r.fact("error_events", data[:120])
    r.fact("error_event_count", len(data))

    # 事件 ID 频次统计
    freq: dict[int, int] = {}
    for e in data:
        try:
            eid = int(e.get("Id"))
        except (TypeError, ValueError):
            continue
        freq[eid] = freq.get(eid, 0) + 1
    r.fact("event_id_freq", dict(sorted(freq.items(), key=lambda kv: -kv[1])[:20]))

    seen_ids = set()
    for e in data:
        try:
            eid = int(e.get("Id"))
        except (TypeError, ValueError):
            continue
        if eid not in _CRASH_EVENT_IDS or eid in seen_ids:
            continue
        seen_ids.add(eid)
        sev, desc = _CRASH_EVENT_IDS[eid]
        n = freq.get(eid, 1)
        r.add(
            Finding(
                id=f"EVENT_{eid}",
                title=f"{desc}（事件 {eid}，{n} 次）",
                severity=sev,
                category="crash" if eid in (41, 1001, 6008, 1000, 1026) else "log",
                evidence=[
                    f"{e.get('TimeCreated')}  {e.get('ProviderName')}",
                    str(e.get("Msg") or "")[:200],
                ],
                impact="非正常关机/进程崩溃往往指向驱动、硬件或内存问题，"
                "重复出现说明根因未解决。",
                fix="结合 minidump 与驱动版本定位；参见崩溃诊断流程。",
                ref="references/logs-and-crash.md",
            )
        )

    # 事件日志被清空的痕迹（攻击者常用）
    cleared = ps_json(
        "Get-WinEvent -FilterHashtable @{LogName='Security'; ID=1102} "
        "-MaxEvents 10 -ErrorAction SilentlyContinue | "
        "Select-Object TimeCreated,@{n='Msg';e={($_.Message -split \"`n\")[0]}}", timeout=60
    ) or []
    if cleared:
        r.fact("audit_log_cleared", cleared)
        r.add(
            Finding(
                id="AUDIT_LOG_CLEARED",
                title=f"安全审计日志被清空过 {len(cleared)} 次（事件 1102）",
                severity="CRITICAL",
                category="malware",
                evidence=[f"{c.get('TimeCreated')} {c.get('Msg')}" for c in cleared],
                impact="清空审计日志是入侵后清除痕迹的强烈信号，通常伴随账号创建或提权。",
                fix="立即按入侵事件响应：隔离主机、保全证据（内存/磁盘镜像）、"
                "排查账号与持久化、评估数据泄露。",
                ref="references/malware-response.md#入侵判定",
            )
        )

    return r


def collect_crash() -> CheckResult:
    r = CheckResult(name="崩溃与转储", category="crash")
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))

    # --- dump 文件清单 ---
    minidumps = []
    md = windir / "Minidump"
    if md.is_dir():
        for f in sorted(md.glob("*.dmp"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            st = f.stat()
            minidumps.append({
                "path": str(f),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    r.fact("minidumps", minidumps)

    memdump = windir / "MEMORY.DMP"
    if memdump.exists():
        st = memdump.stat()
        minidumps.insert(0, {
            "path": str(memdump), "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
        r.fact("memory_dmp", True)

    if len(minidumps) >= 3:
        r.add(
            Finding(
                id="REPEATED_BLUESCREEN",
                title=f"存在 {len(minidumps)} 个崩溃转储，疑似反复蓝屏",
                severity="HIGH",
                category="crash",
                evidence=[f"{d['mtime']}  {Path(d['path']).name}  {human_bytes(d['size'])}"
                          for d in minidumps[:8]],
                impact="反复蓝屏意味着驱动/硬件冲突未解决，业务会周期性中断。",
                fix="用 WinDbg/`!analyze -v` 读取 dump 定位故障模块；"
                "重点核对第三方驱动版本（虚拟化、安全软件、存储驱动）。",
                ref="references/logs-and-crash.md#蓝屏诊断",
            )
        )

    # --- 崩溃配置 ---
    cp = ps_json(
        "Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\CrashControl' | "
        "Select-Object CrashDumpEnabled,AutoReboot,Overwrite,DumpFile", timeout=30
    )
    if cp:
        c = cp[0]
        r.fact("crash_control", {k: str(v) for k, v in c.items()})
        if c.get("CrashDumpEnabled") in (0, "0"):
            r.add(
                Finding(
                    id="DUMP_DISABLED",
                    title="崩溃转储已关闭，蓝屏后将无据可查",
                    severity="MEDIUM",
                    category="crash",
                    evidence=["CrashDumpEnabled=0"],
                    impact="下次蓝屏没有 dump，只能靠猜，排障周期被拉长数倍。",
                    fix="设置为「小内存转储(256KB)」或「自动内存转储」："
                    "CrashDumpEnabled=7(Automatic)/3(Small)，并确认 DumpFile 所在盘有空间。",
                    auto_fixable=True,
                    ref="references/logs-and-crash.md#转储配置",
                )
            )
        if str(c.get("AutoReboot", "")).strip().lower() in ("true", "1", "yes"):
            r.note("AutoReboot=True：蓝屏会自动重启，注意从事件日志 41/1001 回溯。")

    # --- WER 报告目录 ---
    wer_dirs = [
        windir / "LiveKernelReports",
        windir / "SystemTemp",
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows"
        / "WER" / "ReportArchive",
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows"
        / "WER" / "ReportQueue",
    ]
    wer = []
    for d in wer_dirs:
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file() and f.stat().st_size > 0:
                    wer.append(str(f))
                    if len(wer) > 200:
                        break
    r.fact("wer_files_count", len(wer))
    if wer:
        r.fact("wer_sample", wer[:30])

    # --- JVM 崩溃（hs_err_pid）---
    # JVM 崩溃日志（hs_err_pid*.log）——全盘递归扫描代价过高且会拖慢系统，
    # 这里只扫「最可能落地」的位置；其他目录请用 cli 的 --deep 或手工指定。
    jvm: list[str] = []
    search_dirs = [os.environ.get("TEMP", ""), os.environ.get("TMP", ""), os.getcwd()]
    for root in ("C:\\", "D:\\", "E:\\", "F:\\"):
        if os.path.isdir(root):
            search_dirs.append(root)
            for sub in ("tomcat", "apache-tomcat", "AppServer", "app", "services", "work",
                        "java", "Program Files", "Program Files (x86)"):
                p = os.path.join(root, sub)
                if os.path.isdir(p):
                    search_dirs.append(p)
    for base in search_dirs:
        if not base or not os.path.isdir(base):
            continue
        try:
            jvm.extend(_glob.glob(os.path.join(base, "hs_err_pid*.log")))
            jvm.extend(_glob.glob(os.path.join(base, "*", "hs_err_pid*.log")))
            jvm.extend(_glob.glob(os.path.join(base, "*", "*", "hs_err_pid*.log")))
        except Exception as e:  # noqa: BLE001 - 目录不可读时跳过，但留痕
            r.note(f"JVM 崩溃日志扫描跳过 {base}：{type(e).__name__}: {e}")
            continue
    jvm = sorted(set(jvm))
    r.fact("jvm_crash_logs", jvm[:30])
    if jvm:
        r.add(
            Finding(
                id="JVM_CRASH_FILES",
                title=f"发现 {len(jvm)} 个 JVM 崩溃日志（hs_err_pid）",
                severity="MEDIUM",
                category="crash",
                evidence=[str(p) for p in jvm[:8]],
                impact="JVM 级崩溃通常是堆外内存耗尽（DirectByteBuffer/mmap）或 JNI 段错误，"
                "而不是普通 OOM。",
                fix="读 hs_err 头部的 Problematic frame 与内存映射摘要；"
                "若是 Netty/Lettuce 之类堆外分配，检查 -XX:MaxDirectMemorySize 与释放逻辑。",
                ref="references/logs-and-crash.md#jvm崩溃",
            )
        )

    # --- 磁盘 SMART 健康 ---
    smart = ps_json(
        "Get-PhysicalDisk -ErrorAction SilentlyContinue | "
        "Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,Size", timeout=50
    ) or []
    r.fact("physical_disks", smart)
    for d in smart:
        if str(d.get("HealthStatus")) not in ("Healthy", "健康", "None", ""):
            r.add(
                Finding(
                    id="DISK_UNHEALTHY",
                    title=f"物理盘「{d.get('FriendlyName')}」健康状态异常：{d.get('HealthStatus')}",
                    severity="CRITICAL",
                    category="crash",
                    evidence=[f"HealthStatus={d.get('HealthStatus')} "
                              f"OperationalStatus={d.get('OperationalStatus')}"],
                    impact="坏盘会导致 IO 超时、文件系统损坏、数据丢失与系统随机卡死。",
                    fix="立即备份关键数据；检查 SMART 属性（Reallocated_Sector_Ct 等）；"
                    "准备更换磁盘。",
                    ref="references/logs-and-crash.md#硬件",
                )
            )
    return r


# --------------------------------------------------------------------------
# 5b. 应用栈（IIS / Tomcat / 数据库）探测
# --------------------------------------------------------------------------

def collect_appstack() -> CheckResult:
    r = CheckResult(name="应用栈探测", category="config")

    # IIS
    iis = ps_json(
        "Import-Module WebAdministration -ErrorAction SilentlyContinue; "
        "if (Get-Module WebAdministration) { "
        "Get-Website | Select-Object Name,State,PhysicalPath,Bindings,ApplicationPool } else { @() }",
        timeout=60
    ) or []
    r.fact("iis_sites", iis)
    if iis:
        r.fact("iis_present", True)

    # Tomcat / Java
    java = ps_json(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'java(w)?\\.exe' } | "
        "Select-Object ProcessId,Name,CommandLine", timeout=60
    ) or []
    r.fact("java_processes", java)
    if java:
        r.fact("tomcat_present", True)
        for p in java:
            cl = str(p.get("CommandLine") or "")
            # 常见问题：堆参数缺失 / 编码未设 / 关闭钩子被 kill
            if not re.search(r"-Xmx", cl):
                r.add(
                    Finding(
                        id="TOMCAT_NO_XMX",
                        title=f"Java 进程 {p.get('ProcessId')} 未设置 -Xmx",
                        severity="MEDIUM",
                        category="perf",
                        evidence=[cl[:220]],
                        impact="默认堆上限仅物理内存的 1/4 或 1GB 级别，高并发下容易 OOM，"
                        "或与同机其他实例争抢内存。",
                        fix="在 setenv.bat/sh 中显式设置 -Xms/-Xmx（建议 Xms=Xmx）、"
                        "-XX:MaxDirectMemorySize、-XX:+HeapDumpOnOutOfMemoryError。",
                        ref="references/iis-tomcat.md#tomcat调优",
                    )
                )
            if "file.encoding" not in cl and os.name == "nt":
                r.add(
                    Finding(
                        id="TOMCAT_NO_ENCODING",
                        title=f"Java 进程 {p.get('ProcessId')} 未指定 file.encoding",
                        severity="LOW",
                        category="config",
                        evidence=[cl[:220]],
                        impact="中文 Windows 下默认编码为 GBK，跨端交互会出现乱码。",
                        fix="加上 -Dfile.encoding=UTF-8 -Dsun.jnu.encoding=UTF-8",
                        ref="references/iis-tomcat.md#tomcat调优",
                    )
                )

    # 数据库
    dbs = ps_json(
        "Get-Service | Where-Object { $_.Name -match "
        "'MSSQL|MySQL|MariaDB|postgresql|Redis|MongoDB' } | "
        "Select-Object Name,DisplayName,Status,StartType", timeout=60
    ) or []
    r.fact("db_services", dbs)

    # Redis 未授权风险：6379 监听 + 无密码
    rl = [l for l in _listening_ports() if l.get("port") in (6379, 27017, 3306, 5432, 1433)]
    r.fact("db_listeners", rl)
    for l in rl:
        if l.get("bind_all"):
            r.add(
                Finding(
                    id="DB_BIND_ALL",
                    title=f"数据库端口 {l['port']} 监听在所有网卡（0.0.0.0）",
                    severity="HIGH" if l["port"] in (6379, 27017) else "MEDIUM",
                    category="sec",
                    evidence=[f"{l['port']}/{l['proto']} 监听 0.0.0.0，进程 {l.get('process')}"],
                    impact="数据库直接暴露在网络上，配合默认口令/无口令即为入侵通道"
                    "（Redis 未授权写 SSH key、MongoDB 无认证拖库是经典案例）。",
                    fix="改为 bind 127.0.0.1 或内网地址；强制密码与最小权限账号；"
                    "防火墙限制来源 IP。",
                    ref="references/hardening-windows.md#数据库",
                )
            )
    return r


# --------------------------------------------------------------------------
# 注册表 / 端口 底层工具
# --------------------------------------------------------------------------

def _reg_read(path: str, name: str, hive: str = "HKLM"):
    try:
        import winreg  # noqa: PLC0415

        root = getattr(winreg, f"HKEY_{hive}")
        with winreg.OpenKey(root, path) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except Exception:  # noqa: BLE001
        return None


def _reg_enum(hive: str, path: str) -> dict:
    out: dict = {}
    try:
        import winreg  # noqa: PLC0415

        root = getattr(winreg, f"HKEY_{hive}")
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                    out[name] = val
                    i += 1
                except OSError:
                    break
    except Exception:  # noqa: BLE001
        pass
    return out


def _reg_subkeys(hive: str, path: str) -> list[str]:
    out: list[str] = []
    try:
        import winreg  # noqa: PLC0415

        root = getattr(winreg, f"HKEY_{hive}")
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except Exception:  # noqa: BLE001
        pass
    return out


# 用户可写目录：木马落地的典型位置。注意 ProgramData 里也有大量正版软件，
# 所以它只是「候选」，必须再经数字签名核验才能定性（见 _signature_status）。
_WRITABLE_DIR = re.compile(
    r"(\\users\\[^\\]+\\appdata\\|\\users\\public\\|\\temp\\|\\tmp\\|"
    r"\\windows\\temp\\|\\perflogs\\|\\recycler|\\\$recycle|\\programdata\\)",
    re.I,
)
# 已知合法的 ProgramData 子树：直接放行，避免无谓的签名查询
_TRUSTED_SUBTREE = re.compile(
    r"\\(programdata|program files( \(x86\))?)\\(microsoft|package cache|windows defender|"
    r"nvidia|intel|amd|adobe|oracle|java|docker|google|mozilla|lenovo|dell|hp|"
    r"malwarebytes|kaspersky|eset|avast|avira|sophos|bitdefender|360)",
    re.I,
)

_SIG_OK = {"Valid"}


def _signature_status(paths: list[str], limit: int = 30) -> dict:
    """批量查 Authenticode 数字签名状态，返回 {path: 'Valid'|'NotSigned'|...}。

    这是判定「系统组件 vs 落地木马」的关键依据：正版软件有有效签名，
    木马几乎都没有。用一次 PowerShell 调用批量处理，避免 N 次进程启动。
    """
    uniq = []
    for p in paths:
        if p and p not in uniq:
            uniq.append(p)
    uniq = uniq[:limit]
    if not uniq:
        return {}
    items = ",".join("'" + p.replace("'", "''") + "'" for p in uniq)
    out = ps_json(
        f"@({items}) | ForEach-Object {{ "
        f"$s = Get-AuthenticodeSignature -FilePath $_ -ErrorAction SilentlyContinue; "
        f"[pscustomobject]@{{ P=$_; S=[string]$s.Status }} }}",
        timeout=120,
    ) or []
    res = {}
    for x in out:
        p = x.get("P")
        if p:
            res[p] = x.get("S")
    return res


def _is_trusted_signed(path: str, sigmap: dict) -> bool:
    """签名有效，或位于已知可信子树 → 视为正版，不作为可疑项上报。"""
    if not path:
        return False
    if _TRUSTED_SUBTREE.search(path):
        return True
    return sigmap.get(path) in _SIG_OK



_tcp_cache: list[dict] | None = None


def _listening_ports() -> list[dict]:
    """用 Python 解析 netstat -ano，避免解析 CIM 的性能开销。"""
    global _tcp_cache
    if _tcp_cache is not None:
        return _tcp_cache

    out: list[dict] = []
    rc, txt, _ = run(["netstat", "-ano", "-p", "TCP"], timeout=40)
    if rc != 0:
        rc, txt, _ = run(["netstat", "-ano"], timeout=40)

    pid_map = {}
    rc2, ptxt, _ = run(["tasklist", "/fo", "csv", "/nh"], timeout=40)
    if rc2 == 0:
        for line in ptxt.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                pid_map[parts[1].strip('"').strip()] = parts[0].strip('"')

    for line in (txt or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if "LISTENING" not in parts[3].upper() and "LISTEN" not in parts[3].upper():
            continue
        local = parts[1]
        pid = parts[-1]
        try:
            if local.startswith("["):  # IPv6
                addr, port = local.rsplit(":", 1)
                addr = addr.strip("[]")
            else:
                addr, port = local.rsplit(":", 1)
            port_i = int(port)
        except (ValueError, IndexError):
            continue
        out.append({
            "proto": "TCP",
            "addr": addr,
            "port": port_i,
            "pid": pid,
            "process": pid_map.get(pid, "?"),
            "bind_all": addr in ("0.0.0.0", "::", "[::]"),
        })
    out.sort(key=lambda x: x["port"])
    _tcp_cache = out
    return out


def _port_listening(port: int) -> bool:
    return any(l["port"] == port for l in _listening_ports())


def _public_ip_reachable() -> bool:
    """判断是否具备公网出口：探测默认网关之外的连通性（纯读操作，发一个 UDP 包）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return not (ip.startswith(("10.", "192.168."))
                    or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def collect_all(report: Report, scope: str = "all", quick: bool = False,
                log_hours: int = 48, deep: bool = False, progress=None) -> None:
    """progress: 可选回调 progress(label) —— 供图形控制台做实时进度，避免依赖 stdout。"""
    steps = [
        ("system", "系统清单", lambda: collect_system()),
        ("perf", "性能体检", lambda: collect_perf(quick)),
        ("sec", "安全基线", lambda: collect_security()),
        ("malware", "恶意代码与持久化", lambda: collect_malware(deep)),
        ("appstack", "应用栈探测", lambda: collect_appstack()),
        ("crash", "崩溃与转储", lambda: collect_crash()),
        ("log", "事件日志", lambda: collect_logs(log_hours)),
    ]
    want = {
        "all": {"system", "perf", "sec", "malware", "appstack", "crash", "log"},
        "perf": {"system", "perf", "appstack"},
        "sec": {"sec", "appstack"},
        "malware": {"malware", "sec"},
        "log": {"log", "crash"},
        "crash": {"crash", "log", "system"},
    }.get(scope, {"system", "perf", "sec", "malware", "appstack", "crash", "log"})

    for key, label, fn in steps:
        if key not in want:
            continue
        if progress:
            progress(label)
        else:
            print(C.gray(f"  · 采集中：{label} ..."), flush=True)
        try:
            report.add_result(fn())
        except Exception as e:  # noqa: BLE001
            cr = CheckResult(name=label, category="config")
            cr.fail(f"{type(e).__name__}: {e}")
            report.add_result(cr)
