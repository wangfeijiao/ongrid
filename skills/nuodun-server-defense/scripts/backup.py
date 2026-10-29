#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup.py — 备份引擎。

设计要点（都是"备份能不能救命"的关键）：
  * 先备份再动手：任何加固/清理动作前，能自动做一次配置快照。
  * 哈希留痕：每份备份生成 manifest（sha256 + 大小 + 时间 + 源路径），
    这样"备份是不是好的"不靠感觉，靠校验。
  * 校验而非假设：--verify 会重新读取每个文件比对哈希，损坏就明确报出来。
  * 3-2-1 提醒：只在本机复制不算备份；报告里会明确提示异地/离线副本缺失。
  * 保留策略：按份数/天数自动清理，且清理前先列出将要删除的清单。

对外接口：cmd_backup / cmd_restore / snapshot_configs / generate_schedule
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path

from common import (
    C,
    OUTPUT_DIR,
    WINDOWS,
    ensure_output_dir,
    human_bytes,
    now_stamp,
    write_script,
)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception as e:  # noqa: BLE001
        return f"ERR:{type(e).__name__}"


def _expand(sources: list[str]) -> list[Path]:
    out: list[Path] = []
    for s in sources:
        p = Path(os.path.expandvars(os.path.expanduser(s)))
        if p.exists():
            out.append(p)
    return out


def _walk(files: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in files:
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            for dirpath, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in
                           (".git", "node_modules", "__pycache__", "$RECYCLE.BIN",
                            "System Volume Information")]
                for n in names:
                    out.append(Path(dirpath) / n)
    return out


def default_targets() -> list[str]:
    """按平台给出「重要文件」的默认清单。"""
    if WINDOWS:
        return [
            os.path.expandvars(r"%SystemRoot%\System32\drivers\etc"),
            os.path.expandvars(r"%SystemRoot%\System32\inetsrv\config"),
            os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp"),
            os.path.expandvars(r"%USERPROFILE%\.ssh"),
            r"C:\Windows\System32\winevt\Logs",
        ]
    return [
        "/etc", "/root/.ssh", "/home",
        "/usr/local/tomcat/conf", "/etc/nginx", "/etc/httpd",
        "/var/spool/cron", "/etc/mysql", "/etc/redis",
    ]


# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------

