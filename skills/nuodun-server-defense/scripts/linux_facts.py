#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
linux_facts.py — 巡检探针的执行通道与输出解析。

两种执行方式共用同一份 facts 结构：
  * local ：本机就是 Linux/UNIX，直接 sh 探针
  * ssh   ：远程主机，把探针脚本通过 stdin 管道送过去执行（不落盘、不留文件）

输出格式：`key<TAB>编码:值`，同键多行 = 列表。
解析器对任何畸形行都容错丢弃并计数，解析统计会写进报告（避免"静默丢数据"）。
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

AGENT_PATH = Path(__file__).resolve().parent / "linux_agent.sh"
NET_AGENT_PATH = Path(__file__).resolve().parent / "net_agent.sh"


@dataclass
class Target:
    """巡检目标。kind = local | ssh"""

    kind: str = "local"
    host: str = ""
    user: str = "root"
    port: int = 22
    key: str = ""
    password: str = ""
    label: str = ""

    @property
    def display(self) -> str:
        if self.kind == "local":
            return self.label or "本机"
        return self.label or f"{self.user}@{self.host}:{self.port}"


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

def _agent_source(path: Path | None = None) -> str:
    return (path or AGENT_PATH).read_text(encoding="utf-8")


def run_agent(target: Target, deep: bool = False, timeout: int = 300,
              script_path: Path | None = None) -> tuple[bool, str, str]:
    """执行探针。返回 (ok, raw_output, error)。

    script_path 让你换一份探针脚本（如网络诊断用的 net_agent.sh），
    执行通道、编码约定、超时策略完全一致。
    """
    src_path = Path(script_path) if script_path else AGENT_PATH
    if not src_path.exists():
        return False, "", f"探针脚本缺失：{src_path}"

    src = _agent_source(src_path)
    arg = " --deep" if deep else ""

    if target.kind == "local":
        sh = shutil.which("sh") or shutil.which("bash")
        if not sh:
            return False, "", "本机没有 sh，无法执行巡检探针"
        p = subprocess.run(
            [sh, "-s", "--", *(["--deep"] if deep else [])],
            input=src.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
        )
        return (p.returncode == 0 or p.stdout), p.stdout.decode("utf-8", "replace"), \
            p.stderr.decode("utf-8", "replace")

    # ---- 远程 SSH ----
    if not target.host:
        return False, "", "未指定远程主机"

    ssh = shutil.which("ssh")
    plink = shutil.which("plink")

    if ssh:
        args = [ssh, "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15"]
        if target.key:
            args += ["-i", target.key]
        args += ["-p", str(target.port), f"{target.user}@{target.host}", f"sh -s --{arg}"]
        if target.password:
            sp = shutil.which("sshpass")
            if sp:
                args = [sp, "-p", target.password] + args
            else:
                return False, "", ("需要密码但未找到 sshpass。请改用密钥认证"
                                   "（-i 私钥），或在目标机写入公钥后再连接。")
        try:
            p = subprocess.run(args, input=src.encode("utf-8"),
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "", f"SSH 执行超时（{timeout}s）"
        except Exception as e:  # noqa: BLE001
            return False, "", f"SSH 执行失败：{type(e).__name__}: {e}"
        if p.returncode != 0 and not p.stdout:
            return False, "", (p.stderr.decode("utf-8", "replace") or "").strip()[:500]
        return True, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    if plink:
        args = [plink, "-batch", "-P", str(target.port)]
        if target.key:
            args += ["-i", target.key]
        if target.password:
            args += ["-pw", target.password]
        args += [f"{target.user}@{target.host}", f"sh -s --{arg}"]
        try:
            p = subprocess.run(args, input=src.encode("utf-8"),
                               capture_output=True, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return False, "", f"plink 执行失败：{type(e).__name__}: {e}"
        return True, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    return False, "", "本机没有可用的 ssh / plink 客户端，无法连接远程主机"


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

@dataclass
class Facts:
    data: dict = field(default_factory=dict)
    bad_lines: int = 0
    total_lines: int = 0
    error: str = ""
    encodings: set = field(default_factory=set)

    # ---- 取值 ----
    def raw(self, key: str):
        return self.data.get(key)

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
        v = self.s(key).strip()
        if isinstance(self.data.get(key), list):
            v = (self.data[key][-1] or "").strip()
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def f(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.s(key).strip())
        except (TypeError, ValueError):
            return default

    def has(self, key: str) -> bool:
        return key in self.data

    def missing(self, keys: list) -> list:
        return [k for k in keys if k not in self.data]


def _decode(flag: str, payload: str) -> str | None:
    if flag == "b":
        if not payload:
            return ""
        try:
            pad = payload + "=" * (-len(payload) % 4)
            return base64.b64decode(pad).decode("utf-8", "replace").replace("\x01", "\n")
        except Exception:  # noqa: BLE001
            return None
    if flag == "p":
        return payload.replace("\x01", "\n")
    return payload


def parse(raw: str) -> Facts:
    facts = Facts()
    for line in (raw or "").splitlines():
        facts.total_lines += 1
        if "\t" not in line:
            if line.strip():
                facts.bad_lines += 1
            continue
        key, _, rest = line.partition("\t")
        if len(rest) > 1 and rest[1] == ":" and rest[0] in ("b", "p"):
            flag, payload = rest[0], rest[2:]
        else:
            flag, payload = "?", rest
        facts.encodings.add(flag)
        val = _decode(flag, payload)
        if val is None:
            facts.bad_lines += 1
            continue
        val = val.strip("\n")
        if key in facts.data:
            if not isinstance(facts.data[key], list):
                facts.data[key] = [facts.data[key]]
            facts.data[key].append(val)
        else:
            facts.data[key] = val
    return facts


def collect(target: Target, deep: bool = False, timeout: int = 300) -> Facts:
    ok, out, err = run_agent(target, deep=deep, timeout=timeout)
    if not ok and not out:
        f = Facts()
        f.error = err or "探针执行失败"
        return f
    f = parse(out)
    f.error = "" if f.s("agent.done") == "1" else (err.strip()[:400] or "探针未正常结束")
    return f


# --------------------------------------------------------------------------
# 常用派生量（供规则模块复用）
# --------------------------------------------------------------------------

def cpu_snapshot(facts: Facts) -> dict:
    """从两次 /proc/stat 快照算 CPU 占用。"""
    raw = facts.s("perf.cpu_stat")
    if ";;" not in raw:
        return {}
    a, _, b = raw.partition(";;")
    def parse_line(line):
        p = line.split()
        if len(p) < 5:
            return None
        vals = [int(x) for x in p[1:] if x.isdigit()]
        if len(vals) < 4:
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return idle, total
    pa, pb = parse_line(a), parse_line(b)
    if not pa or not pb:
        return {}
    dt = pb[1] - pa[1]
    di = pb[0] - pa[0]
    if dt <= 0:
        return {}
    return {"user_pct": round((1 - di / dt) * 100, 1), "wall_jiffies": dt}


def disk_io_rates(facts: Facts) -> dict:
    """从两次 /proc/diskstats 快照算各设备读写速率（B/s）与繁忙度。"""
    raw = facts.s("perf.diskstats")
    if ";;" not in raw:
        return {}
    a, _, b = raw.partition(";;")
    def snap(txt):
        d = {}
        for line in txt.strip().splitlines():
            p = line.split()
            if len(p) < 4:
                continue
            try:
                d[p[0]] = {"rd": int(p[1]), "wr": int(p[2]), "io": int(p[3])}
            except ValueError:
                continue
        return d
    sa, sb = snap(a), snap(b)
    out = {}
    for dev, v in sb.items():
        if dev not in sa:
            continue
        out[dev] = {
            "read_bps": max(0, (v["rd"] - sa[dev]["rd"]) * 512),
            "write_bps": max(0, (v["wr"] - sa[dev]["wr"]) * 512),
            "busy_pct": round(min(100.0, max(0, v["io"] - sa[dev]["io"]) / 10.0), 1),
        }
    return out
