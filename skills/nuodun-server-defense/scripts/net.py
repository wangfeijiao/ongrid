#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
net.py — 上网提速与网络诊断。

先说清楚这个模块**不做**什么，免得期待落空：

  * 它不"加大带宽"。带宽是运营商给的，软件改不了。
  * 它不做多线程下载加速，也不碰任何代理/翻墙类工具。

它做的是**把本机这一侧的拖累逐个消掉**。这些是真实存在、可测量、可验证的：

  ┌─ DNS 解析慢或被人劫持      → 换更快的解析器，最直接的提速
  ├─ TCP 拥塞控制用着保守算法  → Linux 上换 BBR，长肥管道吞吐能翻几倍
  ├─ TCP 参数保守（窗口/队列）  → 提高并发与吞吐上限
  ├─ 网卡节能 / offload 配置    → 笔记本上"省电模式"会让网速腰斩
  ├─ 后台进程偷跑流量           → 找出来，处理掉
  ├─ 代理残留 / hosts 被改      → 流量绕路或走错地址
  ├─ MTU 不对导致分片           → 大包被切碎，重传率上升
  └─ WiFi 信号差 / 协商速率低   → 无线场景下这比任何调优都重要

判定和变更严格分开：``net diag`` 只读；``net tune`` 只生成脚本；
执行要 ``--apply --yes``，且同时生成回滚脚本。

对外接口：cmd_net / add_net_parser
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    C,
    DARWIN,
    LINUX,
    OUTPUT_DIR,
    WINDOWS,
    ensure_output_dir,
    now_stamp,
    run,
    write_script,
)

SKILL_DIR = Path(__file__).resolve().parent.parent
NET_DIR = OUTPUT_DIR / "net"

# 诊断用的公共解析器。测它们只是为了知道"哪条路更快"，
# 不涉及任何数据外传 —— 只做一次普通的域名解析请求。
PUBLIC_DNS = [
    ("223.5.5.5", "阿里 AliDNS"),
    ("119.29.29.29", "腾讯 DNSPod"),
    ("114.114.114.114", "114DNS"),
    ("180.76.76.76", "百度 BaiduDNS"),
]

PING_TARGETS = [
    ("gateway", "网关"),
    ("ali-dns", "223.5.5.5"),
    ("baidu", "www.baidu.com"),
]


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class NetFacts:
    """网络事实。缺失即缺失，绝不用默认值顶替 —— 顶替出来的结论一定是错的。"""

    data: dict = field(default_factory=dict)
    error: str = ""

    def s(self, key: str, default: str = "") -> str:
        v = self.data.get(key)
        if v is None:
            return default
        if isinstance(v, list):
            return v[-1] if v else default
        return str(v)

    def lst(self, key: str) -> list:
        v = self.data.get(key)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    def i(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.s(key).strip()))
        except (TypeError, ValueError):
            return default

    def f(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.s(key).strip())
        except (TypeError, ValueError):
            return default

    def has(self, key: str) -> bool:
        return key in self.data

    def pairs(self, key: str, sep: str = "|") -> list[tuple]:
        """把 'a|b|c' 形式的列表展开成元组列表。"""
        out = []
        for line in self.lst(key):
            parts = str(line).split(sep)
            out.append(tuple(parts))
        return out


@dataclass
class NetFinding:
    id: str
    title: str
    level: str          # GOOD | INFO | WARN | BAD
    evidence: list = field(default_factory=list)
    impact: str = ""
    fix: str = ""
    ref: str = ""


LEVEL_ORDER = {"BAD": 0, "WARN": 1, "INFO": 2, "GOOD": 3}
LEVEL_LABEL = {"BAD": "严重", "WARN": "注意", "INFO": "提示", "GOOD": "正常"}
LEVEL_COLOR = {"BAD": "#b91c1c", "WARN": "#b45309", "INFO": "#64748b",
               "GOOD": "#0d7a6f"}


# --------------------------------------------------------------------------
# 采集：Windows（走 PowerShell 结构化对象，避开中文标签解析的坑）
# --------------------------------------------------------------------------

_PS_PS1 = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = [ordered]@{}

$out.hostname = $env:COMPUTERNAME

# 网卡
$out.adapters = @(Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | ForEach-Object {
  [ordered]@{
    name=$_.Name; desc=$_.InterfaceDescription; speed=$_.LinkSpeed;
    media="$($_.MediaType)"; mac=$_.MacAddress
  }
})

# 所有网卡（含未启用，用于发现异常）
$out.adapters_all = @(Get-NetAdapter | ForEach-Object {
  [ordered]@{ name=$_.Name; status="$($_.Status)"; speed=$_.LinkSpeed }
})

# IP 配置与 MTU
$out.ip = @(Get-NetIPInterface -AddressFamily IPv4 | ForEach-Object {
  [ordered]@{ alias=$_.InterfaceAlias; mtu=$_.NlMtu; dhcp="$($_.Dhcp)";
              state="$($_.ConnectionState)" }
})

# DNS
$out.dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 |
  Where-Object { $_.ServerAddresses.Count -gt 0 } | ForEach-Object {
    [ordered]@{ alias=$_.InterfaceAlias; servers=@($_.ServerAddresses) }
  })

# TCP 全局设置
$t = Get-NetTCPSetting -SettingName Internet
if ($t) {
  $out.tcp = [ordered]@{
    autotuning = "$($t.AutoTuningLevelLocal)";
    congestion = "$($t.CongestionProvider)";
    ecn = "$($t.EcnCapability)";
    timestamps = "$($t.Timestamps)";
    heuristics = "$($t.ScalingHeuristics)";
    rss = "$($t.ReceiveSideScaling)";
  }
}
$g = Get-NetOffloadGlobalSetting
if ($g) {
  $out.offload = [ordered]@{
    rss = "$($g.ReceiveSideScaling)";
    rsc = "$($g.ReceiveSegmentCoalescing)";
    task = "$($g.TaskOffload)";
    ipsec = "$($g.NetworkDirect)";
  }
}
$o = Get-NetTCPSetting -SettingName Internet
if ($o) { $out.tcp_rss = "$($o.ReceiveSideScaling)" }

# 网卡节能 / 电源管理相关的默认值
$out.powermgmt = @(Get-NetAdapterPowerManagement | ForEach-Object {
  [ordered]@{ name=$_.Name; allow=$_.AllowComputerToTurnOffDevice }
})

# 代理设置
$p = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
if ($p) {
  $out.proxy = [ordered]@{
    enable = $p.ProxyEnable; server = "$($p.ProxyServer)";
    pac = "$($p.AutoConfigURL)"; autodetect = $p.AutoDetect
  }
}

# 无线
try {
  $w = netsh wlan show interfaces
  $out.wlan = @($w | Where-Object { $_ -match '^\s*(SSID|Signal|信号|接收速率|发送速率|无线电类型|Radio type|信道|Channel|BSSID)' })
} catch { }

# 防火墙 profile 状态（不是网络提速项，但邻居问题常被误判成"网慢"）
try {
  $out.fw = @(Get-NetFirewallProfile | ForEach-Object {
    [ordered]@{ name=$_.Name; enabled=$_.Enabled }
  })
} catch { }

