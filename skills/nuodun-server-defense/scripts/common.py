#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common.py — 跨平台基础层。

职责：
  * 平台探测（Windows / Linux / macOS）、权限探测
  * 可靠的外部命令执行（超时、编码兜底、失败不抛异常）
  * Finding / Report 数据模型与评分
  * 统一 JSON / 文本输出与 outputs 落盘

设计约束：
  * 只用标准库，任何机器上都能 import 成功。
  * 采集失败必须降级为一条 WARN 记录，绝不让整个体检崩掉。
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = SKILL_DIR / "outputs"
ASSETS_DIR = SKILL_DIR / "assets"
REFS_DIR = SKILL_DIR / "references"

SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
SEV_LABEL = {
    "CRITICAL": "严重",
    "HIGH": "高危",
    "MEDIUM": "中危",
    "LOW": "低危",
    "INFO": "提示",
}
SEV_COLOR = {
    "CRITICAL": "#b91c1c",
    "HIGH": "#dc2626",
    "MEDIUM": "#d97706",
    "LOW": "#2563eb",
    "INFO": "#64748b",
}
# 每级问题对健康分的扣分权重（同一问题不重复扣）
SEV_PENALTY = {"CRITICAL": 15, "HIGH": 8, "MEDIUM": 3, "LOW": 1, "INFO": 0}

CATEGORIES = {
    "perf": "性能",
    "sec": "安全",
    "malware": "恶意代码",
    "log": "日志",
    "crash": "稳定性",
    "backup": "备份",
    "config": "配置",
}

WINDOWS = sys.platform.startswith("win")
LINUX = sys.platform.startswith("linux")
DARWIN = sys.platform == "darwin"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


# --------------------------------------------------------------------------
# 命令执行
# --------------------------------------------------------------------------

def run(
    cmd: Any,
    timeout: int = 30,
    shell: bool = False,
    cwd: str | None = None,
    env: dict | None = None,
) -> tuple[int, str, str]:
    """执行外部命令，永不抛异常。返回 (returncode, stdout, stderr)。

    cmd 可以是字符串（shell=True）或列表（shell=False，推荐）。
    编码优先 UTF-8，失败回退到本地 ANSI/latin-1，避免中文系统乱码崩溃。
    """
    try:
        p = subprocess.run(
            cmd,
            shell=shell,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", f"not found: {e}"
    except Exception as e:  # noqa: BLE001 - 采集器必须绝对健壮
        return 1, "", f"{type(e).__name__}: {e}"

    def _dec(b: bytes) -> str:
        if not b:
            return ""
        for enc in ("utf-8", "gbk", "cp1252", "latin-1"):
            try:
                return b.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return b.decode("utf-8", errors="replace")

    return p.returncode, _dec(p.stdout).strip(), _dec(p.stderr).strip()


def ps(script: str, timeout: int = 60) -> tuple[int, str, str]:
    """执行一段 PowerShell 脚本（仅 Windows）。返回 (rc, stdout, stderr)。

    统一加 -NoProfile -NonInteractive，避免用户 profile 污染与交互式挂起。
    """
    if not WINDOWS:
        return 1, "", "not windows"
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return 127, "", "powershell not found"
    return run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=timeout,
    )


def ps_json(script: str, timeout: int = 60) -> Any:
    """执行 PowerShell 并把输出按 JSON 解析。失败返回 None。"""
    rc, out, _ = ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout=timeout)
    if rc != 0 or not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return [data]
    return data


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


# --------------------------------------------------------------------------
# 权限与身份
# --------------------------------------------------------------------------

def is_admin() -> bool:
    """是否为管理员 / root。决定哪些加固动作可执行。"""
    if WINDOWS:
        try:
            import ctypes  # noqa: PLC0415

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def whoami() -> str:
    try:
        return f"{getpass.getuser()}@{platform.node()}"
    except Exception:  # noqa: BLE001
        return "unknown"


def hostname() -> str:
    return platform.node() or "unknown"


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------

@dataclass
class Finding:
    """一条体检结论。每个 Finding 都必须能回答：证据是什么、有什么危害、怎么修。"""

    id: str
    title: str
    severity: str = "INFO"
    category: str = "config"
    evidence: list[str] = field(default_factory=list)
    impact: str = ""
    fix: str = ""
    ref: str = ""
    auto_fixable: bool = False
    weight: float = 1.0  # 同一 id 多次出现时的缩放；用于压制同一根因的重复扣分

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity_label"] = SEV_LABEL.get(self.severity, self.severity)
        d["category_label"] = CATEGORIES.get(self.category, self.category)
        return d


