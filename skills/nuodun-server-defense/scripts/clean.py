#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean.py — 硬盘瘦身（空间治理）。

三条铁律（与 SKILL.md 的行为契约一致）：
  1. **默认只扫不删**。``clean scan`` 全程只读，产出「可回收清单」供复核。
  2. **删除必须显式确认**。``clean clean --apply --yes``，且必须点名要清的类别 ——
     不存在"一键清理全部"这种入口。
  3. **优先隔离，而非直接销毁**。默认把文件**移动**到隔离区（可 ``restore`` 还原），
     只有显式加 ``--purge`` 才是真删除。

为什么不做「一键清理」：
  那些工具之所以吓人，是因为把「判定」和「销毁」压缩成了一次点击，删错了回不来。
  这里两者分开：扫描只做判定（可复核、可存档、可带回家慢慢看），清理是另一次动作，
  中间还有隔离区兜底。代价是慢一步，换来的是删错了还能回来。

再叠一层硬约束：每个清理单元必须**严格位于该类别声明的根目录内部**。
类别根是代码里的硬编码白名单，这样即便 glob 写错、目录被换成符号链接指向 /etc，
也删不到允许范围之外的东西。**我们永远不删根目录本身，只删根里面的内容。**

对外接口：cmd_clean / add_clean_parser
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    C,
    OUTPUT_DIR,
    WINDOWS,
    ensure_output_dir,
    human_bytes,
    now_stamp,
    run,
    write_script,
)

SKILL_DIR = Path(__file__).resolve().parent.parent
CLEAN_DIR = OUTPUT_DIR / "cleanup"
QUARANTINE_DIR = OUTPUT_DIR / "quarantine"


# --------------------------------------------------------------------------
# 风险分级
# --------------------------------------------------------------------------

# SAFE   —— 纯垃圾，删了没有副作用，系统自己也会清
# LIKELY —— 建议清，代价是下次要重新下载/重建（缓存类）
# CAUTION —— 谨慎，可能影响功能或系统回滚能力，必须明确点名
RISK_ORDER = {"SAFE": 0, "LIKELY": 1, "CAUTION": 2}
RISK_LABEL = {
    "SAFE": "安全",
    "LIKELY": "建议",
    "CAUTION": "谨慎",
}
RISK_COLOR = {"SAFE": "#0d7a6f", "LIKELY": "#b45309", "CAUTION": "#b91c1c"}


# --------------------------------------------------------------------------
# 环境路径
# --------------------------------------------------------------------------

def _env(*names: str) -> Path | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return Path(v)
    return None


def _win_root() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows"))


def _home() -> Path:
    return Path.home()


# 永不进入扫描/清理的路径（它们本身就是挂载点或运行中的结构）
_WALK_SKIP = {
    "/proc", "/sys", "/dev", "/run", "/boot/efi",
}


# --------------------------------------------------------------------------
# 目录体积 —— 用 du 优先（快得多），失败退回 Python 遍历
# --------------------------------------------------------------------------

class _Budget(Exception):
    """扫描超时。宁可给不完整的结果并写明，也不要卡住用户。"""


def _du(path: Path, budget: float) -> tuple[int, int] | None:
    """用 du 统计 (字节, 文件数)。仅在非 Windows 且有 du 时可用。"""
    if WINDOWS or not shutil.which("du"):
        return None
    rc, out, _ = run(["du", "-s", "-x", "--block-size=1", str(path)],
                     timeout=max(2, int(budget)))
    if rc != 0 or not out.strip():
        return None
    try:
        size = int(out.strip().splitlines()[-1].split()[0])
    except (ValueError, IndexError):
        return None
    # du 不给文件数，文件数只在需要展示时粗略估算
    return size, 0


def _iter_children(root: Path):
    """列出 root 的直接子项，跳过符号链接 / 重解析点与读不到的项。

    跳过链接不只是为了快：一个指向 C:\\ 的 junction 会让遍历无限展开，
    也可能把清理范围带到白名单之外。
    """
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if WINDOWS:
                        try:
                            if e.stat(follow_symlinks=False).st_file_attributes & \
                                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                                continue
                        except OSError:
                            pass
                    yield Path(e.path)
                except OSError:
                    continue
    except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
        return


def _measure(path: Path, deadline: float) -> tuple[int, int]:
    """返回 (字节, 文件数)。超时抛 _Budget。"""
    if time.time() > deadline:
        raise _Budget()
    if path.is_file():
        try:
            st = path.stat()
            return st.st_size, 1
        except OSError:
            return 0, 0

    got = _du(path, deadline - time.time())
    if got is not None:
        return got

    total = 0
    files = 0
    seen: set = set()
    stack = [path]
    while stack:
        if time.time() > deadline:
            raise _Budget()
        cur = stack.pop()
        if cur.is_symlink():
            continue
        try:
            st = cur.stat()
        except OSError:
            continue
        key = (st.st_dev, st.st_ino)
        if st.st_ino and key in seen:
            continue
        seen.add(key)
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
            files += 1
        elif stat.S_ISDIR(st.st_mode):
            stack.extend(_iter_children(cur))
    return total, files


# --------------------------------------------------------------------------
# 类别定义 —— 数据驱动，加一条就是加一行
# --------------------------------------------------------------------------
#
# 字段说明：
#   roots      目录白名单；mode="content" 时清理其**直接子项**
#   files      具体文件白名单；mode="file" 时逐个判定
#   globs      (基准目录, 通配模式)；mode="glob" 时匹配到的文件逐个判定
#   cmd        命令型建议（不由本模块自动删除，除非 --allow-cmd 且在 SAFE_CMDS 内）
#   min_age    只清理"多少天没动过"的项，0 表示不限（正在被占用的东西别碰）
#   keep       在该类别的 glob/files 中保留最新的 N 个（如内核、日志轮转）
#
# 测试接缝，见 _specs()。生产路径下恒为 None。
_SPEC_HOOK: list[dict] | None = None

def _specs() -> list[dict]:
    """返回当前生效的类别定义。

    生产路径永远走 ``_default_specs()``。``_SPEC_HOOK`` 是给自检用的测试接缝：
    让 selftest 能塞一个临时类别进来，在沙箱目录里把「安全闸门 → 隔离 → 还原」
    整条链路真跑一遍 —— **绝不用真实文件来验证删除逻辑**。
    """
    if _SPEC_HOOK is not None:
        return _SPEC_HOOK
    return _default_specs()