def create_backup(
    sources: list[str],
    dest: str,
    label: str = "backup",
    compress: bool = True,
    incremental_from: str | None = None,
    dry_run: bool = False,
) -> dict:
    """创建一份带 manifest 的备份。返回结果字典（可直接进报告）。"""
    ensure_output_dir()
    srcs = _expand(sources)
    if not srcs:
        return {"ok": False, "error": "没有可备份的源路径（全部不存在或无权限）", "files": []}

    dest_p = Path(dest).expanduser()
    stamp = now_stamp()
    bundle_name = f"{label}-{stamp}"
    bundle_dir = dest_p / bundle_name
    work = OUTPUT_DIR / f"backup-{stamp}" if not compress else None

    entries: list[dict] = []
    prev_hashes: dict[str, str] = {}
    if incremental_from:
        mf = Path(incremental_from)
        if mf.is_file():
            try:
                prev = json.loads(mf.read_text(encoding="utf-8"))
                prev_hashes = {e["path"]: e["sha256"] for e in prev.get("entries", [])}
            except Exception:  # noqa: BLE001
                prev_hashes = {}

    t0 = time.time()
    all_files = _walk(srcs)
    skipped_unchanged = 0
    failed: list[str] = []

    staging = work if work else bundle_dir
    if not dry_run:
        staging.mkdir(parents=True, exist_ok=True)

    for fp in all_files:
        try:
            size = fp.stat().st_size
        except OSError:
            continue
        # 单文件超过 2GB 默认跳过（备份引擎不是镜像工具，避免把磁盘写爆）
        if size > 2 * 1024**3:
            failed.append(f"{fp} （>2GB，已跳过）")
            continue
        digest = sha256_file(fp)
        if prev_hashes.get(str(fp)) == digest:
            skipped_unchanged += 1
            continue
        # 目标路径：保留源路径结构，去掉盘符/根斜杠
        rel = str(fp).replace(":", "").lstrip("\\/")
        target = staging / rel
        # stored 必须与归档内的实际条目名一致：
        #   压缩模式 → 归档根就是 staging，故为 rel
        #   目录模式 → 条目位于 dest/<bundle>/rel，故需带上 bundle 名
        # 统一用正斜杠，跨平台一致（zip 规范如此）。
        stored = (rel if compress else f"{bundle_name}/{rel}").replace("\\", "/")
        entries.append({
            "path": str(fp),
            "stored": stored,
            "size": size,
            "sha256": digest,
            "mtime": datetime.fromtimestamp(fp.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fp, target)
            except Exception as e:  # noqa: BLE001
                entries.pop()
                failed.append(f"{fp} （复制失败：{type(e).__name__}: {e}）")

    archive_path = ""
    if not dry_run and compress and entries:
        dest_p.mkdir(parents=True, exist_ok=True)
        archive_path = str(dest_p / f"{bundle_name}.zip")
        try:
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for f in _walk([staging]):
                    z.write(f, str(f.relative_to(staging)))
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            failed.append(f"打包失败：{type(e).__name__}: {e}")
            archive_path = ""

    manifest = {
        "label": label,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "host": platform.node(),
        "sources": sources,
        "incremental_from": incremental_from or "",
        "archive": archive_path,
        "bundle_dir": str(bundle_dir) if not compress else "",
        "file_count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
        "skipped_unchanged": skipped_unchanged,
        "failed": failed,
        "duration_s": round(time.time() - t0, 2),
        "entries": entries,
    }

    if not dry_run:
        mp = (dest_p / f"{bundle_name}.manifest.json")
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["manifest_file"] = str(mp)

    manifest["ok"] = True
    return manifest


def verify_backup(manifest_file: str) -> dict:
    """按 manifest 逐文件校验哈希。返回 {'ok':bool,'bad':[...],'checked':n}"""
    mf = Path(manifest_file)
    if not mf.is_file():
        return {"ok": False, "error": f"manifest 不存在：{manifest_file}", "bad": [], "checked": 0}
    data = json.loads(mf.read_text(encoding="utf-8"))
    base = mf.parent
    archive = data.get("archive") or ""
    bundle_dir = data.get("bundle_dir") or ""
    bad: list[dict] = []
    checked = 0

    if archive and Path(archive).is_file():
        try:
            with zipfile.ZipFile(archive) as z:
                for e in data.get("entries", []):
                    name = e["stored"].replace("\\", "/")
                    checked += 1
                    try:
                        z.getinfo(name)          # 只为确认条目存在，缺了就记缺失
                    except KeyError:
                        bad.append({"path": e["path"], "reason": "归档中缺失"})
                        continue
                    h = hashlib.sha256()
                    with z.open(name) as fh:
                        while True:
                            b = fh.read(1024 * 1024)
                            if not b:
                                break
                            h.update(b)
                    if h.hexdigest() != e["sha256"]:
                        bad.append({"path": e["path"], "reason": "哈希不匹配（内容已损坏）"})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"归档无法读取：{e}", "bad": [], "checked": checked}
    elif bundle_dir and Path(bundle_dir).is_dir():
        for e in data.get("entries", []):
            checked += 1
            fp = base / e["stored"]
            if not fp.is_file():
                bad.append({"path": e["path"], "reason": "文件缺失"})
                continue
            if sha256_file(fp) != e["sha256"]:
                bad.append({"path": e["path"], "reason": "哈希不匹配"})
    else:
        return {"ok": False, "error": "归档与目录都不存在，备份可能已被移动或删除",
                "bad": [], "checked": 0}

    return {"ok": not bad, "bad": bad, "checked": checked,
            "manifest": str(mf), "created": data.get("created")}


def list_backups(dest: str) -> list[dict]:
    d = Path(dest).expanduser()
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "manifest": str(f),
                "label": m.get("label"),
                "created": m.get("created"),
                "files": m.get("file_count"),
                "size": m.get("total_bytes"),
                "archive": m.get("archive"),
                "failed": len(m.get("failed", [])),
            })
        except Exception:  # noqa: BLE001
            continue
    return out