@dataclass
class CheckResult:
    """一组检查（一个子系统）的结果。"""

    name: str
    category: str = "config"
    findings: list[Finding] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ok: bool = True

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def fact(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def note(self, msg: str) -> None:
        """追加一条提示（不是问题，也不代表采集失败），随报告一起落盘。"""
        self.notes.append(msg)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "ok": self.ok,
            "errors": self.errors,
            "notes": self.notes,
            "facts": self.facts,
            "findings": [f.to_dict() for f in self.findings],
        }


class Report:
    """一次体检的完整结果，负责评分、序列化、落盘。"""

    def __init__(self, mode: str = "audit"):
        self.mode = mode
        self.started = now_iso()
        self.finished = ""
        self.host = {
            "hostname": hostname(),
            "operator": whoami(),
            "is_admin": is_admin(),
            "platform": sys.platform,
            "python": platform.python_version(),
        }
        self.results: list[CheckResult] = []
        self.notes: list[str] = []
        self.duration_s = 0.0

    def add_result(self, r: CheckResult) -> CheckResult:
        self.results.append(r)
        # 子系统的提示汇总到报告级 notes，避免只落在 results[].notes 里被忽略
        for msg in r.notes:
            self.notes.append(f"{r.name}：{msg}")
        return r

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    # ---- 聚合 ----

    @property
    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for r in self.results:
            out.extend(r.findings)
        out.sort(key=lambda f: (SEV_ORDER.get(f.severity, 9), f.category, f.id))
        return out

    def counts(self) -> dict:
        c = {k: 0 for k in SEV_ORDER}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        c["TOTAL"] = sum(v for k, v in c.items() if k in SEV_ORDER)
        return c

    def score(self) -> int:
        """健康分 0-100。同一 id 只扣一次（取最严重的权重），避免刷分。"""
        seen: dict[str, str] = {}
        for f in self.findings:
            prev = seen.get(f.id)
            if prev is None or SEV_ORDER[f.severity] < SEV_ORDER[prev]:
                seen[f.id] = f.severity
        penalty = 0.0
        for sev in seen.values():
            penalty += SEV_PENALTY.get(sev, 0)
        return max(0, min(100, round(100 - penalty)))

    def grade(self) -> str:
        s = self.score()
        if s >= 90:
            return "A"
        if s >= 80:
            return "B"
        if s >= 70:
            return "C"
        if s >= 60:
            return "D"
        return "F"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "started": self.started,
            "finished": self.finished,
            "duration_s": round(self.duration_s, 2),
            "host": self.host,
            "score": self.score(),
            "grade": self.grade(),
            "counts": self.counts(),
            "notes": self.notes,
            "results": [r.to_dict() for r in self.results],
            "findings": [f.to_dict() for f in self.findings],
        }

    def save(self, prefix: str | None = None) -> Path:
        ensure_output_dir()
        name = f"{prefix or self.mode}-{now_stamp()}.json"
        path = OUTPUT_DIR / name
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


# --------------------------------------------------------------------------
# 脚本落盘 —— 编码选错脚本会被解释器读坏，必须集中处理
# --------------------------------------------------------------------------