def _default_specs() -> list[dict]:
    if WINDOWS:
        win = _win_root()
        la = _env("LOCALAPPDATA") or (_home() / "AppData" / "Local")
        ad = _env("APPDATA") or (_home() / "AppData" / "Roaming")
        tmp = _env("TEMP", "TMP") or (la / "Temp")
        return [
            # ---------------- SAFE ----------------
            dict(id="user_temp", title="用户临时文件",
                 risk="SAFE", mode="content", roots=[tmp], min_age=7,
                 note="只清 7 天以上没动过的，正在使用中的临时文件不会被碰。"),
            dict(id="sys_temp", title="系统临时文件",
                 risk="SAFE", mode="content", roots=[win / "Temp"], min_age=7),
            dict(id="thumb_cache", title="缩略图 / 图标缓存",
                 risk="SAFE", mode="glob", min_age=0,
                 globs=[(la / "Microsoft" / "Windows" / "Explorer",
                         ("thumbcache_*.db", "iconcache_*.db"))],
                 note="删掉后缩略图会重建，属于自愈型缓存。"),
            dict(id="recycle_bin", title="回收站",
                 risk="SAFE", mode="content", min_age=0,
                 roots=[Path("C:/$Recycle.Bin")],
                 note="回收站是「已删除」的最后一层，清空后无法用常规方式找回。"),
            # ---------------- LIKELY ----------------
            dict(id="win_update_cache", title="Windows 更新缓存",
                 risk="LIKELY", mode="content", min_age=3,
                 roots=[win / "SoftwareDistribution" / "Download"],
                 note="更新已完成则无用；若正好在补丁安装中，建议等重启后再清。"),
            dict(id="delivery_opt", title="传递优化缓存",
                 risk="LIKELY", mode="content", min_age=3,
                 roots=[win / "ServiceProfiles" / "NetworkService" / "AppData" /
                        "Local" / "Microsoft" / "Windows" / "DeliveryOptimization"]),
            dict(id="win_logs", title="系统日志缓存（CBS/DISM/更新）",
                 risk="LIKELY", mode="glob", min_age=14,
                 globs=[(win / "Logs", ("CBS/*.log", "DISM/*.log",
                                        "WindowsUpdate/*.etl", "*.etl"))],
                 note="日志是排障证据。保留最近 14 天，够用。"),
            dict(id="crash_dump", title="崩溃转储文件",
                 risk="LIKELY", mode="glob", min_age=14,
                 globs=[(win, ("MEMORY.DMP", "Minidump/*.dmp",
                               "LiveKernelReports/**/*.dmp"))],
                 note="如果你还在排查蓝屏，先别清 —— 这些 dump 是唯一的现场。"),
            dict(id="browser_cache", title="浏览器缓存",
                 risk="LIKELY", mode="content", min_age=2,
                 roots=[la / "Google" / "Chrome" / "User Data" / "Default" / "Cache",
                        la / "Google" / "Chrome" / "User Data" / "Default" /
                        "Code Cache",
                        la / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache",
                        la / "Microsoft" / "Edge" / "User Data" / "Default" /
                        "Code Cache"],
                 note="不含 Cookie 与登录态，清完不用重新登录。"),
            dict(id="dev_cache", title="开发工具缓存（pip/npm/yarn）",
                 risk="LIKELY", mode="content", min_age=14,
                 roots=[la / "pip" / "cache",
                        ad / "npm-cache",
                        la / "Yarn" / "Cache",
                        la / "Temp" / "npm-cache"],
                 note="清了下次装包要重新下载；离线环境别清。"),
            # ---------------- CAUTION ----------------
            dict(id="prefetch", title="预取文件（Prefetch）",
                 risk="CAUTION", mode="content", min_age=30,
                 roots=[win / "Prefetch"],
                 note="清了之后头几次启动会变慢，系统会自己重新积累。SSD 上收益有限。"),
            dict(id="windows_old", title="旧系统文件（Windows.old）",
                 risk="CAUTION", mode="content", min_age=0,
                 roots=[Path("C:/Windows.old")],
                 note="删掉就**再也无法回退到升级前的系统**。确认真不需要回滚再清。"),
            dict(id="winsxs", title="组件存储清理（WinSxS）",
                 risk="CAUTION", mode="cmd", cmd_kind="winsxs",
                 cmd="DISM /Online /Cleanup-Image /StartComponentCleanup /ResetBase",
                 note="必须用 DISM，手删 WinSxS 会把系统搞坏。ResetBase 后已装补丁无法卸载。"),
            dict(id="hiberfil", title="休眠文件（hiberfil.sys）",
                 risk="CAUTION", mode="cmd", cmd_kind="manual",
                 cmd="powercfg /hibernate off",
                 note="这不是垃圾 —— 关掉休眠会同时失去「快速启动」，开机变慢。"
                      "只在 C 盘实在紧张时考虑。"),
            dict(id="event_logs", title="Windows 事件日志",
                 risk="CAUTION", mode="cmd", cmd_kind="never",
                 cmd="wevtutil cl System / cl Application",
                 note="**这属于销毁证据**。正在做入侵排查或故障复盘时绝对不要执行；"
                      "平时也只是数字上的变小，收益极低。默认从清理清单里排除。"),
        ]

    home = _home()
    cache = home / ".cache"
    return [
        # ---------------- SAFE ----------------
        dict(id="user_temp", title="/tmp 临时文件",
             risk="SAFE", mode="content", roots=[Path("/tmp")], min_age=7,
             note="只清 7 天以上没动过的。注意：/tmp 常被挂成 tmpfs，清了不释放磁盘。"),
        dict(id="var_tmp", title="/var/tmp 临时文件",
             risk="SAFE", mode="content", roots=[Path("/var/tmp")], min_age=30,
             note="/var/tmp 是真占磁盘的，值得清。"),
        dict(id="pkg_cache", title="包管理器缓存",
             risk="SAFE", mode="content", min_age=0,
             roots=[Path(p) for p in (
                 "/var/cache/apt/archives", "/var/cache/yum",
                 "/var/cache/dnf", "/var/cache/zypp/packages",
                 "/var/cache/pacman/pkg")],
             note="已下载的安装包，用完即废；重装时会重新下载。"),
        dict(id="trash", title="用户回收站",
             risk="SAFE", mode="content", min_age=0,
             roots=[cache.parent / ".local" / "share" / "Trash" / "files"]),
        dict(id="core_dump", title="核心转储 / crash 文件",
             risk="SAFE", mode="glob", min_age=7,
             globs=[(Path("/var/lib/systemd/coredump"), ("*.zst", "*.lz4", "*.xz", "*")),
                    (Path("/var/crash"), ("*",)),
                    (Path("/tmp"), ("core.*",)),
                    (Path("/var/tmp"), ("core.*",))],
             note="core 文件动辄几百 MB 且几乎没人看。要排查崩溃就先留着。"),
        # ---------------- LIKELY ----------------
        dict(id="journal", title="systemd 日志（journal）",
             risk="LIKELY", mode="cmd", cmd_kind="journal",
             cmd="journalctl --vacuum-size=200M",
             measure=Path("/var/log/journal"),
             note="日志保留是有上限的，vacuum 到 200M 通常能回收数 GB，且保留最近记录。"),
        dict(id="old_kernels", title="旧内核与孤儿包",
             risk="LIKELY", mode="cmd", cmd_kind="autoremove",
             cmd="apt-get autoremove --purge -y   # 或 dnf remove --oldinstallonly",
             note="/boot 满导致装不上新内核是很常见的故障。至少保留当前 + 1 个可启动内核。"),
        dict(id="rotated_logs", title="已轮转的历史日志",
             risk="LIKELY", mode="glob", min_age=14,
             globs=[(Path("/var/log"), ("*.gz", "*.1", "*.2", "*.3", "*.4",
                                        "*.old", "*.bak", "*.xz"))],
             note="保留最近 14 天。审计期间别动 —— 这是排查的主要证据来源之一。"),
        dict(id="thumb_cache", title="缩略图缓存",
             risk="LIKELY", mode="content", min_age=7,
                 roots=[cache / "thumbnails"]),
        dict(id="dev_cache", title="开发工具缓存",
             risk="LIKELY", mode="content", min_age=14,
             roots=[cache / "pip", home / ".npm" / "_cacache",
                    home / ".yarn" / "cache", home / "go" / "pkg" / "mod" / "cache"]),
        dict(id="user_cache", title="用户缓存目录（~/.cache）",
             risk="CAUTION", mode="content", min_age=14,
             roots=[cache],
             note="~/.cache 里什么都有，范围大、收益也大。清之前建议先看清单。"),
        # ---------------- CAUTION ----------------
        dict(id="docker", title="Docker 未使用数据",
             risk="CAUTION", mode="cmd", cmd_kind="docker",
             cmd="docker system prune -af",
             note="会删掉所有已停止容器、未使用镜像与构建缓存。**确认没有现场要保留再执行**。"),
        dict(id="snap_old", title="Snap 旧版本",
             risk="CAUTION", mode="cmd", cmd_kind="snap",
             cmd="snap list --all | awk '/disabled/{print $1, $3}' | "
                 "while read n r; do snap remove \"$n\" --revision=\"$r\"; done",
             note="禁用版本堆积很占空间，但回滚时会用到。"),
        dict(id="audit_logs", title="审计日志（auditd）",
             risk="CAUTION", mode="cmd", cmd_kind="never",
             cmd="find /var/log/audit -name 'audit.log.*' -delete",
             note="**销毁安全证据**。等保/审计合规期间不要执行。默认排除。"),
    ]