$out | ConvertTo-Json -Depth 6 -Compress
"""


def _ps_object(script: str, timeout: int = 120) -> dict:
    """执行 PowerShell 脚本并把输出解析成 dict。

    这里刻意**不用** common.ps_json —— 它会在脚本末尾再追加一次
    `| ConvertTo-Json`，而我们的脚本自己已经转换过了，双重转换会把结果变成
    「一个装着 JSON 字符串的 JSON 字符串」，解析出来是 str 而不是 dict，
    然后被静默当成「采集失败」。这类失败最坑：看起来像环境问题，其实是接口用错了。
    """
    from common import ps

    _, out, _ = ps(script, timeout=timeout)
    blob = (out or "").strip()
    if not blob:
        return {}
    # PowerShell 可能混入警告行或 BOM，取最外层的 {...}
    i, j = blob.find("{"), blob.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        obj = json.loads(blob[i:j + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _collect_windows() -> NetFacts:
    f = NetFacts()
    obj = _ps_object(_PS_PS1)
    if obj:
        for k, v in obj.items():
            f.data[f"win.{k}"] = v
    else:
        f.error = "PowerShell 未返回数据，Windows 侧网络事实采集失败"

    # 延迟 / 丢包 —— 用 ping.exe，中英文字段都用正则兜住
    gw = _win_gateway()
    targets = [("gateway", gw), ("ali-dns", "223.5.5.5"),
               ("baidu", "www.baidu.com")]
    rows = []
    for label, host in targets:
        if not host:
            continue
        rows.append(_ping_windows(label, host))
    f.data["net.ping"] = [r for r in rows if r]

    # DNS 解析耗时
    f.data["net.dns_time"] = _dns_time_windows()

    # 直连吞吐（下载一小段公开资源，仅测速，不上传任何东西）
    f.data["net.http_ms"] = _http_probe_windows()
    return f


def _win_gateway() -> str:
    rows = run(["route", "print", "0.0.0.0"], timeout=15)[1]
    for line in rows.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] == "0.0.0.0" and p[1] == "0.0.0.0":
            return p[2]
    return ""


def _parse_ping(text: str) -> dict:
    """解析 ping 输出，返回 {loss, min_ms, avg_ms, max_ms}。

    这里有**两个**坑，都踩过：

    坑一：只取第一个毫秒数字。Windows 汇总行是「最短 = Xms，最长 = Yms，平均 = Zms」，
    第一个数字是最小值不是平均值，当成平均值上报就会系统性低估延迟。

    坑二（更隐蔽）：逐包回复行里也有 `时间=1ms` / `time=1ms`，它会混进数字序列，
    把「最小/最大/平均」的位置全挤错一位。所以必须先把逐包行剔掉，
    再按标签取「平均」，而不是靠位置猜。

    实现顺序：先按标签配对取（中英文都覆盖）→ 再试 Linux 一行式 → 最后兜底。
    """
    import re

    out: dict = {}
    if not text:
        return out

    m = re.search(r"(\d+)%", text)
    if m:
        out["loss"] = int(m.group(1))

    # Linux/BSD 一行式：rtt min/avg/max/mdev = 1.2/3.4/5.6/0.7 ms
    m = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?\s*ms", text)
    if m:
        out["min_ms"] = float(m.group(1))
        out["avg_ms"] = float(m.group(2))
        out["max_ms"] = float(m.group(3))
        if m.group(4):
            out["jitter_ms"] = float(m.group(4))
        return out

    # 按标签配对取（中文 / 英文），这才是可靠的定位方式
    def grab(*labels: str) -> float | None:
        for lab in labels:
            mm = re.search(rf"{re.escape(lab)}\s*[=＝]\s*([\d.]+)\s*ms", text, re.I)
            if mm:
                return float(mm.group(1))
        return None

    avg = grab("平均", "Average")
    mn = grab("最短", "Minimum")
    mx = grab("最长", "Maximum")
    if avg is not None:
        out["avg_ms"] = avg
        if mn is not None:
            out["min_ms"] = mn
        if mx is not None:
            out["max_ms"] = mx
        return out

    # 兜底：剔除逐包回复行后再取最后一个数字（汇总行在最后）
    body = "\n".join(
        ln for ln in text.splitlines()
        if not re.search(r"(时间|time)\s*[<=＝]", ln, re.I)
    )
    vals = [float(x) for x in re.findall(r"[=＝]\s*(\d+(?:\.\d+)?)\s*ms", body)]
    if vals:
        # 只能确定有往返时间，无法区分极值 —— 就只说平均，不假装知道 min/max
        out["avg_ms"] = vals[-1]
    return out


def _fmt(v, template: str = "{}") -> str:
    return template.format(v) if v is not None else "?"


def _ping_row(label: str, host: str, pr: dict) -> str:
    """统一的行格式：label|host|loss|min|avg|max（缺什么写 ?，绝不填 0）"""
    return "|".join([
        label, host,
        str(pr["loss"]) if pr.get("loss") is not None else "?",
        _fmt(pr.get("min_ms")), _fmt(pr.get("avg_ms")), _fmt(pr.get("max_ms")),
    ])


def _ping_windows(label: str, host: str) -> str:
    _, out, err = run(["ping", "-n", "6", "-w", "1500", host], timeout=30)
    pr = _parse_ping(out or err)
    if not pr:
        return f"{label}|{host}|?|?|?|?"
    return _ping_row(label, host, pr)


def _dns_time_windows() -> list:
    rows = []
    for server, _name in PUBLIC_DNS:
        rc, out, _ = run(["nslookup", "www.baidu.com", server], timeout=15)
        blob = (out or "") + "\n" + (_ or "")
        ok = rc == 0 and (
            ("baidu.com" in blob.lower() and "Address" in blob)
            or "baidu" in blob.lower()
        )
        rows.append(f"{server}|{'ok' if ok else 'fail'}")
    return rows


def _http_probe_windows() -> list:
    """HTTP 连通与首字节耗时（只发一个 HEAD/GET，不上传）"""
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$res=@()
foreach ($u in @('http://www.baidu.com','http://www.qq.com')) {
  try {
    $sw=[System.Diagnostics.Stopwatch]::StartNew()
    $r=Invoke-WebRequest -Uri $u -Method Head -TimeoutSec 8 -UseBasicParsing
    $sw.Stop()
    $res += "$u|ok|$($sw.ElapsedMilliseconds)"
  } catch {
    $res += "$u|fail|0"
  }
}
$res -join "`n"
"""
    _, out, _ = run(["powershell", "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-Command", script], timeout=40)
    return [ln.strip() for ln in (out or "").splitlines() if "|" in ln]


# --------------------------------------------------------------------------
# 采集：Linux / UNIX（复用只读探针 + SSH 管道）
# --------------------------------------------------------------------------

def _collect_unix(target=None) -> NetFacts:
    from linux_facts import NET_AGENT_PATH, Target, parse, run_agent

    f = NetFacts()
    t = target or Target(kind="local")
    ok, out, err = run_agent(t, timeout=180, script_path=NET_AGENT_PATH)
    if not ok and not out:
        f.error = err or "网络探针执行失败"
        return f
    facts = parse(out)
    if facts.s("agent.done") != "1":
        f.error = (err or "").strip()[:400] or "网络探针未正常结束，结果可能不完整"
    f.data["net.local"] = facts.data
    f.data["_facts_obj"] = facts
    return f


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------

def _unix_facts(f: NetFacts):
    return f.data.get("_facts_obj")


def analyze(f: NetFacts) -> list[NetFinding]:
    out: list[NetFinding] = []
    if WINDOWS or f.has("win.hostname"):
        out += _analyze_windows(f)
    if f.has("net.local") or _unix_facts(f) is not None:
        out += _analyze_unix(f)
    return out


def _analyze_ping(rows: list) -> list[NetFinding]:
    """链路实测分析。行格式：label|host|loss|min|avg|max

    Windows 与 Linux 共用 —— 之前只在 Linux 分支里做了，导致 Windows 上
    采到了 ping 数据却从不判定，报告里静默少了一整块结论。
    """
    out: list[NetFinding] = []
    for row in rows:
        parts = str(row).split("|")
        if len(parts) < 6:
            continue
        label, host, loss, pmin, avg, pmax = parts[:6]
        label_cn = dict(PING_TARGETS).get(label, label)
        try:
            lv: int | None = int(loss)
        except ValueError:
            lv = None
        try:
            av: float | None = float(avg)
        except ValueError:
            av = None
        try:
            jit: float | None = float(pmax) - float(pmin)
        except ValueError:
            jit = None

        if lv is None or av is None:
            out.append(NetFinding(
                id=f"PING_UNPARSED_{label.upper()}",
                title=f"到「{label_cn}」的延迟未能解析",
                level="INFO",
                evidence=[f"原始行：{str(row)[:120]}"],
                fix="说明目标的 ping 输出格式非常规（如 BusyBox）。"
                    "这一项**未判定**，不代表网络正常。",
            ))
            continue

        rng = f"最小 {pmin} / 平均 {av} / 最大 {pmax} ms"
        if lv >= 20:
            out.append(NetFinding(
                id=f"PING_LOSS_{label.upper()}", title=f"到「{label_cn}」丢包 {lv}%",
                level="BAD",
                evidence=[f"{host}：丢包 {lv}%，{rng}"],
                impact="丢包会触发重传与拥塞窗口回退，比高延迟更严重地拖慢实际速度。"
                       "20% 丢包意味着实际吞吐可能只剩正常的三成。",
                fix="先查本机到网关这一段（无线信号、网线、交换机口），再看运营商侧。",
            ))
        elif lv > 0:
            out.append(NetFinding(
                id=f"PING_LOSS_{label.upper()}", title=f"到「{label_cn}」有丢包（{lv}%）",
                level="WARN", evidence=[f"{host}：丢包 {lv}%，{rng}"],
                fix="低比例丢包也可能是链路拥塞，建议换个时段多点采样确认。",
            ))

        if av > 200:
            out.append(NetFinding(
                id=f"PING_LAT_{label.upper()}", title=f"到「{label_cn}」延迟偏高（{av}ms）",
                level="WARN", evidence=[f"{host}：平均 {av}ms（最大 {pmax}ms）"],
                impact="高延迟下即使带宽充足，单个连接的吞吐也会受带宽时延积限制。",
                fix="启用 BBR / 增大窗口；或用多连接并行。",
            ))

        # 抖动：最大最小差得远，说明链路不稳定 —— 体验上比"一直慢"更糟
        if jit is not None and av and jit > max(30.0, av):
            out.append(NetFinding(
                id=f"PING_JITTER_{label.upper()}",
                title=f"到「{label_cn}」延迟抖动大（{jit:.0f}ms）",
                level="WARN",
                evidence=[f"{host}：最小 {pmin}ms / 最大 {pmax}ms，"
                          f"平均 {avg}ms，抖动 {jit:.0f}ms"],
                impact="时快时慢通常是链路拥塞或无线干扰。"
                       "表现为「有时秒开有时转圈」，而平均延迟看起来很漂亮。",
                fix="有线替代无线；检查是否有设备在跑大流量；确认交换机口没有跑满。",
            ))
    return out