def write_script(path, text: str) -> None:
    """写出脚本，按语言选择正确编码。

    * ``.ps1`` / ``.psm1``：**必须带 UTF-8 BOM**。Windows PowerShell 5.1 在没有 BOM
      时按本地 ANSI(GBK) 解码，中文（尤其全角标点）会被撕成非法字节序列，报出
      "字符串缺少终止符"这类看不懂的语法错误 —— 内容明明是对的。
    * ``.sh`` / 其他：**绝不能带 BOM**。BOM 会被并入 shebang，``#!/bin/sh`` 直接失效，
      表现为 "cannot execute binary file" 或莫名其妙的命令未找到。

    统一走这个函数，就不会再有人踩两次同一个坑。
    """
    from pathlib import Path as _P
    p = _P(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() in (".ps1", ".psm1"):
        p.write_text(text, encoding="utf-8-sig")
    else:
        p.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# 输出与日志
# --------------------------------------------------------------------------

class C:
    """终端配色；非 TTY 或 NO_COLOR 时自动关闭。"""

    ON = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    @staticmethod
    def _w(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if C.ON else s

    @classmethod
    def red(cls, s: str) -> str:
        return cls._w("1;31", s)

    @classmethod
    def orange(cls, s: str) -> str:
        return cls._w("1;33", s)

    @classmethod
    def blue(cls, s: str) -> str:
        return cls._w("1;34", s)

    @classmethod
    def gray(cls, s: str) -> str:
        return cls._w("90", s)

    @classmethod
    def green(cls, s: str) -> str:
        return cls._w("1;32", s)

    @classmethod
    def bold(cls, s: str) -> str:
        return cls._w("1", s)


def sev_tag(sev: str) -> str:
    label = SEV_LABEL.get(sev, sev)
    pad = f"[{label}]"
    if sev == "CRITICAL":
        return C.red(pad)
    if sev == "HIGH":
        return C.red(pad)
    if sev == "MEDIUM":
        return C.orange(pad)
    if sev == "LOW":
        return C.blue(pad)
    return C.gray(pad)


def print_findings(findings: Iterable[Finding], limit: int | None = None) -> None:
    items = list(findings)
    if limit:
        items = items[:limit]
    for f in items:
        print(f"{sev_tag(f.severity)} {f.title}  {C.gray('(' + f.id + ')')}")
        for ev in f.evidence[:4]:
            print(f"      · {ev}")
        if f.fix:
            print(f"      {C.green('修复:')} {f.fix.splitlines()[0]}")
        if f.ref:
            print(f"      {C.gray('参考: ' + f.ref)}")
        print()


def summarize(report: Report, prefix: str = "") -> dict:
    """打印汇总并返回一行 JSON（供 Agent 判读）。"""
    c = report.counts()
    print()
    print(C.bold("=" * 68))
    print(C.bold(f"  体检汇总 · {report.host['hostname']} · 模式 {report.mode}"))
    print(C.bold("=" * 68))
    print(f"  健康分：{report.score()}/100  等级：{report.grade()}")
    print(
        f"  严重 {c['CRITICAL']} · 高危 {c['HIGH']} · 中危 {c['MEDIUM']} · "
        f"低危 {c['LOW']} · 提示 {c['INFO']}  （共 {c['TOTAL']} 项）"
    )
    failed = [r.name for r in report.results if not r.ok]
    if failed:
        print(C.orange(f"  ⚠ 采集降级的模块：{', '.join(failed)}（结论可能不完整）"))
    print()

    payload = {
        "mode": report.mode,
        "score": report.score(),
        "grade": report.grade(),
        "counts": c,
        "degraded_modules": failed,
        "json": prefix,
        "host": report.host["hostname"],
    }
    print(C.gray("JSON: " + json.dumps(payload, ensure_ascii=False)))
    return payload


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}EB"


def human_seconds(sec: float) -> str:
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}天")
    if h:
        parts.append(f"{h}小时")
    if m:
        parts.append(f"{m}分")
    if s or not parts:
        parts.append(f"{s}秒")
    return "".join(parts)


def pct(part: float, whole: float) -> float:
    if not whole:
        return 0.0
    return round(part * 100.0 / whole, 1)


def parse_size(text: str) -> int:
    """把 '1.5G' / '512M' / '1024' 解析成字节。解析失败返回 0。"""
    if not text:
        return 0
    m = re.match(r"^\s*([\d.]+)\s*([KMGT]?)B?\s*$", str(text), re.IGNORECASE)
    if not m:
        return 0
    val = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[m.group(2).upper()]
    return int(val * mult)


# --------------------------------------------------------------------------
# 终端兜底与错误解释（薄封装，实现在 errors.py）
# --------------------------------------------------------------------------
# 放在模块末尾、用 try 包住：即使 errors.py 缺失，common 也必须能正常 import。
try:
    import errors as _errors  # noqa: PLC0415

    # 所有 import common 的入口（cli / ui / setup / selftest / 各子模块）
    # 都自动获得「中文/符号打不出来时降级为 ?，而不是崩掉」的保障。
    _errors.set_stdio_utf8()
except Exception:  # noqa: BLE001
    _errors = None  # type: ignore[assignment]


def explain_rc(rc: int, stderr: str = "") -> tuple[str, str]:
    """外部命令退出码 -> (原因, 处理建议)。errors 不可用时退化为通用文案。"""
    if _errors is not None:
        try:
            return _errors.explain_rc(rc, stderr)
        except Exception:  # noqa: BLE001
            pass
    if rc == 0:
        return "成功", ""
    return f"命令返回非零退出码 {rc}", "看它输出的错误行判断原因"


def explain_exc(exc: BaseException) -> tuple[str, str]:
    """异常 -> (原因, 处理建议)。"""
    if _errors is not None:
        try:
            cause, hint, _c = _errors.explain(exc)
            return cause, hint
        except Exception:  # noqa: BLE001
            pass
    return f"{type(exc).__name__}: {exc}", "查看错误日志了解详情"