# 允许 --allow-cmd 自动执行的"命令型"类别（都是幂等且只影响缓存的）
SAFE_CMDS = {"journal", "autoremove"}


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class Unit:
    """一个清理单元 —— 要么是一个真实路径，要么是一条待执行的建议命令。"""

    category: str
    title: str
    risk: str
    path: str = ""
    size: int = 0
    files: int = 0
    mtime: float = 0.0
    kind: str = "path"          # path | cmd
    cmd: str = ""
    cmd_kind: str = ""
    note: str = ""
    partial: bool = False       # 统计超时，体积是下限而非精确值

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d

    @property
    def age_days(self) -> int:
        if not self.mtime:
            return 0
        return int((time.time() - self.mtime) // 86400)


@dataclass
class Scan:
    mode: str = "clean-scan"
    started: str = ""
    duration_s: float = 0.0
    platform: str = ""
    disks: list = field(default_factory=list)
    units: list = field(default_factory=list)
    big_files: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # 读不到 / 跳过的项，如实列出

    # ---- 统计 ----
    def by_category(self) -> dict:
        out: dict[str, dict] = {}
        for u in self.units:
            d = out.setdefault(u.category, {
                "category": u.category, "title": u.title, "risk": u.risk,
                "count": 0, "size": 0, "partial": False,
                "cmd": "", "note": u.note, "suggest_only": u.kind == "cmd",
            })
            d["count"] += 1
            d["size"] += u.size
            d["partial"] = d["partial"] or u.partial
            if u.cmd and not d["cmd"]:
                d["cmd"] = u.cmd
        return out

    def risk_totals(self) -> dict:
        out = {k: {"size": 0, "count": 0} for k in RISK_ORDER}
        for u in self.units:
            if u.kind != "path":
                continue
            out.setdefault(u.risk, {"size": 0, "count": 0})
            out[u.risk]["size"] += u.size
            out[u.risk]["count"] += 1
        return out

    def reclaimable(self) -> int:
        """可分级的可回收总量（不含"仅建议"的命令型）。"""
        return sum(u.size for u in self.units if u.kind == "path")

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "started": self.started,
            "duration_s": round(self.duration_s, 1),
            "platform": self.platform, "disks": self.disks,
            "reclaimable": self.reclaimable(),
            "risk_totals": self.risk_totals(),
            "categories": list(self.by_category().values()),
            "units": [u.to_dict() for u in self.units],
            "big_files": self.big_files,
            "duplicates": self.duplicates,
            "notes": self.notes, "skipped": self.skipped,
        }


# --------------------------------------------------------------------------
# 磁盘使用情况
# --------------------------------------------------------------------------

def disk_report() -> list[dict]:
    rows = []
    if WINDOWS:
        import string
        import ctypes

        drives = []
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << i):
                    drives.append(f"{letter}:\\")
        except Exception:  # noqa: BLE001
            drives = []
        for d in drives:
            try:
                u = shutil.disk_usage(d)
            except OSError:
                continue
            if u.total == 0:
                continue
            rows.append({"mount": d, "total": u.total, "used": u.used,
                         "free": u.free, "percent": round(u.used * 100 / u.total, 1)})
        return rows

    rc, out, _ = run(["df", "-P", "-k"], timeout=15)
    if rc != 0:
        u = shutil.disk_usage("/")
        return [{"mount": "/", "total": u.total, "used": u.used,
                 "free": u.free, "percent": round(u.used * 100 / u.total, 1)}]
    for i, ln in enumerate(out.splitlines()):
        if i == 0 or not ln.strip():
            continue
        parts = ln.split()
        if len(parts) < 6:
            continue
        try:
            total = int(parts[1]) * 1024
            used = int(parts[2]) * 1024
            free = int(parts[3]) * 1024
        except ValueError:
            continue
        if total <= 0:
            continue
        mount = parts[5]
        if mount.startswith(("/proc", "/sys", "/dev", "/run")):
            continue
        rows.append({"mount": mount, "total": total, "used": used,
                     "free": free, "percent": round(used * 100 / total, 1)})
    rows.sort(key=lambda r: -r["percent"])
    return rows


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

def _collect_roots(spec: dict) -> list[Path]:
    return [Path(p) for p in spec.get("roots", [])]


def _glob_files(spec: dict, deadline: float) -> list[Path]:
    out: list[Path] = []
    for base, pats in spec.get("globs", []):
        base = Path(base)
        if not base.is_dir():
            continue
        for pat in pats:
            try:
                for p in base.glob(pat):
                    if p.is_file() and not p.is_symlink():
                        out.append(p)
            except (OSError, ValueError):
                continue
            if time.time() > deadline:
                break
    return out