# ---------------------- Windows ----------------------

def _analyze_windows(f: NetFacts) -> list[NetFinding]:
    out: list[NetFinding] = []

    # 1) DNS：配了什么
    dns_rows = f.data.get("win.dns") or []
    servers: list[str] = []
    if isinstance(dns_rows, list):
        for r in dns_rows:
            if isinstance(r, dict):
                for s in (r.get("servers") or []):
                    if s and s not in servers:
                        servers.append(str(s))
    if servers:
        pub = {ip for ip, _ in PUBLIC_DNS}
        slow_or_unknown = [s for s in servers if s not in pub]
        out.append(NetFinding(
            id="DNS_CONFIGURED", title="当前使用的 DNS 服务器",
            level="INFO",
            evidence=[f"{len(servers)} 个：{', '.join(servers[:6])}"],
            fix="若解析慢或结果异常，可参考下方推荐值更换。",
            ref="references/net-speedup.md#dns",
        ))
        for s in slow_or_unknown[:4]:
            if s.startswith(("10.", "192.168.", "172.", "127.")):
                out.append(NetFinding(
                    id="DNS_INTERNAL", title=f"DNS 指向内网地址 {s}",
                    level="INFO",
                    evidence=[f"接口 DNS 列表含 {s}"],
                    impact="若该地址已失效（换过网络/VPN 卸载后残留），"
                           "所有解析都要先超时才回落，表现为「上网很慢但能通」。",
                    fix="确认该 DNS 是否仍有效；失效则改回自动获取或公共 DNS。",
                ))

    dns_time = f.data.get("net.dns_time") or []
    ok_servers = [r for r in dns_time if str(r).endswith("|ok")]
    fail_servers = [r for r in dns_time if str(r).endswith("|fail")]
    if dns_time:
        if len(ok_servers) == 0:
            out.append(NetFinding(
                id="DNS_ALL_UNREACHABLE", title="所有公共 DNS 均不可达",
                level="BAD",
                evidence=[f"测试了 {len(dns_time)} 个解析器，全部失败"],
                impact="说明出口网络本身有问题（不是 DNS 配置问题），"
                       "或全部 DNS 查询被拦截。",
                fix="先排查网络连通性（网关、拨号、路由），再谈解析优化。",
            ))
        elif fail_servers:
            out.append(NetFinding(
                id="DNS_PARTIAL", title="部分公共解析器不可达",
                level="INFO",
                evidence=[f"不可达：{', '.join(str(r).split('|')[0] for r in fail_servers)}"],
                fix="把不可达的从候选里去掉即可，不影响其他优化。",
            ))

    # 2) TCP 参数
    tcp = f.data.get("win.tcp") or {}
    if isinstance(tcp, dict) and tcp:
        at = str(tcp.get("autotuning", ""))
        if at and at.lower() not in ("normal", "restricted"):
            out.append(NetFinding(
                id="TCP_AUTOTUNING", title=f"TCP 接收窗口自动调优为 {at}",
                level="WARN",
                evidence=[f"AutoTuningLevel = {at}"],
                impact="关闭或设为受限会显著压低高延迟链路的下载速度 —— "
                       "这是「同一条网线，别人快我慢」的经典原因。",
                fix="netsh int tcp set global autotuninglevel=normal",
                ref="references/net-speedup.md#tcp",
            ))
        cong = str(tcp.get("congestion", ""))
        if cong and cong.lower() not in ("cubic", "ctcp", "default"):
            out.append(NetFinding(
                id="TCP_CONGESTION", title=f"拥塞控制算法为 {cong}",
                level="INFO",
                evidence=[f"CongestionProvider = {cong}"],
                fix="若内核支持，CUBIC 在多数场景优于默认（Windows 10+ 通常已是 CUBIC）。",
            ))
        if str(tcp.get("heuristics", "")).lower() in ("disabled", "disable"):
            out.append(NetFinding(
                id="TCP_HEURISTICS_OFF", title="TCP 窗口缩放启发式为关闭",
                level="INFO",
                evidence=["ScalingHeuristics = Disabled"],
                fix="一般保持默认即可；只有明确诊断出问题时才调整。",
            ))
        rss = str(tcp.get("rss", "") or (f.data.get("win.offload") or {}).get("rss", ""))
        if rss and rss.lower() in ("disabled", "disable"):
            out.append(NetFinding(
                id="RSS_OFF", title="网卡多队列接收（RSS）被关闭",
                level="WARN",
                evidence=[f"ReceiveSideScaling = {rss}"],
                impact="多核机器上收包会挤在单核，千兆以上链路吞吐上不去、延迟抖动变大。",
                fix="Enable-NetAdapterRss -Name '<网卡名>'",
            ))

    # 3) 网卡速率与节能
    adapters = f.data.get("win.adapters") or []
    if isinstance(adapters, list):
        for a in adapters:
            if not isinstance(a, dict):
                continue
            name = a.get("name", "?")
            sp = str(a.get("speed", ""))
            low = False
            try:
                import re as _re
                m = _re.match(r"([\d.]+)\s*(Gbps|Mbps|Kbps)", sp)
                if m:
                    v = float(m.group(1))
                    unit = m.group(2)
                    mbps = v * 1000 if unit == "Gbps" else (v / 1000 if unit == "Kbps" else v)
                    if mbps < 100:
                        low = True
            except Exception:  # noqa: BLE001
                pass
            if low:
                out.append(NetFinding(
                    id="NIC_LOW_SPEED", title=f"网卡「{name}」协商速率偏低（{sp}）",
                    level="WARN",
                    evidence=[f"{name}：{sp}（{a.get('desc','')}）"],
                    impact="在千兆网络里跑到 100Mbps，通常是网线老化、水晶头接触不良，"
                           "或对端口协商失败。下载速度天花板就在这。",
                    fix="换一根网线/换个端口再测；确认后可用 "
                        "`Set-NetAdapterAdvancedProperty -Name '<名字>' "
                        "-DisplayName 'Speed & Duplex' -DisplayValue 'Auto Negotiation'`。",
                    ref="references/net-speedup.md#nic",
                ))
    pw = f.data.get("win.powermgmt") or []
    # AllowComputerToTurnOffDevice 是枚举：0=Unsupported 1=Disabled 2=Enabled。
    # PowerShell 序列化成 JSON 后是数字，所以这里要把 2 也认成"已启用"。
    _on = ("true", "1", "2", "enabled")
    if isinstance(pw, list) and any(
            isinstance(x, dict) and str(x.get("allow", "")).strip().lower() in _on
            for x in pw):
        names = [str(x.get("name")) for x in pw
                 if isinstance(x, dict)
                 and str(x.get("allow", "")).strip().lower() in _on]
        out.append(NetFinding(
            id="NIC_POWER_SAVE", title="网卡允许被系统关闭以省电",
            level="INFO",
            evidence=[f"涉事网卡：{', '.join(names[:5])}",
                      "AllowComputerToTurnOffDevice = Enabled"],
            impact="笔记本上会表现为「闲置一会儿后第一次打开网页特别慢」，"
                   "以及待机唤醒后短暂断流。",
            fix="设备管理器 → 网卡 → 电源管理 → 取消勾选"
                "「允许计算机关闭此设备以节约电源」。",
        ))

    # 4) 代理残留
    proxy = f.data.get("win.proxy") or {}
    if isinstance(proxy, dict):
        en = proxy.get("enable")
        pac = str(proxy.get("pac") or "")
        srv = str(proxy.get("server") or "")
        if str(en) in ("1", "True", "true") and (srv or pac):
            out.append(NetFinding(
                id="PROXY_ENABLED", title="系统代理处于启用状态",
                level="WARN",
                evidence=[f"ProxyServer={srv or '-'}", f"PAC={pac or '-'}"],
                impact="如果代理已经不可用（卸载了软件但没关开关），"
                       "每次上网都要先等代理超时才回落 —— 表现就是「网页打开很慢但最终能开」。",
                fix="确认不需要就关掉：设置 → 网络和 Internet → 代理 → 全部关闭。",
                ref="references/net-speedup.md#proxy",
            ))
    # 链路实测（Windows 上同样要判，不能只采不判）
    out += _analyze_ping(f.data.get("net.ping") or [])
    return out