def prune_backups(dest: str, keep: int = 14, dry_run: bool = True) -> list[str]:
    """保留最近 keep 份，其余列入删除清单（默认只列不删）。"""
    items = list_backups(dest)
    victims = items[keep:]
    names = [v["manifest"] for v in victims]
    if dry_run:
        return names
    for v in victims:
        for p in (v["manifest"], v.get("archive")):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
    return names


# --------------------------------------------------------------------------
# 配置快照（加固前的安全网）
# --------------------------------------------------------------------------

def snapshot_configs(tag: str = "prechange") -> str:
    """把当前关键配置打进一份快照，返回 manifest 路径。供 harden 自动调用。"""
    ensure_output_dir()
    dest = OUTPUT_DIR / "snapshots"
    dest.mkdir(parents=True, exist_ok=True)
    res = create_backup(default_targets(), str(dest), label=f"{tag}-snapshot", compress=True)
    return res.get("manifest_file", "")


def restore_snapshot(manifest_file: str, overrides: dict | None = None) -> dict:
    """从快照恢复。overrides: {源路径前缀: 目标前缀}，用于恢复到沙箱目录先行核对。"""
    mf = Path(manifest_file)
    if not mf.is_file():
        return {"ok": False, "error": "manifest 不存在"}
    data = json.loads(mf.read_text(encoding="utf-8"))
    archive = Path(data.get("archive") or "")
    if not archive.is_file():
        return {"ok": False, "error": "归档文件不存在，无法恢复"}
    restored, failed = [], []
    with zipfile.ZipFile(archive) as z:
        for e in data.get("entries", []):
            src_path = e["path"]
            target = Path(src_path)
            if overrides:
                for pref, repl in overrides.items():
                    if src_path.startswith(pref):
                        target = Path(src_path.replace(pref, repl, 1))
                        break
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(e["stored"].replace("\\", "/")) as fh, target.open("wb") as out:
                    shutil.copyfileobj(fh, out)
                restored.append(str(target))
            except Exception as ex:  # noqa: BLE001
                failed.append(f"{target}: {type(ex).__name__}: {ex}")
    return {"ok": not failed, "restored": len(restored), "failed": failed}


# --------------------------------------------------------------------------
# 定时备份脚本生成
# --------------------------------------------------------------------------