def _scan_category(spec: dict, budget: float, log=None) -> tuple[list[Unit], list[str]]:
    """扫描单个类别，返回 (单元列表, 跳过说明)。"""
    units: list[Unit] = []
    skipped: list[str] = []
    min_age = spec.get("min_age", 0)
    cutoff = time.time() - min_age * 86400 if min_age else 0.0
    mode = spec.get("mode", "content")
    deadline = time.time() + budget

    if mode == "cmd":
        size = 0
        mp = spec.get("measure")
        if mp and Path(mp).exists():
            try:
                size, _ = _measure(Path(mp), min(deadline, time.time() + 20))
            except _Budget:
                skipped.append(f"{spec['id']}: 统计 {mp} 体积超时，实际可能更大")
        u = Unit(category=spec["id"], title=spec["title"], risk=spec["risk"],
                 kind="cmd", cmd=spec.get("cmd", ""),
                 cmd_kind=spec.get("cmd_kind", "manual"),
                 size=size, note=spec.get("note", ""))
        return [u], skipped

    if mode == "glob":
        files = _glob_files(spec, deadline)
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            if cutoff and st.st_mtime > cutoff:
                continue
            units.append(Unit(category=spec["id"], title=spec["title"],
                              risk=spec["risk"], path=str(f), size=st.st_size,
                              files=1, mtime=st.st_mtime,
                              note=spec.get("note", "")))
        return units, skipped

    # content 模式：根目录的直接子项才是清理单元
    for root in _collect_roots(spec):
        if not root.is_dir():
            continue
        if str(root).rstrip("\\/") in _WALK_SKIP:
            continue
        if log:
            log(f"  · 扫描 {spec['title']}：{root}")
        for child in _iter_children(root):
            if time.time() > deadline:
                skipped.append(f"{spec['id']}: 扫描 {root} 超出时间预算，清单可能不完整")
                break
            try:
                st = child.stat()
            except OSError:
                continue
            if cutoff and st.st_mtime > cutoff:
                continue
            partial = False
            try:
                size, files = _measure(child, min(deadline, time.time() + 30))
            except _Budget:
                size, files, partial = 0, 0, True
                skipped.append(f"{spec['id']}: {child} 统计超时，体积未知（按 0 计）")
            units.append(Unit(
                category=spec["id"], title=spec["title"], risk=spec["risk"],
                path=str(child), size=size, files=files,
                mtime=st.st_mtime, note=spec.get("note", ""), partial=partial))
    return units, skipped


def _walk(root: Path, deadline: float, max_depth: int = 5):
    """受限遍历：跳过链接与重解析点，限制深度，超时即止。

    为什么要限深度：在机械盘上全盘递归会把磁盘队列打满，用户会明显感到"电脑卡了"。
    而大文件/重复文件通常藏在 Downloads、Desktop、Videos、node_modules 这类浅层位置，
    深度 5 已经能覆盖绝大多数情况。宁可扫得浅一点，也不要为了完整性把机器拖垮。
    """
    stack = [(root, 0)]
    while stack:
        if time.time() > deadline:
            return
        cur, d = stack.pop()
        for ch in _iter_children(cur):
            try:
                if ch.is_dir():
                    if d + 1 <= max_depth:
                        stack.append((ch, d + 1))
                    continue
                yield ch
            except OSError:
                continue


def scan_big_files(roots: list[Path], min_size: int, top: int,
                   budget: float = 60.0, max_depth: int = 5) -> tuple[list[dict], list[str]]:
    """找工作目录里的大文件 —— 只报告，永不自动清理。"""
    found: list[dict] = []
    skipped: list[str] = []
    deadline = time.time() + budget
    for root in roots:
        if not root.exists():
            continue
        for ch in _walk(root, deadline, max_depth):
            try:
                st = ch.stat()
            except OSError:
                continue
            if st.st_size >= min_size:
                found.append({"path": str(ch), "size": st.st_size, "mtime": st.st_mtime})
        if time.time() > deadline:
            skipped.append(f"大文件扫描达到时间上限（{root}，深度限 {max_depth}），"
                           f"结果不完整 —— 如需更全请加大 --big-budget 或缩小范围")
    found.sort(key=lambda x: -x["size"])
    return found[:top], skipped


def scan_duplicates(roots: list[Path], min_size: int = 10 * 1024 * 1024,
                    budget: float = 60.0, max_depth: int = 5
                    ) -> tuple[list[dict], list[str]]:
    """重复文件 —— 先按大小分组，再比头部 64KB。

    只报告。删重复文件是**用户自己的决定**：同名同内容的两个文件，
    很可能一个是"原件"一个是"备份"，工具没资格替你选。
    """
    import hashlib

    by_size: dict[int, list[Path]] = {}
    deadline = time.time() + budget
    skipped: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for ch in _walk(root, deadline, max_depth):
            try:
                st = ch.stat()
            except OSError:
                continue
            if st.st_size >= min_size:
                by_size.setdefault(st.st_size, []).append(ch)
        if time.time() > deadline:
            skipped.append(f"重复文件扫描达到时间上限（{root}），结果不完整")
    groups = []
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        buckets: dict[str, list[Path]] = {}
        for f in files:
            try:
                with f.open("rb") as fh:
                    head = hashlib.sha256(fh.read(65536)).hexdigest()
            except OSError:
                continue
            buckets.setdefault(head, []).append(f)
        for fl in buckets.values():
            if len(fl) < 2:
                continue
            groups.append({
                "size": size, "count": len(fl),
                "wasted": size * (len(fl) - 1),
                "paths": [str(x) for x in fl],
            })
    groups.sort(key=lambda g: -g["wasted"])
    return groups[:50], skipped