# ---------------------- Linux / UNIX ----------------------

def _analyze_unix(f: NetFacts) -> list[NetFinding]:
    facts = _unix_facts(f)
    if facts is None:
        return []
    out: list[NetFinding] = []

    def sysctl(key: str) -> str:
        for row in facts.lst("net.sysctl"):
            k, _, v = str(row).partition("|")
            if k == key:
                return v.strip()
        return ""

    # 1) 拥塞控制 —— 这是 Linux 上收益最大的一项
    cc = sysctl("net.ipv4.tcp_congestion_control")
    avail = facts.s("net.cc_available") or sysctl("net.ipv4.tcp_available_congestion_control")
    if cc:
        if "bbr" in cc.lower():
            out.append(NetFinding(
                id="BBR_ON", title="TCP 拥塞控制已启用 BBR",
                level="GOOD", evidence=[f"tcp_congestion_control = {cc}"],
            ))
        elif "bbr" in avail.lower():
            out.append(NetFinding(
                id="BBR_AVAILABLE_OFF", title="内核支持 BBR，但当前未启用",
                level="WARN",
                evidence=[f"当前 = {cc}", f"内核可用 = {avail}"],
                impact="在高延迟或轻微丢包的链路上（跨境、移动网络、云间互联），"
                       "BBR 相比 CUBIC 的吞吐提升经常是数倍；丢包率越高差距越大。",
                fix="net.ipv4.tcp_congestion_control=bbr（低版本内核需先 modprobe tcp_bbr）",
                ref="references/net-speedup.md#bbr",
            ))
        else:
            out.append(NetFinding(
                id="BBR_UNAVAILABLE", title="内核未提供 BBR",
                level="INFO",
                evidence=[f"当前 = {cc}", f"可用 = {avail or '未知'}"],
                fix="内核 4.9+ 才有 BBR。升级内核后再考虑，不必强求。",
            ))

    qdisc = " ".join(facts.lst("net.qdisc"))
    # BBR 依赖 pacing。fq / fq_codel / cake / sfq 都提供 pacing，
    # 只有 pfifo_fast 之类无 pacing 的队列才会让 BBR 打折。
    if cc and "bbr" in cc.lower() and qdisc and not any(
            q in qdisc.lower() for q in ("fq", "fq_codel", "cake", "sfq")):
        out.append(NetFinding(
            id="QDISC_NOT_FQ", title="启用 BBR 但队列规则不支持 pacing",
            level="INFO",
            evidence=list(facts.lst("net.qdisc"))[:3],
            impact="BBR 在有 pacing 的队列（fq / fq_codel / cake）上才发挥完整效果。",
            fix="net.core.default_qdisc=fq",
        ))

    # 2) TCP 参数
    checks = [
        ("net.ipv4.tcp_slow_start_after_idle", "1",
         "TCP_IDLE_SLOWSTART", "空闲后慢启动仍开启",
         "连接空闲一段时间后，拥塞窗口会被重置，长连接会周期性地重新慢启动。"
         "对保持长连接的服务（数据库、消息队列、HTTP/2）影响明显。",
         "net.ipv4.tcp_slow_start_after_idle=0"),
        ("net.ipv4.tcp_moderate_rcvbuf", "0",
         "TCP_RCVBUF_NO_MODERATE", "接收缓冲区不再自动调整",
         "接收窗口无法随链路变化自动扩缩，高带宽链路吃不饱。",
         "net.ipv4.tcp_moderate_rcvbuf=1"),
        ("net.ipv4.tcp_window_scaling", "0",
         "TCP_NOWINDOWSCALE", "TCP 窗口缩放被关闭",
         "窗口上限锁在 64KB，高延迟链路上吞吐直接被锁死。",
         "net.ipv4.tcp_window_scaling=1"),
        ("net.ipv4.tcp_sack", "0",
         "TCP_NOSACK", "SACK 被关闭",
         "丢包后只能重传整段，重传效率低、恢复慢。",
         "net.ipv4.tcp_sack=1"),
        ("net.ipv4.tcp_mtu_probing", "0",
         "TCP_MTU_PROBING_OFF", "MTU 探测未开启",
         "某些链路（PPPoE、VPN）实际 MTU 小于 1500，大包被丢弃后连接会卡住。",
         "net.ipv4.tcp_mtu_probing=1"),
    ]
    for key, bad_value, fid, title, impact, fix in checks:
        v = sysctl(key)
        if v and v == bad_value:
            out.append(NetFinding(
                id=fid, title=f"{title}（{key}={v}）", level="WARN",
                evidence=[f"{key} = {v}"], impact=impact, fix=fix,
                ref="references/net-speedup.md#tcp",
            ))

    backlog = sysctl("net.core.netdev_max_backlog")
    if backlog and backlog.isdigit() and int(backlog) < 1000:
        out.append(NetFinding(
            id="NETDEV_BACKLOG_LOW", title=f"网卡收包队列偏小（{backlog}）",
            level="WARN",
            evidence=[f"net.core.netdev_max_backlog = {backlog}"],
            impact="高 PPS 场景下会在内核侧直接丢包，表现为「带宽没跑满但就是慢」。",
            fix="net.core.netdev_max_backlog=16384（按业务调）",
        ))
    rmem_max = sysctl("net.core.rmem_max")
    if rmem_max and rmem_max.isdigit() and int(rmem_max) < 4 * 1024 * 1024:
        out.append(NetFinding(
            id="RMEM_MAX_LOW", title=f"接收缓冲区上限偏小（{int(rmem_max) // 1024}KB）",
            level="INFO",
            evidence=[f"net.core.rmem_max = {rmem_max}"],
            impact="每秒能承载的数据量有上限，高带宽高延迟链路尤其明显。",
            fix="net.core.rmem_max=16777216 / net.core.wmem_max=16777216",
        ))

    # 3) 重传 / 超时（真实链路质量的硬证据）
    retrans = facts.s("net.tcp_retrans")
    if retrans and retrans.isdigit():
        out.append(NetFinding(
            id="TCP_RETRANS_COUNT", title="TCP 重传累计计数",
            level="INFO",
            evidence=[f"RetransSegs = {retrans}（自开机累计，需结合运行时长看）"],
            fix="关注增量：间隔几分钟两次采样差值大，说明链路在丢包。",
        ))

    # 4) DNS
    dns = facts.lst("net.dns")
    if dns:
        out.append(NetFinding(
            id="DNS_CONFIGURED", title="当前使用的 DNS 服务器",
            level="INFO", evidence=[", ".join(dns[:6])],
        ))
        dead = [d for d in dns if d.startswith(("10.", "192.168.", "172."))
                or d in ("127.0.0.1", "127.0.0.53")]
        if dead:
            note = ("127.0.0.53 是 systemd-resolved 的本地存根，正常。"
                    if "127.0.0.53" in dead else "")
            out.append(NetFinding(
                id="DNS_LOCAL_STUB", title=f"DNS 指向本地/内网地址：{', '.join(dead)}",
                level="INFO",
                evidence=[f"/etc/resolv.conf 中的 nameserver：{', '.join(dead)}"],
                impact=("若非 systemd-resolved，说明解析依赖某台内网 DNS；"
                        "它一慢，所有上网都慢。 " + note),
                fix="用 `net diag --dns-bench` 对比公共解析器耗时后决定是否更换。",
            ))
    dns_time = facts.lst("net.dns_time")
    if dns_time:
        usable = [r for r in dns_time if "timeout" not in str(r)]
        out.append(NetFinding(
            id="DNS_BENCH", title="公共解析器可用性实测",
            level="GOOD" if len(usable) >= 3 else "WARN",
            evidence=[f"{str(r).split('|')[0]}："
                      f"{'可用' if 'timeout' not in str(r) else '不可达'}"
                      for r in dns_time[:5]],
            fix="从可用列表里选延迟最低的作为首选 DNS。",
        ))

    # 5) 链路实测（与 Windows 共用同一套判定）
    out += _analyze_ping(facts.lst("net.ping"))

    # 6) 网卡速率与错误
    for row in facts.lst("net.if_speed"):
        p = str(row).split("|")
        if len(p) >= 2 and p[1].isdigit() and int(p[1]) < 100:
            out.append(NetFinding(
                id="NIC_LOW_SPEED", title=f"网卡 {p[0]} 协商速率 {p[1]}Mb/s",
                level="WARN",
                evidence=[f"{p[0]}：{p[1]}Mb/s"],
                impact="千兆网络里只跑到百兆，瓶颈在物理层，任何系统调优都无效。",
                fix="换网线/换交换机端口，确认对端也是千兆。",
                ref="references/net-speedup.md#nic",
            ))
    for row in facts.lst("net.if_eee"):
        p = str(row).split("|")
        if len(p) >= 2 and p[1].lower().startswith("enabled"):
            out.append(NetFinding(
                id="NIC_EEE_ON", title=f"网卡 {p[0]} 开启了节能以太网（EEE）",
                level="INFO", evidence=[f"{p[0]}：EEE status: {p[1]}"],
                impact="部分交换机与 EEE 兼容性不好，会出现间歇性丢包或降速。",
                fix=f"ethtool --set-eee {p[0]} eee off（先用 --show-eee 确认）",
            ))
    for row in facts.lst("net.if_err"):
        p = str(row).split("|")
        name = p[0]
        nums = {}
        for seg in p[1:]:
            k, _, v = seg.partition("=")
            if v.isdigit():
                nums[k] = int(v)
        if nums.get("rx_err", 0) or nums.get("tx_err", 0):
            out.append(NetFinding(
                id="NIC_ERRORS", title=f"网卡 {name} 出现收发错误",
                level="WARN",
                evidence=[f"{name}：{', '.join(f'{k}={v}' for k, v in nums.items())}"],
                impact="物理层错误（网线、光衰、双工不匹配）会直接表现为丢包和降速。",
                fix="先换线换口排除物理问题；确认双工模式是否两边一致。",
            ))
        if nums.get("rx_drop", 0) or nums.get("tx_drop", 0):
            out.append(NetFinding(
                id="NIC_DROPS", title=f"网卡 {name} 出现丢包",
                level="INFO",
                evidence=[f"{name}：{', '.join(f'{k}={v}' for k, v in nums.items())}"],
                fix="环形缓冲区不足或中断处理跟不上；可调整 ring buffer（ethtool -G）。",
            ))

    # 7) conntrack 表满 = 新连接失败，表现是"时好时坏"
    ct = facts.s("net.conntrack")
    if "/" in ct:
        cur, _, mx = ct.partition("/")
        if cur.strip().isdigit() and mx.strip().isdigit() and int(mx.strip()) > 0:
            pctv = int(cur.strip()) * 100.0 / int(mx.strip())
            if pctv >= 80:
                out.append(NetFinding(
                    id="CONNTRACK_FULL", title=f"连接跟踪表占用 {pctv:.0f}%",
                    level="WARN",
                    evidence=[f"nf_conntrack：{cur}/{mx}"],
                    impact="表满后新连接会被直接丢弃，表现为间歇性无法访问，"
                           "而 ping 还是通的 —— 很容易被误判成「网络抖动」。",
                    fix="net.netfilter.nf_conntrack_max 调大，并检查 tcp_fin_timeout。",
                ))
            elif pctv >= 40:
                out.append(NetFinding(
                    id="CONNTRACK_HOT", title=f"连接跟踪表占用 {pctv:.0f}%",
                    level="INFO", evidence=[f"nf_conntrack：{cur}/{mx}"],
                    fix="保持关注；上升趋势意味着有连接泄漏或短连接风暴。",
                ))

    # 8) 代理残留
    pe = facts.lst("net.proxy_env")
    if pe:
        out.append(NetFinding(
            id="PROXY_ENV", title="环境变量里存在代理设置",
            level="INFO", evidence=pe[:5],
            impact="CLI 工具的流量会绕到代理；代理失效则表现成「命令卡住」。",
            fix="确认仍需要就保留，否则从 shell 配置与 /etc/environment 里清掉。",
            ref="references/net-speedup.md#proxy",
        ))

    # 9) hosts 被改（DNS 劫持线索）
    hosts_lines = facts.lst("net.hosts")
    suspicious = [h for h in hosts_lines
                  if len(str(h).split()) >= 2
                  and not str(h).strip().startswith("#")
                  and any(k in str(h).lower() for k in (
                      "baidu", "taobao", "qq.com", "weixin", "360", "google",
                      "microsoft", "apple", "java", "update"))]
    if suspicious:
        out.append(NetFinding(
            id="HOSTS_TAMPERED", title="hosts 中存在可能被篡改的域名绑定",
            level="WARN",
            evidence=[str(x)[:120] for x in suspicious[:8]],
            impact="把常用域名指到内网或第三方 IP，是流量劫持的典型手法，"
                   "也常用于阻断更新。",
            fix="逐条核对；确认无关就删除，并在 hosts 上设只读。",
            ref="references/net-speedup.md#hosts",
        ))

    # 10) 已建立连接的对端端口 Top —— 找出谁在长期占用带宽
    ports = []
    for row in facts.lst("net.estab_port"):
        p = str(row).split("|")
        if len(p) == 2 and p[1].isdigit():
            ports.append((p[0], int(p[1])))
    if ports:
        top = sorted(ports, key=lambda x: -x[1])[:5]
        out.append(NetFinding(
            id="TOP_ESTAB_PORTS", title="已建立连接最集中的对端端口",
            level="INFO",
            evidence=[f"端口 {p}：{n} 条连接" for p, n in top],
            fix="端口数量异常集中时，排查对应进程是否在无节制重连（会吃掉带宽与端口）。",
        ))

    # 11) WiFi
    for row in facts.lst("net.wifi"):
        s = str(row)
        low = "signal: -" in s and any(f"signal: -{n}" in s for n in range(75, 100))
        if low:
            out.append(NetFinding(
                id="WIFI_WEAK", title="无线信号强度偏弱",
                level="WARN", evidence=[s[:140]],
                impact="信号弱时协商速率会大幅下降并频繁重传 —— "
                       "此时讨论任何 TCP 参数调优都没有意义。",
                fix="靠近 AP、换 5GHz 频段、或用网线。物理层解决比软件解决快得多。",
                ref="references/net-speedup.md#wifi",
            ))
    return out