def generate_schedule(dest: str, sources: list[str], keep: int = 14,
                      hour: int = 3, script_out: str | None = None) -> str:
    """生成一个可直接挂到计划任务 / cron 的备份脚本。"""
    ensure_output_dir()
    skill = Path(__file__).resolve().parent.parent
    srcs = " ".join(f'"{s}"' for s in sources)

    if WINDOWS:
        out = Path(script_out or (OUTPUT_DIR / "backup-task.ps1"))
        write_script(out, f"""# 由 服务器安全维护、杀木马病毒skill 生成 —— 每日 {hour:02d}:00 自动备份 + 校验 + 保留策略
# 挂载方式（管理员）：
#   schtasks /create /tn "OpsDailyBackup" /sc daily /st {hour:02d}:00 ^
#     /tr "powershell -NoProfile -ExecutionPolicy Bypass -File \\"{out}\\"" /ru SYSTEM
#
# 出错处理：每一步都判断结果并打印原因，绝不"默默失败"（备份失败而没人知道最危险）。
$ErrorActionPreference = "Continue"
$py = "python"
if (-not (Get-Command $py -ErrorAction SilentlyContinue)) {{
    Write-Host "错误：PATH 里找不到 python。" -ForegroundColor Red
    Write-Host "      原因：计划任务以 SYSTEM 身份运行时，PATH 与你登录时不同。" -ForegroundColor DarkGray
    Write-Host "      处理：把脚本里的 $py 改成 python 的绝对路径后重新挂载任务。" -ForegroundColor DarkGray
    exit 1
}}
$skill = "{skill}"

& $py "$skill\\scripts\\backup.py" run --dest "{dest}" --sources {srcs} --label daily --compress
if ($LASTEXITCODE -ne 0) {{
    Write-Host "错误：备份失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red
    Write-Host "      原因：多数是目标目录不可写、磁盘空间不足，或源路径被占用。" -ForegroundColor DarkYellow
    Write-Host "      处理：确认目标盘可用空间，并手工执行一次看具体报错。" -ForegroundColor DarkGray
    exit 2
}}

& $py "$skill\\scripts\\backup.py" verify-latest --dest "{dest}"
if ($LASTEXITCODE -ne 0) {{
    Write-Host "错误：备份校验未通过。" -ForegroundColor Red
    Write-Host "      原因：备份文件被改动/损坏，或写入过程被中断。" -ForegroundColor DarkYellow
    Write-Host "      处理：不要删除这份备份，先查磁盘健康与杀毒软件隔离记录；重跑一次备份。" -ForegroundColor DarkGray
    exit 3
}}

& $py "$skill\\scripts\\backup.py" prune --dest "{dest}" --keep {keep} --yes
Write-Host "每日备份流程结束。" -ForegroundColor Green
""")
        return str(out)

    out = Path(script_out or (OUTPUT_DIR / "backup-task.sh"))
    write_script(out, f"""#!/bin/sh
# 由 服务器安全维护、杀木马病毒skill 生成 —— 每日 {hour:02d}:00 自动备份 + 校验 + 保留策略
# 挂载方式：crontab -e 加入
#   0 {hour} * * * /bin/sh "{out}" >> /var/log/ops-backup.log 2>&1
#
# 出错处理：每一步都判断结果并打印原因，绝不静默失败（备份失败而没人知道最危险）。
set -u
PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "错误：找不到 $PY。" >&2
  echo "      原因：cron 的 PATH 很窄，通常不包含 python3 所在目录。" >&2
  echo "      处理：把本脚本里的 PY= 改成 python3 的绝对路径（which python3 查看）。" >&2
  exit 1
fi
ND_SKILL="{skill}"

"$PY" "$ND_SKILL/scripts/backup.py" run --dest "{dest}" --sources {srcs} --label daily --compress
if [ $? -ne 0 ]; then
  echo "错误：备份失败。" >&2
  echo "      原因：多数是目标目录不可写、磁盘空间不足，或源路径被占用。" >&2
  echo "      处理：确认目标盘可用空间，并手工执行一次看具体报错。" >&2
  exit 2
fi

"$PY" "$ND_SKILL/scripts/backup.py" verify-latest --dest "{dest}"
if [ $? -ne 0 ]; then
  echo "错误：备份校验未通过。" >&2
  echo "      原因：备份文件被改动/损坏，或写入过程被中断。" >&2
  echo "      处理：不要删除这份备份，先查磁盘健康与安全软件隔离记录；重跑一次备份。" >&2
  exit 3
fi

"$PY" "$ND_SKILL/scripts/backup.py" prune --dest "{dest}" --keep {keep} --yes
echo "每日备份流程结束。"
""")
    try:
        os.chmod(out, 0o755)
    except OSError:
        pass
    return str(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_backup(args) -> int:
    print(C.bold("备份"))
    if args.action == "run":
        srcs = args.sources or default_targets()
        print(C.gray(f"  源：{len(srcs)} 项    目标：{args.dest}"))
        res = create_backup(srcs, args.dest, label=args.label,
                            compress=not args.no_compress,
                            incremental_from=args.incremental_from,
                            dry_run=args.dry_run)
        if not res.get("ok"):
            print(C.red(f"  失败：{res.get('error')}"))
            return 1
        print(f"  文件 {res['file_count']} 个 / {human_bytes(res['total_bytes'])}"
              f"（增量跳过 {res['skipped_unchanged']}）耗时 {res['duration_s']}s")
        if res.get("archive"):
            print(f"  归档：{res['archive']}")
        if res.get("manifest_file"):
            print(f"  清单：{res['manifest_file']}")
        if res["failed"]:
            print(C.orange(f"  ⚠ {len(res['failed'])} 项未备份："))
            for x in res["failed"][:10]:
                print(f"      {x}")
        print()
        print(C.orange("  提醒：本机副本不算完整备份。按 3-2-1 原则，"
                       "至少再有一份异地或离线副本。"))
        return 0

    if args.action == "verify":
        res = verify_backup(args.manifest)
        if res.get("error"):
            print(C.red(f"  {res['error']}"))
            return 1
        if res["ok"]:
            print(C.green(f"  ✓ 校验通过：{res['checked']} 个文件哈希全部匹配"))
            return 0
        print(C.red(f"  ✗ 校验失败：{len(res['bad'])}/{res['checked']} 个文件异常"))
        for b in res["bad"][:20]:
            print(f"      {b['path']} —— {b['reason']}")
        return 2

    if args.action == "verify-latest":
        items = list_backups(args.dest)
        if not items:
            print(C.orange("  该目录下没有找到备份清单"))
            return 1
        res = verify_backup(items[0]["manifest"])
        print(C.gray(f"  校验对象：{items[0]['manifest']}（{items[0].get('created')}）"))
        if res.get("error"):
            print(C.red(f"  {res['error']}"))
            return 1
        if res["ok"]:
            print(C.green(f"  ✓ 校验通过：{res['checked']} 个文件哈希全部匹配"))
            return 0
        print(C.red(f"  ✗ 校验失败：{len(res['bad'])}/{res['checked']} 个文件异常"))
        for b in res["bad"][:20]:
            print(f"      {b['path']} —— {b['reason']}")
        return 2

    if args.action == "list":
        items = list_backups(args.dest)
        if not items:
            print(C.gray("  （空）"))
            return 0
        print(f"  {'时间':<20} {'标签':<12} {'文件':>6} {'大小':>10}")
        for it in items:
            print(f"  {str(it['created']):<20} {str(it['label']):<12} "
                  f"{it['files']:>6} {human_bytes(it['size']):>10}")
        return 0

    if args.action == "prune":
        victims = prune_backups(args.dest, keep=args.keep, dry_run=not args.yes)
        if not victims:
            print(C.gray("  无需清理"))
            return 0
        verb = "已删除" if args.yes else "将删除（加 --yes 才真正执行）"
        print(f"  {verb} {len(victims)} 份：")
        for v in victims:
            print(f"      {v}")
        return 0

    if args.action == "restore":
        res = restore_snapshot(args.manifest, overrides=None if not args.to else {"": args.to})
        if not res.get("ok"):
            print(C.red(f"  恢复存在失败：{res.get('error','')}"))
            for x in res.get("failed", [])[:20]:
                print(f"      {x}")
            return 2
        print(C.green(f"  ✓ 已恢复 {res['restored']} 个文件"))
        return 0

    if args.action == "schedule":
        p = generate_schedule(args.dest, args.sources or default_targets(),
                              keep=args.keep, hour=args.hour)
        print(C.green(f"  已生成定时备份脚本：{p}"))
        print(C.gray("  按脚本头部注释挂到计划任务或 crontab 即可。"))
        return 0

    if args.action == "snapshot":
        p = snapshot_configs(tag=args.label or "manual")
        print(C.green(f"  配置快照已生成：{p}"))
        return 0

    print(C.red(f"  未知动作：{args.action}"))
    return 1


def add_backup_parser(sub) -> None:
    p = sub.add_parser("backup", help="备份 / 校验 / 恢复 / 定时")
    p.add_argument("action", choices=["run", "verify", "verify-latest", "list",
                                      "prune", "restore", "schedule", "snapshot"])
    p.add_argument("--dest", default=str(Path.home() / "ops-backups"))
    p.add_argument("--sources", nargs="*", help="源路径（默认按平台给重要文件清单）")
    p.add_argument("--label", default="backup")
    p.add_argument("--manifest", default="")
    p.add_argument("--incremental-from", default=None)
    p.add_argument("--no-compress", action="store_true")
    p.add_argument("--keep", type=int, default=14)
    p.add_argument("--hour", type=int, default=3)
    p.add_argument("--to", default="", help="恢复到指定前缀目录（沙箱核对用）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_backup)