def build_scan(big: bool = False, dup: bool = False, deep: bool = False,
               min_size: int = 200 * 1024 * 1024, top: int = 40,
               budget: float = 25.0, big_roots: list[Path] | None = None,
               big_budget: float = 90.0, max_depth: int = 5,
               log=None) -> Scan:
    t0 = time.time()
    sc = Scan(started=time.strftime("%Y-%m-%d %H:%M:%S"),
              platform=sys.platform)
    sc.disks = disk_report()
    specs = _specs()
    if not deep:
        # 浅扫：跳过需要遍历大量小文件的类别（fast 模式给的是"能立刻拿到的"）
        pass
    for spec in specs:
        if log:
            log(f"  · 类别 {spec['title']}（{RISK_LABEL.get(spec['risk'], spec['risk'])}）")
        try:
            units, skipped = _scan_category(spec, budget, log=log)
        except Exception as e:  # noqa: BLE001 - 扫描绝不能让整体崩掉
            sc.skipped.append(f"{spec['id']}: 扫描异常 {type(e).__name__}: {e}")
            continue
        sc.units.extend(units)
        sc.skipped.extend(skipped)

    if big:
        roots = big_roots or (
            [Path(_home())] if WINDOWS else [Path("/var"), Path("/home"), Path("/tmp")]
        )
        if log:
            log(f"  · 大文件扫描（阈值 {human_bytes(min_size)}，最多下钻 {max_depth} 层）")
        bf, sk = scan_big_files(roots, min_size, top, budget=big_budget,
                                max_depth=max_depth)
        sc.big_files = bf
        sc.skipped.extend(sk)

    if dup:
        roots = big_roots or ([_home()] if WINDOWS else [_home(), Path("/var")])
        if log:
            log("  · 重复文件检测（按大小分组 + 头部哈希）")
        dp, sk = scan_duplicates(roots, min_size=max(10 * 1024 * 1024, min_size // 10),
                                 budget=big_budget, max_depth=max_depth)
        sc.duplicates = dp
        sc.skipped.extend(sk)

    sc.notes.append("扫描全程只读，未移动/删除任何文件。")
    sc.duration_s = time.time() - t0
    return sc


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render_scan_text(sc: Scan, max_rows: int = 40) -> str:
    L: list[str] = []
    L.append("=" * 74)
    L.append("  硬盘瘦身 · 可回收清单（只读扫描，未改动任何文件）")
    L.append("=" * 74)
    L.append(f"  时间 {sc.started}    耗时 {sc.duration_s:.1f}s    平台 {sc.platform}")
    L.append("")

    if sc.disks:
        L.append("  磁盘使用")
        L.append(f"    {'挂载点':<14}{'总量':>10}{'已用':>10}{'可用':>10}{'使用率':>8}")
        for d in sc.disks[:8]:
            bar = "█" * int(min(20, d["percent"] / 5))
            flag = "  ← 偏紧" if d["percent"] >= 85 else ""
            L.append(f"    {d['mount']:<14}{human_bytes(d['total']):>10}"
                     f"{human_bytes(d['used']):>10}{human_bytes(d['free']):>10}"
                     f"{d['percent']:>7.1f}% {bar}{flag}")
        L.append("")

    cats = sc.by_category()
    tot = sc.reclaimable()
    L.append(f"  合计可回收：{human_bytes(tot)}")
    L.append("")

    for risk in ("SAFE", "LIKELY", "CAUTION"):
        rows = [c for c in cats.values() if c["risk"] == risk and c["count"]]
        if not rows:
            continue
        rows.sort(key=lambda c: -c["size"])
        sub = sum(c["size"] for c in rows)
        L.append(f"  【{RISK_LABEL[risk]}】{human_bytes(sub)}")
        for c in rows:
            mark = "  建议命令" if c["suggest_only"] else ""
            L.append(f"    {c['title']:<28}{human_bytes(c['size']):>10}"
                     f"   {c['count']:>3} 项{mark}")
            if c["note"]:
                L.append(f"        {C.gray(c['note'])}")
            if c["suggest_only"] and c["cmd"]:
                L.append(f"        {C.gray('$ ' + c['cmd'].splitlines()[0])}")
        L.append("")

    if sc.big_files:
        L.append(f"  大文件 Top {len(sc.big_files)}（仅供参考，请自行判断可否移走）")
        for b in sc.big_files[:max_rows]:
            L.append(f"    {human_bytes(b['size']):>10}  {b['path']}")
        L.append("")

    if sc.duplicates:
        L.append("  疑似重复文件（仅供参考，删除权在你）")
        for g in sc.duplicates[:20]:
            L.append(f"    {human_bytes(g['size'])} × {g['count']} 份，"
                     f"冗余 {human_bytes(g['wasted'])}")
            for p in g["paths"][:4]:
                L.append(f"        {p}")
        L.append("")

    if sc.skipped:
        L.append("  未能覆盖（这不是「没问题」，是「没看到」）")
        for s in sc.skipped[:30]:
            L.append(f"    · {s}")
        L.append("")

    L.append("  下一步：")
    L.append("    · 看某类别明细： cli.py clean show --category thumb_cache")
    L.append("    · 生成清理计划： cli.py clean plan --categories thumb_cache,win_logs")
    L.append("    · 执行（隔离优先）： cli.py clean clean --scan <文件> "
             "--categories a,b --apply --yes")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 计划
# --------------------------------------------------------------------------

def plan_clean(sc: Scan, categories: list[str]) -> list[Unit]:
    cats = set(categories)
    return [u for u in sc.units if u.category in cats and u.kind == "path"]


# --------------------------------------------------------------------------
# 隔离区
# --------------------------------------------------------------------------

def _pick_quarantine(units: list[Unit], override: str | None = None) -> Path:
    """选隔离区位置。优先同盘 —— 同盘 move 是改目录项，瞬间完成；
    跨盘 move 是完整拷贝，几十 GB 会等到天荒地老。"""
    if override:
        return Path(override)
    drives = set()
    for u in units:
        if not u.path:
            continue
        if WINDOWS:
            d = Path(u.path).drive
            drives.add(d.upper() if d else "?")
        else:
            drives.add("/")
    stamp = now_stamp()
    if WINDOWS and len(drives) == 1 and drives != {"?"}:
        cand = Path(f"{drives.pop()}\\.ops-quarantine\\{stamp}")
    elif not WINDOWS and len(drives) == 1:
        cand = Path("/.ops-quarantine") / stamp
    else:
        cand = QUARANTINE_DIR / stamp
    # 确认可写，否则退回 outputs
    try:
        cand.mkdir(parents=True, exist_ok=True)
        probe = cand / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return cand
    except Exception:  # noqa: BLE001
        fallback = QUARANTINE_DIR / stamp
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _safe_rel(path: Path) -> Path:
    p = str(path)
    if WINDOWS and len(p) > 1 and p[1] == ":":
        p = p[0] + p[2:].replace("\\", "/")
    p = p.lstrip("/").replace(":", "_")
    return Path(p)


def _guard(unit: Unit, spec: dict) -> str:
    """返回拒绝理由；空字符串表示放行。"""
    p = Path(unit.path)
    mode = spec.get("mode", "content")
    try:
        rp = Path(os.path.realpath(p))
    except OSError:
        return "无法解析真实路径"

    if mode == "glob":
        allowed = [Path(os.path.realpath(b)) for b, _ in spec.get("globs", [])
                   if Path(b).is_dir()]
        pats = [pat for _, ps in spec.get("globs", []) for pat in ps]
        if not _within(rp, allowed):
            return "不在该类别声明的目录内"
        import fnmatch
        if not any(fnmatch.fnmatch(rp.name, pat) or
                   fnmatch.fnmatch(str(rp), pat) for pat in pats):
            return "文件名不匹配该类别模式"
        return ""

    roots = [Path(os.path.realpath(r)) for r in _collect_roots(spec)]
    if not roots:
        return "该类别无有效根目录"
    # 关键不变量：只能删根**内部**的东西，永远不删根本身
    if any(rp == r for r in roots):
        return "拒绝：这是类别根目录本身"
    if Path(rp.anchor) == rp:
        return "拒绝：这是驱动器/挂载点根目录"
    if not _within(rp, roots):
        return "不在该类别声明的根目录内"
    return ""


def _within(path: Path, roots: list[Path]) -> bool:
    for r in roots:
        try:
            if path == r or r in path.parents:
                return True
        except (OSError, ValueError):
            continue
    return False


# --------------------------------------------------------------------------
# 执行清理
# --------------------------------------------------------------------------

def clean_units(units: list[Unit], purge: bool = False, dry_run: bool = False,
                quarantine: Path | None = None, allow_cmd: bool = False,
                log=None) -> dict:
    specs = {s["id"]: s for s in _specs()}
    res = {
        "ok": True, "mode": "purge" if purge else "quarantine",
        "dry_run": dry_run,
        "quarantine": str(quarantine) if quarantine and not purge else "",
        "items": [], "freed": 0, "removed": 0,
        "failed": [], "refused": [], "commands": [],
    }
    qroot = None
    if not purge and not dry_run:
        qroot = quarantine or _pick_quarantine(units)
        qroot.mkdir(parents=True, exist_ok=True)
        (qroot / "items").mkdir(exist_ok=True)

    for u in units:
        spec = specs.get(u.category, {})
        if u.kind == "cmd":
            res["commands"].append({"category": u.category, "cmd": u.cmd,
                                    "kind": u.cmd_kind})
            continue

        why = _guard(u, spec)
        if why:
            res["refused"].append({"path": u.path, "reason": why})
            continue

        src = Path(u.path)
        if dry_run:
            res["items"].append({"path": str(src), "size": u.size,
                                 "action": "would-" + ("delete" if purge else "quarantine")})
            res["freed"] += u.size
            continue

        try:
            if purge:
                if src.is_dir() and not src.is_symlink():
                    shutil.rmtree(src, ignore_errors=False)
                else:
                    src.unlink()
                act = "deleted"
            else:
                dst = qroot / "items" / _safe_rel(src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst = dst.with_name(dst.name + f".{int(time.time())}")
                shutil.move(str(src), str(dst))
                act = "quarantined"
            res["items"].append({"path": str(src), "size": u.size, "action": act,
                                 "dst": str(dst) if not purge else ""})
            res["freed"] += u.size
            res["removed"] += 1
            if log and res["removed"] % 25 == 0:
                log(f"    · 已处理 {res['removed']} 项 / {human_bytes(res['freed'])}")
        except Exception as e:  # noqa: BLE001
            res["failed"].append({
                "path": str(src), "size": u.size,
                "reason": f"{type(e).__name__}: {e}",
            })
            # 第一条失败就停手 —— 继续硬删可能是在错误的前提下推进
            if log:
                log(C.red(f"    ✗ {src} —— {type(e).__name__}: {e}"))
            res["ok"] = False
            res["stopped_early"] = True
            break

    if qroot and not dry_run:
        _write_quarantine_manifest(qroot, res)
    return res


def _write_quarantine_manifest(qroot: Path, res: dict) -> None:
    items = [{"orig": i["path"], "quarantine": i.get("dst", ""), "size": i["size"]}
             for i in res["items"] if i.get("action") == "quarantined"]
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "quarantine_dir": str(qroot),
        "item_count": len(items),
        "total_bytes": sum(i["size"] for i in items),
        "items": items,
    }
    (qroot / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 还原脚本 —— 隔离区里没有"还原脚本"就等于没有隔离区的意义
    ps = ["# 从隔离区还原（服务器安全维护、杀木马病毒skill 生成）",
          f"# 隔离区：{qroot}",
          "$ErrorActionPreference = 'Continue'",
          "$manifest = Get-Content -Raw -Encoding UTF8 "
          f"-LiteralPath '{qroot / 'manifest.json'}' | ConvertFrom-Json",
          "$n = 0",
          "foreach ($it in $manifest.items) {",
          "    if (-not $it.quarantine) { continue }",
          "    if (-not (Test-Path -LiteralPath $it.quarantine)) { "
          "Write-Host ('缺失: ' + $it.orig); continue }",
          "    $dir = Split-Path -Parent $it.orig",
          "    if (-not (Test-Path -LiteralPath $dir)) { "
          "New-Item -ItemType Directory -Force -Path $dir | Out-Null }",
          "    Move-Item -LiteralPath $it.quarantine -Destination $it.orig -Force",
          "    $n++",
          "}",
          "Write-Host ('已还原 ' + $n + ' 项')"]
    write_script(qroot / "restore.ps1", "\n".join(ps) + "\n")

    sh = ["#!/bin/sh",
          "# 从隔离区还原（服务器安全维护、杀木马病毒skill 生成）",
          f"# 隔离区：{qroot}",
          "set -u",
          f"QD='{qroot}'",
          'if [ ! -f "$QD/manifest.json" ]; then echo "找不到 manifest"; exit 1; fi',
          "python3 - \"$QD\" <<'PY'",
          "import json, os, shutil, sys",
          "q = sys.argv[1]",
          "m = json.load(open(os.path.join(q, 'manifest.json'), encoding='utf-8'))",
          "n = 0",
          "for it in m['items']:",
          "    src, dst = it.get('quarantine'), it['orig']",
          "    if not src or not os.path.exists(src):",
          "        print('缺失:', dst); continue",
          "    os.makedirs(os.path.dirname(dst), exist_ok=True)",
          "    shutil.move(src, dst)",
          "    n += 1",
          "print('已还原', n, '项')",
          "PY"]
    write_script(qroot / "restore.sh", "\n".join(sh) + "\n")


def list_quarantine() -> list[dict]:
    out = []
    roots = [QUARANTINE_DIR]
    if WINDOWS:
        import string
        for letter in string.ascii_uppercase:
            p = Path(f"{letter}:\\.ops-quarantine")
            if p.is_dir():
                roots.append(p)
    else:
        p = Path("/.ops-quarantine")
        if p.is_dir():
            roots.append(p)
    for r in roots:
        if not r.is_dir():
            continue
        for d in sorted(r.iterdir(), reverse=True):
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            out.append({"batch": d.name, "dir": str(d),
                        "created": m.get("created", ""),
                        "count": m.get("item_count", 0),
                        "size": m.get("total_bytes", 0)})
    out.sort(key=lambda x: x["created"], reverse=True)
    return out


def restore_batch(batch_dir: str) -> dict:
    d = Path(batch_dir)
    mf = d / "manifest.json" if d.is_dir() else Path(batch_dir)
    if mf.is_dir():
        mf = mf / "manifest.json"
    if not mf.is_file():
        return {"ok": False, "error": f"找不到清单：{mf}"}
    m = json.loads(mf.read_text(encoding="utf-8"))
    ok = 0
    failed = []
    for it in m.get("items", []):
        src, dst = it.get("quarantine"), it.get("orig")
        if not src or not Path(src).exists():
            failed.append({"path": dst, "reason": "隔离副本不存在"})
            continue
        try:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"path": dst, "reason": f"{type(e).__name__}: {e}"})
    return {"ok": not failed, "restored": ok, "failed": failed}