# --------------------------------------------------------------------------
# 调优动作（生成脚本，默认不执行）
# --------------------------------------------------------------------------

@dataclass
class Action:
    id: str
    title: str
    why: str
    risk: str            # low | medium | high
    apply_win: list = field(default_factory=list)
    roll_win: list = field(default_factory=list)
    apply_nix: list = field(default_factory=list)
    roll_nix: list = field(default_factory=list)
    verify: str = ""
    verify_nix: str = ""   # 同一动作在两个平台验证方式不同时用这个（否则 Linux 脚本会去跑 PowerShell 命令）
    note: str = ""


ACTIONS: list[Action] = [
    Action(
        id="DNS_SWITCH",
        title="把 DNS 换成实测最快的公共解析器",
        why="解析是每次上网的第一步。路由器自带的 DNS 常因转发链路长而变慢，"
            "换成本地就近的公共解析器，对「打开网页第一下卡顿」改善最直接。",
        risk="medium",
        apply_win=[
            "# 对所有已启用网卡设置 DNS；回滚脚本会恢复为自动获取",
            "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | ForEach-Object {",
            "  Set-DnsClientServerAddress -InterfaceAlias $_.Name "
            "-ServerAddresses @('223.5.5.5','119.29.29.29') -ErrorAction SilentlyContinue",
            "  Write-Host ('    已设置：' + $_.Name)",
            "}",
            "ipconfig /flushdns | Out-Null",
        ],
        roll_win=[
            "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | ForEach-Object {",
            "  Set-DnsClientServerAddress -InterfaceAlias $_.Name "
            "-ResetServerAddresses -ErrorAction SilentlyContinue",
            "  Write-Host ('    已恢复自动获取：' + $_.Name)",
            "}",
            "ipconfig /flushdns | Out-Null",
        ],
        apply_nix=[
            "# 备份原 resolv.conf 后指向公共解析器",
            "cp -n /etc/resolv.conf /etc/resolv.conf.ops-backup 2>/dev/null || true",
            "cat > /etc/resolv.conf <<'EOF'",
            "# 由 服务器安全维护、杀木马病毒skill 设置",
            "nameserver 223.5.5.5",
            "nameserver 119.29.29.29",
            "EOF",
        ],
        roll_nix=[
            "if [ -f /etc/resolv.conf.ops-backup ]; then",
            "  cp /etc/resolv.conf.ops-backup /etc/resolv.conf",
            "  echo '已从 /etc/resolv.conf.ops-backup 恢复'",
            "else",
            "  echo '未找到备份，请手工恢复 DNS 配置'",
            "fi",
        ],
        verify="Get-DnsClientServerAddress -AddressFamily IPv4",
        verify_nix="cat /etc/resolv.conf 2>/dev/null | grep -v '^#' | head -8",
        note="企业内网 / 域环境**不要执行这一项** —— 内网域名与内部服务"
             "只有内网 DNS 能解析，换成公共 DNS 会导致内网访问失败。"
             "这也是它被标为中风险的原因。",
    ),
    Action(
        id="TCP_AUTOTUNING",
        title="开启 TCP 接收窗口自动调优（normal）",
        why="关闭时高延迟链路吞吐会被窗口大小锁死，是「带宽有但速度上不去」的常见原因。",
        risk="low",
        apply_win=["netsh int tcp set global autotuninglevel=normal"],
        roll_win=["netsh int tcp set global autotuninglevel=disabled"],
        verify="netsh int tcp show global",
    ),
    Action(
        id="TCP_HEURISTICS",
        title="关闭 TCP 窗口缩放启发式（heuristics=disabled）",
        why="Windows 的窗口缩放启发式会为「兼容性」保守地限制窗口，"
            "微软对服务器/网关场景的建议就是关掉它，让系统按实际链路能力放大窗口。",
        risk="low",
        apply_win=["netsh int tcp set heuristics disabled"],
        roll_win=["netsh int tcp set heuristics enabled"],
        verify="netsh int tcp show heuristics",
        note="名字容易看反：这里「关闭启发式」才是提速方向，回滚就是把它重新打开。",
    ),
    Action(
        id="TCP_ECN",
        title="启用 ECN 显式拥塞通知",
        why="让路由器用标记代替丢包来通知拥塞，减少重传。链路不支持时无害。",
        risk="low",
        apply_win=["netsh int tcp set global ecncapability=enabled"],
        roll_win=["netsh int tcp set global ecncapability=disabled"],
        verify="netsh int tcp show global",
    ),
    Action(
        id="TCP_TIMESTAMPS",
        title="启用 TCP 时间戳",
        why="提高 RTT 测量精度与重传判定准确性，对高延迟链路有帮助。",
        risk="low",
        apply_win=["netsh int tcp set global timestamps=enabled"],
        roll_win=["netsh int tcp set global timestamps=disabled"],
        verify="netsh int tcp show global",
    ),
    Action(
        id="RSS_ON",
        title="启用网卡多队列接收（RSS）",
        why="把收包分散到多个 CPU 核，消除单核瓶颈，降低延迟抖动。",
        risk="low",
        apply_win=["# 对所有已启用网卡开启 RSS",
                   "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                   "Enable-NetAdapterRss -ErrorAction SilentlyContinue"],
        roll_win=["Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                  "Disable-NetAdapterRss -ErrorAction SilentlyContinue"],
        verify="Get-NetAdapterRss | Select Name,Enabled",
        note="部分虚拟网卡/旧驱动不支持 RSS，失败属正常，不影响其他项。",
    ),
    Action(
        id="RSC_ON",
        title="启用接收段合并（RSC）",
        why="把多个小包在网卡层合并后再交给协议栈，降低 CPU 占用、提升吞吐。",
        risk="low",
        apply_win=["Set-NetOffloadGlobalSetting -ReceiveSegmentCoalescing Enabled"],
        roll_win=["Set-NetOffloadGlobalSetting -ReceiveSegmentCoalescing Disabled"],
        verify="Get-NetOffloadGlobalSetting",
        note="做网络抓包分析时建议临时关闭 RSC，否则看到的包是合并后的。",
    ),
    Action(
        id="BBR",
        title="启用 BBR 拥塞控制 + fq 队列",
        why="高延迟/轻微丢包链路上吞吐提升显著，是 Linux 上收益最大的单项优化。",
        risk="medium",
        apply_nix=[
            "# 先确认内核支持",
            "modprobe tcp_bbr 2>/dev/null || true",
            "grep -q bbr /proc/sys/net/ipv4/tcp_available_congestion_control || "
            "{ echo '内核不支持 BBR，跳过'; exit 0; }",
            "cat > /etc/sysctl.d/99-ops-bbr.conf <<'EOF'",
            "net.core.default_qdisc=fq",
            "net.ipv4.tcp_congestion_control=bbr",
            "EOF",
            "sysctl --system >/dev/null",
        ],
        roll_nix=[
            "rm -f /etc/sysctl.d/99-ops-bbr.conf",
            "sysctl --system >/dev/null",
        ],
        verify="sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc",
        note="建议在业务低峰执行；已建立的连接不会立即切换算法，新连接生效。",
    ),
    Action(
        id="TCP_TUNING_NIX",
        title="调整 TCP 缓冲区与队列参数",
        why="放开接收/发送缓冲区上限与网卡收包队列，给高带宽链路留出余量。",
        risk="medium",
        apply_nix=[
            "cat > /etc/sysctl.d/99-ops-net.conf <<'EOF'",
            "# 由 服务器安全维护、杀木马病毒skill 生成 —— 网络吞吐基线",
            "net.core.rmem_max=16777216",
            "net.core.wmem_max=16777216",
            "net.core.netdev_max_backlog=16384",
            "net.ipv4.tcp_slow_start_after_idle=0",
            "net.ipv4.tcp_mtu_probing=1",
            "net.ipv4.tcp_fastopen=3",
            "EOF",
            "sysctl --system >/dev/null",
        ],
        roll_nix=[
            "rm -f /etc/sysctl.d/99-ops-net.conf",
            "sysctl --system >/dev/null",
        ],
        verify="sysctl net.core.rmem_max net.core.netdev_max_backlog "
               "net.ipv4.tcp_slow_start_after_idle net.ipv4.tcp_fastopen",
        note="内存较小的机器（<2GB）不要把 rmem_max 调得过大，缓冲区会吃内存。",
    ),
]


def actions_for(scope: str = "auto") -> list[Action]:
    if scope == "win":
        return [a for a in ACTIONS if a.apply_win]
    if scope == "nix":
        return [a for a in ACTIONS if a.apply_nix]
    return [a for a in ACTIONS if (a.apply_win if WINDOWS else a.apply_nix)]


# --------------------------------------------------------------------------
# 脚本生成
# --------------------------------------------------------------------------

_HDR_PS1 = """# =============================================================================
# 网络提速脚本 —— 由 服务器安全维护、杀木马病毒skill 生成
#
# 生成时间：@@TS@@
# 目标平台：Windows
# 动作数量：@@N@@
#
# ⚠ 执行前请先通读。本脚本会修改系统网络参数。
#   回滚脚本：@@ROLLBACK@@
#   执行后请用下面的「验证」命令确认，并重跑 `net diag` 对比前后差异。
#
# 说明：网络参数改动**不会带来超过物理带宽的速度**。
#       它消除的是本机侧的限制（窗口、队列、收包分发）。
#
# 出错处理：每个动作单独 try/catch。某一项失败只报出「哪一项 + 什么原因」，
#           其余动作继续，最后统一汇总，绝不静默中断。
# =============================================================================
$ErrorActionPreference = 'Continue'
$ND_FAILED = New-Object System.Collections.ArrayList

function Step([string]$msg) { Write-Host ""; Write-Host "--- [$msg]" -ForegroundColor Cyan }
function Ok([string]$m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "    [!] $m"  -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host "    [X] $m"  -ForegroundColor Red
                            [void]$ND_FAILED.Add($m) }
function Verify([string]$cmd) {
    Write-Host "    验证：$cmd" -ForegroundColor DarkGray
    try { Invoke-Expression $cmd 2>&1 | Select-Object -First 12 | ForEach-Object {
        Write-Host "      $_" -ForegroundColor DarkGray } }
    catch { Warn "验证命令未跑成：$($_.Exception.Message)（不影响已应用的改动）" }
}
function Apply-Step([string]$name, [scriptblock]$body) {
    try {
        & $body
        Ok "$name 已应用"
    } catch {
        Fail "$name 未生效：$($_.Exception.Message)"
        Write-Host "        原因：多为权限不足 / 该网卡或参数在本机不存在 / 驱动不支持该项" -ForegroundColor DarkYellow
        if ($_.Exception.InnerException) {
            Write-Host "        细节：$($_.Exception.InnerException.Message)" -ForegroundColor DarkGray
        }
        Write-Host "        处理：其余动作继续；已改动的部分可用回滚脚本还原" -ForegroundColor DarkGray
    }
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "错误：需要以管理员身份运行。" -ForegroundColor Red
    Write-Host "      原因：网卡与 TCP 参数属于系统级设置，普通权限改不动。" -ForegroundColor DarkGray
    Write-Host "      处理：右键 PowerShell → 以管理员身份运行，再执行本脚本。" -ForegroundColor DarkGray
    exit 1
}

Write-Host "网络提速 · 共 @@N@@ 个动作" -ForegroundColor Green
"""

_HDR_SH = """#!/bin/sh
# =============================================================================
# 网络提速脚本 —— 由 服务器安全维护、杀木马病毒skill 生成
#
# 生成时间：@@TS@@
# 目标平台：Linux / UNIX
# 动作数量：@@N@@
#
# ⚠ 执行前请先通读。本脚本会修改内核网络参数（写入 /etc/sysctl.d/）。
#   回滚脚本：@@ROLLBACK@@
#   执行后请用下面的「验证」命令确认，并重跑 `net diag` 对比前后差异。
#
# 说明：网络参数改动**不会带来超过物理带宽的速度**。
#       它消除的是本机侧的限制（拥塞算法、缓冲区、队列）。
#
# 出错处理：每个动作在独立子 shell 里执行，失败只报出「哪一项 + 什么原因」，
#           其余动作继续，最后统一汇总，绝不静默中断。
# =============================================================================
set -u
ND_FAILED=""
if [ "$(id -u)" != "0" ]; then
  echo "错误：需要 root 权限（或 sudo）。" >&2
  echo "      原因：内核网络参数（sysctl）与网卡队列只有 root 能改。" >&2
  echo "      处理：用 'sudo sh <本脚本>' 执行。" >&2
  exit 1
fi

step()   { echo ""; echo "--- [$1]"; }
ok()     { echo "    [OK] $1"; }
warn()   { echo "    [!] $1"; }
fail()   { echo "    [X] $1"; ND_FAILED="$ND_FAILED$1; "; }
verify() { echo "    验证：$1"; sh -c "$1" 2>/dev/null | head -12 | sed 's/^/      /' || true; }

echo "网络提速 · 共 @@N@@ 个动作"
"""

_TAIL_PS1 = """
Write-Host ""
if ($ND_FAILED.Count -gt 0) {
    Write-Host "⚠ 有 $($ND_FAILED.Count) 个动作未生效：" -ForegroundColor Yellow
    $ND_FAILED | ForEach-Object { Write-Host "  · $_" -ForegroundColor Yellow }
    Write-Host "请重跑 net diag 确认现状；需要撤销请执行回滚脚本。" -ForegroundColor DarkGray
    exit 2
}
Write-Host "完成。请重跑 net diag 对比前后差异。" -ForegroundColor Green
Write-Host "如需回滚： powershell -File .\\@@ROLLBACK@@" -ForegroundColor DarkGray
"""

_TAIL_SH = """
echo ""
if [ -n "$ND_FAILED" ]; then
  echo "⚠ 以下动作未成功：$ND_FAILED"
  echo "  请重跑 net diag 确认现状；需要撤销请执行回滚脚本。"
  exit 2
fi
echo "完成。请重跑 net diag 对比前后差异。"
echo "如需回滚： sh ./@@ROLLBACK@@"
"""


def generate_tune_scripts(scope: str = "auto", out_dir: str | None = None) -> dict:
    ensure_output_dir()
    NET_DIR.mkdir(parents=True, exist_ok=True)
    plat = "win" if (scope == "win" or (scope == "auto" and WINDOWS)) else "nix"
    acts = actions_for(plat)
    ts = now_stamp()
    d = Path(out_dir) if out_dir else (NET_DIR / f"tune-{ts}")
    d.mkdir(parents=True, exist_ok=True)
    suffix = ".ps1" if plat == "win" else ".sh"
    apply_p = d / f"net-tune-apply{suffix}"
    roll_p = d / f"net-tune-rollback{suffix}"

    header = _HDR_PS1 if plat == "win" else _HDR_SH
    header = (header.replace("@@TS@@", ts).replace("@@N@@", str(len(acts)))
              .replace("@@ROLLBACK@@", roll_p.name))

    def lines_of(which: str) -> list[str]:
        out = []
        for a in acts:
            steps = a.apply_win if (plat == "win" and which == "apply") else \
                a.roll_win if (plat == "win") else \
                a.apply_nix if which == "apply" else a.roll_nix
            if not steps:
                continue
            flag = {"high": " [高风险]", "medium": " [中风险]"}.get(a.risk, "")
            out.append("")
            out.append(f"# --- [{a.id}] {a.title}{flag}")
            out.append(f"#     {a.why}")
            if a.note:
                out.append(f"#     注意：{a.note}")
            if plat == "win":
                # 每个动作独立 try/catch：失败只报出这一项，其余照常执行
                out.append(f'Apply-Step "{a.title}" {{')
                out.extend(steps)
                out.append("}")
                if which == "apply" and a.verify:
                    out.append(f'Verify "{a.verify}"')
            else:
                ver = (a.verify_nix or a.verify) if plat != "win" else a.verify
                out.append(f'step "{a.title}"')
                out.append("if ! ( set -e")
                out.extend(steps)
                out.append("); then")
                out.append(f'  fail "{a.title} 未生效（原因见上）"')
                out.append('  echo "        原因：多为权限不足 / 命令不存在 / 内核或驱动不支持该项"')
                out.append('  echo "        处理：其余动作继续执行；已改动的部分可用回滚脚本还原"')
                out.append("fi")
                if which == "apply" and ver:
                    out.append(f'verify "{ver}"')
        return out

    tail = _TAIL_PS1.replace("@@ROLLBACK@@", roll_p.name) if plat == "win" else \
        _TAIL_SH.replace("@@ROLLBACK@@", roll_p.name)

    write_script(apply_p, header + "\n".join(lines_of("apply")) + "\n" + tail)
    write_script(
        roll_p,
        (header.replace("网络提速 · 共", "网络提速回滚 · 共"))
        + "\n".join(lines_of("roll")) + "\n" + tail)
    if plat != "win":
        import os

        for p in (apply_p, roll_p):
            try:
                os.chmod(p, 0o755)
            except OSError:
                pass

    return {
        "ok": True, "platform": plat, "action_count": len(acts),
        "actions": [{"id": a.id, "title": a.title, "risk": a.risk,
                     "why": a.why, "note": a.note} for a in acts],
        "apply_script": str(apply_p), "rollback_script": str(roll_p),
        "dir": str(d),
    }


def apply_script(path: str, target: str | None = None) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"脚本不存在：{path}"}
    if target:
        import subprocess

        user, _, host = target.partition("@")
        if not host:
            user, host = "root", target
        src = p.read_text(encoding="utf-8")
        try:
            pr = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                 f"{user or 'root'}@{host}", "sudo sh -s"],
                input=src.encode("utf-8"), capture_output=True, timeout=600)
            return {"ok": pr.returncode == 0,
                    "stdout": pr.stdout.decode("utf-8", "replace"),
                    "stderr": pr.stderr.decode("utf-8", "replace"),
                    "rc": pr.returncode}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if WINDOWS:
        rc, out, err = run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(p)], timeout=900)
    else:
        rc, out, err = run(["sh", str(p)], timeout=900)
    return {"ok": rc == 0, "stdout": out, "stderr": err, "rc": rc}


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render_text(f: NetFacts, findings: list[NetFinding]) -> str:
    L: list[str] = []
    L.append("=" * 74)
    L.append("  上网提速 · 只读诊断（未修改任何配置）")
    L.append("=" * 74)
    L.append("")
    if f.error:
        L.append(f"  采集提示：{f.error}")
        L.append("  —— 下面的结论覆盖度受限，缺失项不等于「正常」。")
        L.append("")

    if WINDOWS or f.has("win.hostname"):
        adapters = f.data.get("win.adapters") or []
        if adapters:
            L.append("  活动网卡")
            for a in adapters:
                if isinstance(a, dict):
                    L.append(f"    {a.get('name','?'):<18}{a.get('speed','?'):>10}  "
                             f"{C.gray(str(a.get('desc',''))[:44])}")
            L.append("")
        dns_rows = f.data.get("win.dns") or []
        flat = []
        for r in dns_rows:
            if isinstance(r, dict):
                flat += [str(s) for s in (r.get("servers") or [])]
        if flat:
            L.append(f"  DNS：{', '.join(sorted(set(flat)))}")
            L.append("")
        tcp = f.data.get("win.tcp") or {}
        off = f.data.get("win.offload") or {}
        if isinstance(tcp, dict) and tcp:
            L.append("  TCP 全局设置")
            for k in ("autotuning", "congestion", "ecn", "timestamps", "heuristics"):
                if k in tcp:
                    L.append(f"    {k:<14}{tcp[k]}")
            if isinstance(off, dict) and off.get("rss"):
                L.append(f"    {'rss':<14}{off['rss']}（网卡多队列接收）")
            L.append("")

    facts = _unix_facts(f)
    if facts is not None:
        for row in facts.lst("net.if")[:6]:
            p = str(row).split("|")
            if len(p) >= 5 and p[1] == "UP":
                L.append(f"  接口 {p[0]:<12} MTU {p[2]:<6} IP {p[4]}")
        cc = ""
        for row in facts.lst("net.sysctl"):
            k, _, v = str(row).partition("|")
            if k == "net.ipv4.tcp_congestion_control":
                cc = v
        if cc:
            L.append(f"  拥塞控制：{cc}"
                     f"    队列：{' '.join(facts.lst('net.qdisc'))[:60]}")
        L.append("")

    for row in (f.data.get("net.ping") or []) or (facts.lst("net.ping") if facts else []):
        p = str(row).split("|")
        if len(p) >= 6:
            label = dict(PING_TARGETS).get(p[0], p[0])
            L.append(f"  延迟 {label:<12} {p[1]:<18} 丢包 {p[2]:>4}%   "
                     f"最小 {p[3]:>7}  平均 {p[4]:>7}  最大 {p[5]:>7}  (ms)")
        elif len(p) >= 5:
            label = dict(PING_TARGETS).get(p[0], p[0])
            L.append(f"  延迟 {label:<12} {p[1]:<18} 丢包 {p[2]:>4}%   "
                     f"最小 {p[3]:>7}  平均 {p[4]:>7}  (ms)")
    L.append("")

    groups = [("BAD", "必须处理"), ("WARN", "建议处理"),
              ("INFO", "供参考"), ("GOOD", "表现良好")]
    for lvl, title in groups:
        items = [x for x in findings if x.level == lvl]
        if not items:
            continue
        L.append(f"  【{title}】{len(items)} 项")
        for x in items:
            L.append(f"    · {x.title}  {C.gray('(' + x.id + ')')}")
            for ev in x.evidence[:3]:
                L.append(f"        {ev}")
            if x.impact:
                L.append(f"        {C.gray('影响：' + x.impact)}")
            if x.fix:
                L.append(f"        {C.green('做法：')}{x.fix.splitlines()[0]}")
        L.append("")

    L.append("  说明：网络参数调优**不会超过运营商给的带宽上限**，")
    L.append("        它消除的是本机侧的限制。真正的带宽不足只能靠升级线路解决。")
    L.append("")
    L.append("  下一步：")
    L.append("    · 生成调优脚本（不执行）： cli.py net tune")
    L.append("    · 执行： cli.py net tune --apply --yes")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_net(args) -> int:
    if args.action == "diag":
        print(C.bold("上网提速 · 只读诊断"))
        print(C.gray("  只读取配置与实测链路质量，不修改任何设置。"))
        print()
        if not WINDOWS and not DARWIN and not LINUX:
            print(C.orange("  当前平台未适配，仅能做基础检查。"))

        if args.target:
            from linux_facts import Target

            user_host, _, port = args.target.partition(":")
            user, _, host = user_host.partition("@")
            t = Target(kind="ssh", host=host, user=user or "root",
                       port=int(port or 22), key=args.key or "",
                       label=args.target)
            print(C.gray(f"  · 通过 SSH 下发只读网络探针到 {t.display} ..."))
            f = _collect_unix(t)
        elif WINDOWS:
            print(C.gray("  · 读取网卡 / TCP 参数 / DNS / 代理配置 ..."))
            f = _collect_windows()
            print(C.gray("  · 实测延迟、DNS 可达性、HTTP 首字节耗时 ..."))
        else:
            f = _collect_unix()

        findings = analyze(f)
        findings.sort(key=lambda x: LEVEL_ORDER.get(x.level, 9))
        text = render_text(f, findings)
        print(text)

        ensure_output_dir()
        NET_DIR.mkdir(parents=True, exist_ok=True)
        jp = NET_DIR / f"diag-{now_stamp()}.json"
        jp.write_text(json.dumps({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "findings": [x.__dict__ for x in findings],
            "error": f.error,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(C.gray(f"  诊断结果：{jp}"))
        if args.json:
            print(json.dumps({
                "bad": len([x for x in findings if x.level == "BAD"]),
                "warn": len([x for x in findings if x.level == "WARN"]),
                "info": len([x for x in findings if x.level == "INFO"]),
                "json": str(jp),
            }, ensure_ascii=False))
        return 0

    if args.action == "tune":
        scope = args.platform or "auto"
        print(C.bold("上网提速 · 生成调优脚本"))
        res = generate_tune_scripts(scope)
        print(f"  平台 {res['platform']} · 共 {res['action_count']} 个动作")
        for a in res["actions"]:
            flag = {"medium": "  [中风险]"}.get(a["risk"], "")
            print(f"    [{a['id']}] {a['title']}{flag}")
            if a.get("note"):
                print(C.gray(f"        注意：{a['note']}"))
        print()
        print(f"  正向脚本：{res['apply_script']}")
        print(f"  回滚脚本：{res['rollback_script']}")
        print()
        if not (args.apply and args.yes):
            print(C.gray("  当前为「只看不改」模式。确认无误后加 --apply --yes 执行。"))
            return 3
        print(C.bold("  执行中 ..."))
        r = apply_script(res["apply_script"], target=args.target)
        for line in (r.get("stdout") or "").splitlines()[-80:]:
            print("    " + line)
        if r.get("stderr"):
            for line in r["stderr"].splitlines()[-20:]:
                print(C.orange("    ! " + line))
        if not r.get("ok"):
            print(C.red(f"  执行未完全成功（rc={r.get('rc')}）。"
                        f"如异常请用回滚脚本恢复。"))
            return 2
        print(C.green("  ✓ 执行完成。建议重跑 `net diag` 对比前后差异。"))
        return 0

    print(C.red(f"  未知动作：{args.action}"))
    return 1


def add_net_parser(sub) -> None:
    p = sub.add_parser("net", help="上网提速：网络只读诊断 / 生成调优脚本")
    p.add_argument("action", choices=["diag", "tune"])
    p.add_argument("--target", default=None, help="远程 Linux/UNIX：user@host[:port]")
    p.add_argument("--key", default="", help="SSH 私钥路径")
    p.add_argument("--platform", default="auto", choices=["auto", "win", "nix"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_net)