def purge_quarantine(batch_dir: str | None = None) -> dict:
    """彻底销毁隔离区内容 —— 这是**不可逆**的一步。"""
    targets = [Path(batch_dir)] if batch_dir else \
        [Path(x["dir"]) for x in list_quarantine()]
    freed = 0
    for t in targets:
        if not t.is_dir():
            continue
        try:
            freed += sum(f.stat().st_size for f in t.rglob("*") if f.is_file())
            shutil.rmtree(t)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "purged": len(targets), "freed": freed}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _parse_categories(text: str) -> list[str]:
    """把 ``a,b, c`` 解析成 ['a','b','c']。

    注意别再写成直接迭代字符串 —— 那样 '--categories thumb_cache' 会被拆成
    一个一个字符，然后判定成"没有可清理的单元"，看起来像"没东西可清"，
    实际是类别名根本没匹配上。这种静默失效比报错危险得多。
    """
    return [c.strip() for c in (text or "").split(",") if c.strip()]


def _load_scan(path: str) -> Scan:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    sc = Scan(mode=raw.get("mode", "clean-scan"), started=raw.get("started", ""),
              duration_s=raw.get("duration_s", 0.0),
              platform=raw.get("platform", ""), disks=raw.get("disks", []),
              big_files=raw.get("big_files", []),
              duplicates=raw.get("duplicates", []),
              notes=raw.get("notes", []), skipped=raw.get("skipped", []))
    for d in raw.get("units", []):
        d.pop("age_days", None)
        fields = set(Unit.__dataclass_fields__)
        sc.units.append(Unit(**{k: v for k, v in d.items() if k in fields}))
    return sc


def _newest_scan() -> Path | None:
    if not CLEAN_DIR.is_dir():
        return None
    fs = sorted(CLEAN_DIR.glob("scan-*.json"), reverse=True)
    return fs[0] if fs else None


def cmd_clean(args) -> int:
    act = args.action

    # ---------------- scan ----------------
    if act == "scan":
        print(C.bold("硬盘瘦身 · 只读扫描"))
        print(C.gray("  只做判定，不移动、不删除任何文件。"))
        print()
        sc = build_scan(big=args.big, dup=args.dup, deep=args.deep,
                        min_size=args.min_size, top=args.top,
                        budget=args.budget, big_budget=args.big_budget,
                        max_depth=args.depth,
                        big_roots=[Path(p) for p in args.path] if args.path else None,
                        log=lambda s: print(C.gray(s)) if not args.quiet else None)
        ensure_output_dir()
        CLEAN_DIR.mkdir(parents=True, exist_ok=True)
        jp = CLEAN_DIR / f"scan-{now_stamp()}.json"
        jp.write_text(json.dumps(sc.to_dict(), ensure_ascii=False, indent=2),
                      encoding="utf-8")
        tp = CLEAN_DIR / f"scan-{now_stamp()}.txt"
        text = render_scan_text(sc)
        tp.write_text(text, encoding="utf-8")
        print()
        print(text)
        print(C.gray(f"  清单 JSON：{jp}"))
        print(C.gray(f"  清单文本：{tp}"))
        if args.json:
            print(json.dumps({"json": str(jp), "text": str(tp),
                              "reclaimable": sc.reclaimable(),
                              "risk_totals": sc.risk_totals()},
                             ensure_ascii=False))
        return 0

    # ---------------- show ----------------
    if act == "show":
        src = args.scan or _newest_scan()
        if not src:
            print(C.orange("  没有可用的扫描结果，先跑 clean scan。"))
            return 1
        sc = _load_scan(str(src))
        units = [u for u in sc.units if not args.category or u.category == args.category]
        if not args.category:
            print(C.gray("  未指定 --category，显示全部单元。"))
        units.sort(key=lambda u: -u.size)
        print(f"  {'类别':<20}{'大小':>10}{'文件':>8}  路径")
        for u in units[: args.max_show]:
            kind = "(建议命令)" if u.kind == "cmd" else ""
            print(f"  {u.category:<20}{human_bytes(u.size):>10}{u.files:>8}  "
                  f"{u.path or u.cmd} {kind}")
        if len(units) > args.max_show:
            print(C.gray(f"  ... 其余 {len(units) - args.max_show} 项（用 --max-show 调整）"))
        return 0

    # ---------------- plan ----------------
    if act == "plan":
        src = args.scan or _newest_scan()
        if not src:
            print(C.orange("  没有可用的扫描结果，先跑 clean scan。"))
            return 1
        sc = _load_scan(str(src))
        cats = _parse_categories(args.categories)
        if not cats:
            print(C.orange("  必须用 --categories 点名要清理的类别 —— "
                           "本工具没有「一键清全部」。"))
            print(C.gray("  可用类别："))
            for c in sorted(sc.by_category().values(), key=lambda x: -x["size"]):
                print(f"    {c['category']:<20}{human_bytes(c['size']):>10}"
                      f"  {RISK_LABEL.get(c['risk'], c['risk'])}  {c['title']}")
            return 1
        units = plan_clean(sc, cats)
        total = sum(u.size for u in units)
        print(C.bold(f"清理计划 · {len(units)} 项 / {human_bytes(total)}"))
        print()
        by_cat: dict[str, list[Unit]] = {}
        for u in units:
            by_cat.setdefault(u.category, []).append(u)
        for cid, us in sorted(by_cat.items(), key=lambda kv: -sum(u.size for u in kv[1])):
            spec = next((s for s in _specs() if s["id"] == cid), {})
            print(f"  【{cid}】{spec.get('title', cid)} "
                  f"（{RISK_LABEL.get(spec.get('risk', ''), '')}）"
                  f" {len(us)} 项 / {human_bytes(sum(u.size for u in us))}")
            for u in sorted(us, key=lambda x: -x.size)[: args.max_show]:
                print(f"      {human_bytes(u.size):>10}  {u.path}")
            if len(us) > args.max_show:
                print(C.gray(f"      ... 其余 {len(us) - args.max_show} 项"))
            if spec.get("note"):
                print(C.gray(f"      注意：{spec['note']}"))
            print()
        print(C.gray("  确认无误后执行："))
        print(C.gray(f"    cli.py clean clean --scan \"{src}\" "
                     f"--categories {','.join(cats)} --apply --yes"))
        print(C.gray("  默认进隔离区（可 restore 还原）；要真删再加 --purge。"))
        return 0

    # ---------------- clean ----------------
    if act == "clean":
        src = args.scan or _newest_scan()
        if not src:
            print(C.orange("  没有可用的扫描结果，先跑 clean scan。"))
            return 1
        sc = _load_scan(str(src))
        cats = _parse_categories(args.categories)
        if not cats:
            print(C.red("  拒绝执行：必须用 --categories 点名类别。"))
            return 3
        units = plan_clean(sc, cats)
        if not units:
            print(C.orange("  这些类别下没有可清理的单元。"))
            return 1
        total = sum(u.size for u in units)

        caution = [u for u in units
                   if u.risk == "CAUTION"]
        if caution and not args.allow_caution:
            print(C.red(f"  拒绝执行：选中的类别里有 {len(caution)} 项属于「谨慎」级，"
                        f"合计 {human_bytes(sum(u.size for u in caution))}。"))
            print(C.gray("  请逐条确认后加 --allow-caution。清单："))
            for u in caution[:15]:
                print(f"      {human_bytes(u.size):>10}  {u.path or u.cmd}")
            return 3

        print(C.bold("硬盘瘦身 · 清理"))
        print(f"  单元 {len(units)} 个 / {human_bytes(total)}")
        print(f"  方式：{'直接删除（不可逆）' if args.purge else '移入隔离区（可还原）'}")
        print()

        if not args.apply or not args.yes:
            print(C.gray("  当前为演练模式（未加 --apply --yes），只列出将处理的内容："))
            for u in sorted(units, key=lambda x: -x.size)[: args.max_show]:
                print(f"      {human_bytes(u.size):>10}  {u.path}")
            if len(units) > args.max_show:
                print(C.gray(f"      ... 其余 {len(units) - args.max_show} 项"))
            print()
            print(C.orange("  未做任何改动。确认后加 --apply --yes 执行。"))
            return 3 if not args.yes else 0

        before = {d["mount"]: d["free"] for d in disk_report()}
        res = clean_units(units, purge=args.purge, dry_run=False,
                          quarantine=Path(args.to) if args.to else None,
                          log=lambda s: print(s))
        after = {d["mount"]: d["free"] for d in disk_report()}

        print()
        print(C.green(f"  ✓ 已处理 {res['removed']} 项，释放 {human_bytes(res['freed'])}"))
        for m, f in before.items():
            if m in after and after[m] > f:
                print(C.gray(f"    实测 {m} 可用空间 {human_bytes(f)} → "
                             f"{human_bytes(after[m])}（+{human_bytes(after[m] - f)}）"))
        if res["quarantine"]:
            print(f"  隔离区：{res['quarantine']}")
            print(C.gray("    · 确认一切正常后再销毁： cli.py clean purge --batch <目录> --yes"))
            print(C.gray("    · 反悔了要还原：       cli.py clean restore --batch <目录>"))
        if res["refused"]:
            print(C.orange(f"  ⚠ 安全闸门拦下 {len(res['refused'])} 项（不在声明根目录内）："))
            for r in res["refused"][:10]:
                print(f"      {r['path']} —— {r['reason']}")
        if res["failed"]:
            print(C.red(f"  ✗ {len(res['failed'])} 项失败（已在该处停止，后续未执行）："))
            for f in res["failed"][:10]:
                print(f"      {f['path']} —— {f['reason']}")
        if res.get("commands"):
            print()
            print(C.bold("  以下属于命令型清理，需你确认后手动执行（或加 --allow-cmd）："))
            for c in res["commands"]:
                tag = {"manual": "建议手动", "never": "默认不建议",
                       "journal": "可自动", "autoremove": "可自动",
                       "docker": "建议手动", "snap": "建议手动",
                       "winsxs": "建议手动"}.get(c["kind"], "")
                print(f"      [{c['category']}] {tag}")
                print(f"        $ {c['cmd']}")
            if args.allow_cmd:
                for c in res["commands"]:
                    if c["kind"] in SAFE_CMDS:
                        print()
                        print(C.gray(f"  执行：{c['cmd']}"))
                        rc, out, err = run(c["cmd"], shell=True, timeout=600)
                        if rc != 0:
                            print(C.orange(f"    返回码 {rc}：{(err or out)[:300]}"))
                        else:
                            print(C.gray(f"    {(out or '').strip()[:300]}"))
        return 0 if res["ok"] else 2

    # ---------------- quarantine / restore / purge ----------------
    if act == "quarantine":
        items = list_quarantine()
        if not items:
            print(C.gray("  （隔离区为空）"))
            return 0
        print(f"  {'批次':<20}{'时间':<20}{'项数':>6}{'大小':>10}")
        for it in items:
            print(f"  {it['batch']:<20}{str(it['created']):<20}"
                  f"{it['count']:>6}{human_bytes(it['size']):>10}")
        print()
        print(C.gray("  还原：cli.py clean restore --batch <目录>"))
        print(C.gray("  销毁：cli.py clean purge --batch <目录> --yes"))
        return 0

    if act == "restore":
        batch = args.batch or ""
        if not batch:
            print(C.red("  需要 --batch <隔离区目录>"))
            return 1
        res = restore_batch(batch)
        if not res["ok"] and res.get("error"):
            print(C.red(f"  {res['error']}"))
            return 1
        print(C.green(f"  ✓ 已还原 {res['restored']} 项"))
        if res["failed"]:
            print(C.orange(f"  {len(res['failed'])} 项未还原："))
            for f in res["failed"][:10]:
                print(f"      {f['path']} —— {f['reason']}")
        return 0

    if act == "purge":
        if not args.yes:
            print(C.red("  销毁隔离区是不可逆的。确认后加 --yes。"))
            print(C.gray("  先看看里面有什么： cli.py clean quarantine"))
            return 3
        res = purge_quarantine(args.batch or None)
        print(C.green(f"  ✓ 已销毁 {res['purged']} 个批次，回收 {human_bytes(res['freed'])}"))
        return 0

    print(C.red(f"  未知动作：{act}"))
    return 1


def add_clean_parser(sub) -> None:
    p = sub.add_parser("clean", help="硬盘瘦身：扫描 / 计划 / 隔离清理 / 还原")
    p.add_argument("action", choices=["scan", "show", "plan", "clean",
                                      "quarantine", "restore", "purge"])
    p.add_argument("--scan", default="", help="扫描结果 JSON（默认用最新一份）")
    p.add_argument("--category", default="", help="show 只看某个类别")
    p.add_argument("--categories", default="",
                   help="要清理的类别，逗号分隔（必填，无默认）")
    p.add_argument("--path", action="append", default=[],
                   help="大文件/重复文件扫描的根（可重复）")
    p.add_argument("--big", action="store_true", help="附带大文件扫描")
    p.add_argument("--dup", action="store_true", help="附带重复文件检测（慢）")
    p.add_argument("--deep", action="store_true")
    p.add_argument("--min-size", type=int, default=200 * 1024 * 1024,
                   help="大文件阈值（字节，默认 200MB）")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--budget", type=float, default=25.0, help="每类别扫描秒数上限")
    p.add_argument("--big-budget", type=float, default=90.0,
                   help="大文件/重复文件扫描的时间上限（秒）")
    p.add_argument("--depth", type=int, default=5,
                   help="大文件/重复文件扫描的最大下钻层数（默认 5，机械盘别调大）")
    p.add_argument("--max-show", type=int, default=30)
    p.add_argument("--to", default="", help="隔离区位置（默认同盘，便于秒级还原）")
    p.add_argument("--batch", default="", help="隔离区批次目录")
    p.add_argument("--purge", action="store_true",
                   help="直接删除而非隔离（不可逆，谨慎）")
    p.add_argument("--allow-caution", action="store_true",
                   help="允许处理「谨慎」级类别")
    p.add_argument("--allow-cmd", action="store_true",
                   help="执行幂等的命令型清理（journal vacuum / autoremove）")
    p.add_argument("--apply", action="store_true", help="真正执行")
    p.add_argument("--yes", action="store_true", help="确认")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_clean)


if __name__ == "__main__":
    ap_argv = sys.argv[1:]
    print("clean.py 由 cli.py 调用，例如：")
    print("  python cli.py clean scan")
    print("  python cli.py clean clean --categories thumb_cache --apply --yes")
    raise SystemExit(0)
