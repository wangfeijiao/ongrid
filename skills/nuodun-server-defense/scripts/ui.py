#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui.py — 本地图形控制台（127.0.0.1，仅标准库）。

为什么用「本地网页」而不是桌面 GUI：
  * 零依赖：不用装 tkinter/PyQt/浏览器内核，任何机器上都能起。
  * 跨平台一致：Windows 和 UNIX/Linux 上界面完全一样（你的诉求）。
  * 可远程：控制台在本地跑，体检目标可以是本机，也可以是远程 Linux（SSH）。
  * 结果可交付：控制台里看的是实时视图，一键导出的 HTML 报告是能直接发出去的成品。

安全边界：
  * 只绑 127.0.0.1，不对外监听。
  * 加固执行必须带 UI 下发的确认令牌；未确认一律拒绝。
  * 所有"会改系统"的动作默认只出脚本，界面上要点两次。

接口：
  GET  /                    控制台页面
  GET  /api/env             本机与技能环境信息（能力探测）
  POST /api/run             启动一次体检（异步）
  GET  /api/job?id=..       任务状态 + 增量日志
  GET  /api/report?id=..    体检结果 JSON
  GET  /api/export?id=..&kind=html|json|plan   下载成品
  POST /api/backup          执行备份
  GET  /api/backups         列出备份
  POST /api/harden/plan     生成加固方案（不执行）
  POST /api/harden/apply    执行加固（需 token）
  POST /api/clean/scan      硬盘瘦身：只读扫描（异步）
  POST /api/clean/plan      出清理计划（不执行）
  POST /api/clean/apply     执行清理（需 token + 点名类别）
  GET  /api/clean/quarantine  列出隔离区批次
  POST /api/clean/restore   从隔离区还原
  POST /api/clean/purge     销毁隔离区内容（需 token，不可逆）
  POST /api/net/diag        上网提速：只读网络诊断（异步）
  POST /api/net/tune        生成网络调优脚本（不执行）
  POST /api/hunt/scan       木马深度查杀（只读，异步）
  POST /api/hunt/forensics  一键应急取证包（只读，异步）
  GET  /api/errors          最近的出错记录（原因/处理/堆栈位置）
  POST /api/errors/clear    清空错误日志
  POST /api/shutdown        关闭控制台

错误处理契约（这一层是"不崩"的保证）：
  * 任何接口出错都返回 **JSON**（HTTP 500），字段包含 error / cause / hint / impact / log，
    界面会直接把「原因 + 处理办法」展示给用户，而不是一个空白页或断开的连接。
  * 后台任务出错时写入任务日志（前端轮询可见），并把结构化诊断放进 error_detail。
  * 服务端绝不因单个请求异常而退出；异常堆栈只进错误日志，不刷用户终端。
"""

from __future__ import annotations

import argparse
import functools
import json
import platform
import shutil
import socket
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

import errors as E  # noqa: E402
from common import (  # noqa: E402
    ASSETS_DIR,
    C,
    OUTPUT_DIR,
    WINDOWS,
    ensure_output_dir,
    is_admin,
    now_stamp,
)

SKILL_DIR = Path(__file__).resolve().parent.parent
UI_HTML = ASSETS_DIR / "ui.html"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
SERVER_REF: dict = {}
CLEAN_DIR = OUTPUT_DIR / "cleanup"


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------

_SENSITIVE_KEYS = {"password", "passwd", "secret", "token", "passphrase"}


def _redact(params: dict) -> dict:
    """返回脱敏后的参数副本：敏感字段打码，避免明文密码留在任务记录里。

    说明：后台任务实际使用的是原始 params（按引用传入），这里只对
    存进 JOBS 的副本脱敏。这样即使未来某处把 params 回显到界面或
    写进日志，也不会带出 SSH 明文密码。
    """
    safe = {}
    for k, v in params.items():
        if k.lower() in _SENSITIVE_KEYS and v:
            safe[k] = "******"
        elif isinstance(v, dict):
            safe[k] = _redact(v)
        else:
            safe[k] = v
    return safe


def _new_job(kind: str, params: dict) -> str:
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[jid] = {
            "id": jid, "kind": kind, "params": _redact(params),
            "status": "running", "progress": 0,
            "logs": [], "result": None, "error": "", "error_detail": {},
            "started": datetime.now().strftime("%H:%M:%S"),
            "started_ts": time.time(),
        }
    return jid


def _log(jid: str, msg: str) -> None:
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            if len(j["logs"]) > 500:
                j["logs"] = j["logs"][-300:]


def _finish(jid: str, result=None, error: str = "", detail: dict | None = None) -> None:
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is None:
            return
        j["status"] = "error" if error else "done"
        j["result"] = result
        j["error"] = error
        j["error_detail"] = detail or {}
        j["progress"] = 100
        j["elapsed"] = round(time.time() - j["started_ts"], 1)


def _fail(jid: str, msg: str, kind: str = "拒绝执行", hint: str = "") -> None:
    """业务性拒绝（不是程序故障）：说清为什么不做，不做任何修改。"""
    _log(jid, f"【{kind}】{msg}")
    if hint:
        _log(jid, f"  {hint}")
    _finish(jid, error=msg, detail={
        "cause": msg, "certain": True,
        "hint": hint or "请按界面提示补齐参数后重试。",
        "impact": "没有做任何改动。", "log": "", "type": kind,
    })


def _job_error(jid: str, exc: BaseException, context: str) -> None:
    """后台任务出错：落盘 + 写进任务日志（前端可见）+ 结构化返回。"""
    info = E.record(exc, context=context, app="ui")
    _log(jid, f"【出错】{info['type']}: {info['message']}")
    _log(jid, f"  原因：{info['cause']}")
    _log(jid, f"  处理：{info['hint']}")
    _log(jid, f"  影响：{info['impact']}")
    _log(jid, f"  日志：{info['log']}")
    _log(jid, "  本工具已安全停下；可以修正上面的原因后重试，"
              "或换用命令行（cli.py）详细排查。")
    _finish(jid, error=E.one_line(exc), detail=info)


# --------------------------------------------------------------------------
# 体检任务
# --------------------------------------------------------------------------

def _run_audit_job(jid: str, params: dict) -> None:
    try:
        scope = params.get("scope", "all")
        deep = bool(params.get("deep"))
        log_hours = int(params.get("log_hours") or 48)
        quick = bool(params.get("quick"))
        target = params.get("target") or {"kind": "local"}
        kind = target.get("kind", "local")

        _log(jid, f"开始体检：目标={_target_label(target)}，范围={scope}"
                  f"{'，深扫' if deep else ''}")

        if kind == "ssh":
            from linux_facts import Target, collect as lcollect
            from linux_rules import apply_all

            t = Target(kind="ssh", host=target.get("host", ""),
                       user=target.get("user") or "root",
                       port=int(target.get("port") or 22),
                       key=target.get("key") or "",
                       password=target.get("password") or "",
                       label=target.get("label") or "")
            _log(jid, f"通过 SSH 连接 {t.display} 并下发巡检探针（只读，不落盘）...")
            from common import Report  # noqa: PLC0415

            rep = Report(mode="audit-ssh")
            rep.host["hostname"] = t.display
            rep.note(f"远程目标：{t.display}（探针只读执行）")
            facts = lcollect(t, deep=deep, timeout=int(params.get("timeout") or 300))
            if facts.error:
                _log(jid, f"提示：{facts.error}")
            _log(jid, f"探针返回 {facts.total_lines} 行，解析 {len(facts.data)} 个键"
                      f"（失败 {facts.bad_lines} 行）")
            for cr in apply_all(facts, deep=deep):
                rep.add_result(cr)
            rep.finished = _now()
            rep.duration_s = time.time() - JOBS[jid]["started_ts"]

        else:
            from common import Report  # noqa: PLC0415
            rep = Report(mode=f"audit-{scope}")
            if WINDOWS:
                import collect_win  # noqa: PLC0415

                collect_win.collect_all(rep, scope=scope, quick=quick,
                                        log_hours=log_hours, deep=deep,
                                        progress=lambda lb: _log(jid, f"采集中：{lb}"))
            else:
                from linux_facts import Target, collect as lcollect  # noqa: PLC0415
                from linux_rules import apply_all  # noqa: PLC0415

                _log(jid, "本机为 UNIX/Linux，执行本地巡检探针")
                facts = lcollect(Target(kind="local"), deep=deep,
                                 timeout=int(params.get("timeout") or 300))
                for cr in apply_all(facts, deep=deep):
                    rep.add_result(cr)
            rep.finished = _now()
            rep.duration_s = time.time() - JOBS[jid]["started_ts"]

        payload = rep.to_dict()
        json_path = rep.save(prefix=f"audit-{rep.mode}")
        payload["json_file"] = str(json_path)

        _log(jid, f"体检完成：健康分 {payload['score']}/100（{payload['grade']}），"
                  f"共 {payload['counts']['TOTAL']} 项问题")

        from report import write_report  # noqa: PLC0415
        html_path = write_report(payload)
        payload["html_file"] = html_path
        _log(jid, f"HTML 报告已生成：{html_path}")

        _finish(jid, payload)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "任务")


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _target_label(t: dict) -> str:
    if t.get("kind") == "ssh":
        return f"{t.get('user','root')}@{t.get('host')}:{t.get('port',22)}"
    return "本机"


# --------------------------------------------------------------------------
# 备份 / 加固任务
# --------------------------------------------------------------------------

def _run_backup_job(jid: str, params: dict) -> None:
    try:
        from backup import create_backup, default_targets, prune_backups, verify_backup

        dest = params.get("dest") or str(Path.home() / "ops-backups")
        srcs = params.get("sources") or default_targets()
        _log(jid, f"备份目标目录：{dest}")
        _log(jid, f"备份源：{', '.join(srcs[:8])}{' ...' if len(srcs) > 8 else ''}")
        res = create_backup(srcs, dest, label=params.get("label") or "manual",
                            compress=True)
        if not res.get("ok"):
            err = res.get("error", "备份失败")
            cause, hint = E.explain_rc(int(res.get("rc") or 1), str(err))
            _fail(jid, err, kind="备份失败",
                  hint=f"原因：{cause}；处理：{hint or '检查目标目录是否可写、空间是否足够'}")
            return
        _log(jid, f"已备份 {res['file_count']} 个文件 / {res['total_bytes']} 字节，"
                  f"耗时 {res['duration_s']}s")
        if res.get("archive"):
            _log(jid, f"归档：{res['archive']}")
        mf = res.get("manifest_file", "")
        if mf:
            _log(jid, "开始校验备份完整性（逐文件 sha256）...")
            v = verify_backup(mf)
            if v.get("ok"):
                _log(jid, f"✓ 校验通过：{v['checked']} 个文件哈希全部匹配")
            else:
                _log(jid, f"✗ 校验发现问题：{v.get('error') or v.get('bad')}")
        victims = prune_backups(dest, keep=int(params.get("keep") or 14), dry_run=True)
        if victims:
            _log(jid, f"保留策略：有 {len(victims)} 份历史备份超出保留份数"
                      f"（需在命令行执行 prune --yes 才会真正删除）")
        _finish(jid, res)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "备份")


def _run_harden_plan_job(jid: str, params: dict) -> None:
    try:
        from harden import PROFILE_ALIASES, generate_scripts

        profile = params.get("profile") or "baseline"
        plat = "win" if params.get("platform") == "win" or (WINDOWS and
                                                            params.get("platform") != "nix") else "nix"
        _log(jid, f"生成加固方案：画像={profile}（{PROFILE_ALIASES.get(profile, profile)}）")
        res = generate_scripts(profile, platform=plat)
        _log(jid, f"共 {res['action_count']} 个动作，脚本已生成")
        for a in res["actions"]:
            flag = {"high": " [高风险]", "medium": " [中风险]"}.get(a["risk"], "")
            _log(jid, f"  · {a['title']}{flag}")
        _log(jid, f"正向脚本：{res['apply_script']}")
        _log(jid, f"回滚脚本：{res['rollback_script']}")
        _log(jid, "未做任何修改。确认后可点击「执行加固」。")
        _finish(jid, res)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "生成")


def _run_harden_apply_job(jid: str, params: dict) -> None:
    try:
        from backup import snapshot_configs
        from harden import generate_scripts, apply_script

        profile = params.get("profile") or "baseline"
        plat = "win" if params.get("platform") == "win" or (WINDOWS and
                                                            params.get("platform") != "nix") else "nix"
        _log(jid, "【1/3】生成脚本 ...")
        res = generate_scripts(profile, platform=plat)
        _log(jid, "【2/3】生成配置快照（回滚依据）...")
        try:
            mf = snapshot_configs(tag=f"preharden-{profile}")
            _log(jid, f"      快照：{mf}")
        except Exception as e:  # noqa: BLE001
            mf = ""
            _log(jid, f"      快照失败：{e}（继续，但回滚能力受限）")
        _log(jid, "【3/3】执行加固脚本 ...")
        r = apply_script(res["apply_script"], target=params.get("target") or None)
        for line in (r.get("stdout") or "").splitlines()[-120:]:
            _log(jid, "  | " + line)
        if r.get("stderr"):
            for line in r["stderr"].splitlines()[-30:]:
                _log(jid, "  ! " + line)
        r["snapshot"] = mf
        r["plan"] = res
        _log(jid, "执行结束。请核对上面的验证输出，并重启相关服务后重跑体检确认。")
        _finish(jid, r)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "执行")


def _run_clean_scan_job(jid: str, params: dict) -> None:
    try:
        from clean import build_scan, disk_report, render_scan_text

        _log(jid, "开始只读扫描（不移动、不删除任何文件）")
        for d in disk_report():
            _log(jid, f"  磁盘 {d['mount']} 已用 {d['percent']}% "
                      f"（可用 {d['free'] // 1048576}MB）")
        sc = build_scan(
            big=bool(params.get("big")),
            dup=bool(params.get("dup")),
            min_size=int(params.get("min_mb") or 200) * 1024 * 1024,
            budget=float(params.get("budget") or 25),
            big_budget=float(params.get("big_budget") or 90),
            max_depth=int(params.get("depth") or 5),
            big_roots=[Path(p) for p in (params.get("paths") or [])] or None,
            log=lambda s: _log(jid, s))
        payload = sc.to_dict()
        CLEAN_DIR.mkdir(parents=True, exist_ok=True)
        jp = CLEAN_DIR / f"scan-{now_stamp()}.json"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        payload["json_file"] = str(jp)
        (CLEAN_DIR / f"scan-{now_stamp()}.txt").write_text(
            render_scan_text(sc), encoding="utf-8")
        _log(jid, f"扫描完成：可回收约 {payload['reclaimable'] // 1048576}MB")
        for c in payload["categories"]:
            _log(jid, f"  · {c['title']}：{c['count']} 项 / "
                      f"{c['size'] // 1024}KB（{c['risk']}）")
        _log(jid, "以上仅为判定结果，未做任何改动。")
        _finish(jid, payload)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "扫描")


def _load_job_scan(params: dict):
    from clean import _load_scan

    p = params.get("scan") or ""
    if not p or not Path(p).is_file():
        raise E.OpsError(
            "找不到扫描结果文件，无法出清理计划",
            cause="出清理计划必须先有一次只读扫描的结果；你这次没有传，或传的路径已不存在",
            hint="先在「硬盘瘦身」页点「开始扫描」，扫完再点「生成清理计划」",
            context="生成清理计划")
    return _load_scan(p)


def _run_clean_plan_job(jid: str, params: dict) -> None:
    try:
        from clean import plan_clean, _specs

        sc = _load_job_scan(params)
        cats = [c.strip() for c in (params.get("categories") or []) if c.strip()]
        if not cats:
            _fail(jid, "未点名任何类别。本工具不提供「一键清理全部」。",
                  hint="请先在上面勾选要清理的类别，再看一次清单与分级。")
            return
        units = plan_clean(sc, cats)
        specs = {s["id"]: s for s in _specs()}
        total = sum(u.size for u in units)
        _log(jid, f"清理计划：{len(units)} 项 / {total // 1024}KB")
        by: dict[str, list] = {}
        for u in units:
            by.setdefault(u.category, []).append(u)
        for cid, us in sorted(by.items(), key=lambda kv: -sum(u.size for u in kv[1])):
            sp = specs.get(cid, {})
            _log(jid, f"  【{sp.get('title', cid)}】{len(us)} 项 / "
                      f"{sum(u.size for u in us) // 1024}KB（{sp.get('risk','')}）")
        _finish(jid, {"units": [u.to_dict() for u in units],
                      "total": total, "count": len(units),
                      "categories": cats})
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "生成计划")


def _run_clean_apply_job(jid: str, params: dict) -> None:
    try:
        from clean import clean_units, disk_report, plan_clean

        sc = _load_job_scan(params)
        cats = [c.strip() for c in (params.get("categories") or []) if c.strip()]
        if not cats:
            _fail(jid, "未点名任何类别，拒绝执行。",
                  hint="清理必须点名类别 —— 没有「一键清全部」这种入口。")
            return
        units = plan_clean(sc, cats)
        if not units:
            _fail(jid, "这些类别下没有可清理的单元。",
                  hint="可能已经被清理过，或扫描结果过旧 —— 重新扫描一次再看。")
            return
        purge = bool(params.get("purge"))
        caution = [u for u in units if u.risk == "CAUTION"]
        if caution and not params.get("allow_caution"):
            _fail(jid, f"选中的类别里有 {len(caution)} 项属于「谨慎」级，"
                       f"未获授权（缺 allow_caution），已拒绝执行。",
                  hint="请在界面上勾选「允许处理谨慎级」，或改选更安全的类别。")
            return
        if purge and params.get("confirm") != "I-UNDERSTAND-CLEAN":
            _fail(jid, "直接删除需要确认令牌，已拒绝执行。",
                  hint="不勾选确认时默认走「移入隔离区」（可还原），这是更安全的选择。")
            return

        _log(jid, f"{'直接删除（不可逆）' if purge else '移入隔离区（可还原）'}："
                  f"{len(units)} 项 / {sum(u.size for u in units) // 1024}KB")
        before = {d["mount"]: d["free"] for d in disk_report()}
        res = clean_units(units, purge=purge,
                          quarantine=Path(params["to"]) if params.get("to") else None,
                          log=lambda s: _log(jid, s))
        after = {d["mount"]: d["free"] for d in disk_report()}
        for m, f in before.items():
            if m in after and after[m] > f:
                _log(jid, f"  实测 {m} 可用 {f // 1048576}MB → "
                          f"{after[m] // 1048576}MB")
        _log(jid, f"已处理 {res['removed']} 项，释放 {res['freed'] // 1024}KB")
        for r in res["refused"][:10]:
            _log(jid, f"  安全闸门拦下：{r['path']} —— {r['reason']}")
        for f in res["failed"][:10]:
            _log(jid, f"  失败：{f['path']} —— {f['reason']}（已在该处停止）")
        if res.get("quarantine"):
            _log(jid, f"隔离区：{res['quarantine']}")
            _log(jid, "确认业务正常几天后再销毁隔离区；期间可随时还原。")
        for c in res.get("commands", []):
            _log(jid, f"命令型（需你手动确认）：{c['cmd']}")
        _finish(jid, res)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "清理")


def _run_net_diag_job(jid: str, params: dict) -> None:
    try:
        from net import (_collect_unix, _collect_windows, analyze,
                         render_text)

        target = params.get("target")
        if target:
            from linux_facts import Target

            user_host, _, port = target.partition(":")
            user, _, host = user_host.partition("@")
            t = Target(kind="ssh", host=host, user=user or "root",
                       port=int(port or 22), label=target)
            _log(jid, f"通过 SSH 下发只读网络探针到 {t.display} ...")
            f = _collect_unix(t)
        elif WINDOWS:
            _log(jid, "读取网卡 / TCP 参数 / DNS / 代理配置 ...")
            f = _collect_windows()
            _log(jid, "实测延迟、DNS 可达性、HTTP 首字节耗时 ...")
        else:
            f = _collect_unix()

        findings = analyze(f)
        order = {"BAD": 0, "WARN": 1, "INFO": 2, "GOOD": 3}
        findings.sort(key=lambda x: order.get(x.level, 9))
        _log(jid, render_text(f, findings))
        _finish(jid, {"findings": [x.__dict__ for x in findings],
                      "error": f.error})
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "诊断")


def _run_net_tune_job(jid: str, params: dict) -> None:
    try:
        from net import generate_tune_scripts

        res = generate_tune_scripts(params.get("platform") or "auto")
        _log(jid, f"平台 {res['platform']} · 共 {res['action_count']} 个动作")
        for a in res["actions"]:
            flag = {"medium": "  [中风险]"}.get(a["risk"], "")
            _log(jid, f"  · {a['title']}{flag}")
            if a.get("note"):
                _log(jid, f"      注意：{a['note']}")
        _log(jid, f"正向脚本：{res['apply_script']}")
        _log(jid, f"回滚脚本：{res['rollback_script']}")
        _log(jid, "未做任何修改。审阅后可在命令行执行 net tune --apply --yes。")
        _finish(jid, res)
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "生成")


def _run_hunt_scan_job(jid: str, params: dict) -> None:
    """木马深度查杀（只读）。结果 shaping 成与体检一致的 findings 结构。"""
    try:
        from malware import run_scan

        roots = params.get("web_roots") or None
        _log(jid, "深度查杀开始（挖矿 / 矿池通信 / Webshell / 勒索 / 隐藏持久化）...")
        res = run_scan(
            web_roots=[Path(p) for p in roots] if roots else None,
            budget=float(params.get("budget") or 30),
            log=lambda s: _log(jid, s))
        sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
        hits = sorted(res.get("hits") or [],
                      key=lambda h: sev_order.get(h.get("level"), 9))
        for h in hits:
            _log(jid, f"[{h['level']}] {h['title']}")
        _finish(jid, {"findings": hits,
                      "webshells": res.get("webshells") or []})
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "查杀")


def _run_hunt_forensics_job(jid: str, params: dict) -> None:
    try:
        from malware import create_forensics

        _log(jid, "正在采集应急取证包（进程/服务/计划任务/网络/自启动/hosts）...")
        z = create_forensics(log=lambda s: _log(jid, s))
        _log(jid, "取证包只读采集，未改动任何系统状态。")
        _finish(jid, {"forensics_zip": str(z),
                      "sha256_file": str(z) + ".sha256"})
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "取证")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _route(fn):
    """给每个 HTTP 路由套一层兜底。

    保证：单个请求出错不会掐断连接、不会让服务退出，也不会把 traceback
    打到用户终端 —— 而是返回一个带「原因 / 处理 / 影响 / 日志位置」的 JSON。
    """

    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # 客户端提前断开，属正常现象
        except Exception as e:  # noqa: BLE001
            info = E.record(e, context=f"控制台接口 {getattr(self, 'path', '?')}", app="ui")
            try:
                self._json({
                    "error": f"{info['type']}: {info['message']}",
                    "error_type": info["type"],
                    "cause": info["cause"],
                    "hint": info["hint"],
                    "impact": "该请求已安全终止，服务仍在运行，其他功能不受影响。",
                    "log": info["log"],
                }, 500)
            except Exception:  # noqa: BLE001
                pass

    return wrapper


class ConsoleServer(ThreadingHTTPServer):
    """带兜底的控制台服务：内部异常只记日志，绝不刷屏、绝不退出。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):  # noqa: D102
        try:
            exc = sys.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionResetError,
                                ConnectionAbortedError)):
                return
            if exc is not None:
                E.record(exc, context=f"HTTP 连接处理（{client_address}）", app="ui")
        except Exception:  # noqa: BLE001
            pass


class Handler(BaseHTTPRequestHandler):
    # ⚠ 这个值会进 HTTP 响应头（Server: ...），而标准库用 **latin-1 严格编码**写响应头。
    #   一旦这里出现中文，每个请求都会在「发响应头」这一步抛 UnicodeEncodeError，
    #   客户端只会看到"连接被关闭、没有任何响应" —— 也就是整个控制台静默不可用。
    #   所以这里必须是 ASCII；中文名放在页面/日志里展示即可。
    server_version = "nuodun-server-defense"
    sys_version = ""

    def version_string(self):
        return self.server_version

    def send_header(self, keyword, value):  # noqa: D102
        """容错版响应头写入：非 ASCII 一律降级为 ?，绝不让响应发不出去。"""
        v = str(value)
        try:
            v.encode("latin-1")
        except UnicodeEncodeError:
            v = v.encode("latin-1", "replace").decode("latin-1")
        super().send_header(keyword, v)

    def log_message(self, fmt, *args):  # 静默，避免刷屏
        pass

    # ---- 工具 ----
    def _json(self, obj, code: int = 200) -> None:
        try:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        except Exception as e:  # noqa: BLE001 —— 结果序列化失败也要给前端一个可读的回复
            body = json.dumps(
                {"error": "结果序列化失败", "cause": E.one_line(e),
                 "hint": "把这条错误日志提供给维护者", "log": str(E.LOG_DIR)},
                ensure_ascii=False).encode("utf-8")
            code = 500
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # 客户端已走，正常现象
        except Exception as e:  # noqa: BLE001
            # 响应发不出去是"致命的静默故障"，必须留痕，否则用户只看到空白页
            E.record(e, context=f"发送响应（{getattr(self, 'path', '?')}）", app="ui")

    def _file(self, path: Path, ctype: str, download_name: str | None = None) -> None:
        try:
            if not path.is_file():
                self._json({"error": "文件不存在",
                            "cause": f"路径不存在或已被移动：{path}",
                            "hint": "重新生成一次（例如重跑体检），或用命令行确认该文件还在",
                            "impact": "没有任何改动。"}, 404)
                return
            data = path.read_bytes()
        except Exception as e:  # noqa: BLE001
            info = E.record(e, context=f"读取导出文件 {path}", app="ui")
            self._json({"error": info["message"], "cause": info["cause"],
                        "hint": info["hint"], "impact": "没有任何改动。",
                        "log": info["log"]}, 500)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if download_name:
                from urllib.parse import quote  # noqa: PLC0415

                self.send_header("Content-Disposition",
                                 f"attachment; filename*=UTF-8''{quote(download_name)}")
            self.end_headers()
            self.wfile.write(data)
        except Exception:  # noqa: BLE001
            pass

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001 —— 请求体坏了当空对象处理，由各接口自己校验必填参数
            return {}

    # ---- 路由 ----
    @_route
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            if not UI_HTML.is_file():
                self._json({"error": f"缺少界面文件 {UI_HTML}",
                            "cause": "技能包不完整：assets/ui.html 不见了",
                            "hint": "重新安装技能包；命令行功能（cli.py）不受影响",
                            "impact": "只有图形控制台不可用。"}, 500)
                return
            self._file(UI_HTML, "text/html; charset=utf-8")
            return

        if u.path == "/api/env":
            self._json(_env_info())
            return

        if u.path == "/api/errors":
            last = _safe_int((q.get("last") or ["20"])[0], 20, 1, 200)
            all_days = (q.get("all") or ["0"])[0] in ("1", "true", "yes")
            self._json({"items": E.read_log(limit=last, all_days=all_days),
                        "files": E.log_files(), "log_dir": str(E.LOG_DIR)})
            return

        if u.path == "/api/job":
            jid = (q.get("id") or [""])[0]
            with JOBS_LOCK:
                j = JOBS.get(jid)
                if not j:
                    self._json({"error": "任务不存在（可能控制台重启过）",
                                "cause": "任务 ID 已过期或输入有误",
                                "hint": "回到对应页签重新发起一次任务",
                                "impact": "没有任何改动。"}, 404)
                    return
                since = _safe_int((q.get("since") or ["0"])[0], 0, 0, 10 ** 9)
                self._json({
                    "id": j["id"], "kind": j["kind"], "status": j["status"],
                    "elapsed": j.get("elapsed", round(time.time() - j["started_ts"], 1)),
                    "logs": j["logs"][since:], "log_total": len(j["logs"]),
                    "error": j["error"],
                    "error_detail": j.get("error_detail") or {},
                    "has_result": j["result"] is not None,
                })
            return

        if u.path == "/api/report":
            jid = (q.get("id") or [""])[0]
            with JOBS_LOCK:
                j = JOBS.get(jid)
            if not j or not j.get("result"):
                self._json({"error": "结果尚未就绪",
                            "cause": "任务还没跑完，或该任务本来就不产出报告",
                            "hint": "等任务状态变成「完成」后再导出",
                            "impact": "没有任何改动。"}, 404)
                return
            self._json(j["result"])
            return

        if u.path == "/api/export":
            jid = (q.get("id") or [""])[0]
            kind = (q.get("kind") or ["html"])[0]
            with JOBS_LOCK:
                j = JOBS.get(jid)
            if not j:
                self._json({"error": "任务不存在（可能控制台重启过）",
                            "hint": "回到对应页签重新发起一次任务",
                            "impact": "没有任何改动。"}, 404)
                return
            if kind == "html":
                p = j["result"].get("html_file") if j.get("result") else None
                if p and Path(p).is_file():
                    self._file(Path(p), "text/html; charset=utf-8")
                    return
                if j.get("result"):
                    from report import write_report  # noqa: PLC0415

                    p = write_report(j["result"])
                    self._file(Path(p), "text/html; charset=utf-8")
                    return
            if kind == "json":
                p = j["result"].get("json_file") if j.get("result") else None
                if p and Path(p).is_file():
                    self._file(Path(p), "application/json; charset=utf-8")
                    return
            if kind in ("harden", "backup"):
                res = j.get("result") or {}
                p = res.get("apply_script") or res.get("manifest_file") or ""
                if p and Path(p).is_file():
                    self._file(Path(p), "text/plain; charset=utf-8",
                               download_name=Path(p).name)
                    return
            self._json({"error": "暂无可导出的文件",
                        "cause": "这类任务不产出 HTML/JSON 文件，或任务尚未完成",
                        "hint": "先在对应页签跑完任务；加固/备份类可导出生成的脚本或清单",
                        "impact": "没有任何改动。"}, 404)
            return

        if u.path == "/api/backups":
            from backup import list_backups  # noqa: PLC0415

            dest = (q.get("dest") or [str(Path.home() / "ops-backups")])[0]
            self._json({"dest": dest, "items": list_backups(dest)})
            return

        if u.path == "/api/clean/quarantine":
            from clean import list_quarantine  # noqa: PLC0415

            self._json({"items": list_quarantine()})
            return

        if u.path == "/api/errors":
            last = _safe_int((q.get("last") or ["20"])[0], 20, 1, 200)
            all_days = (q.get("all") or ["0"])[0] in ("1", "true", "yes")
            self._json({"items": E.read_log(limit=last, all_days=all_days),
                        "files": E.log_files(), "log_dir": str(E.LOG_DIR)})
            return

        self._json({"error": f"接口不存在：{u.path}",
                    "cause": "路径拼写有误，或控制台版本与界面文件不匹配",
                    "hint": "刷新页面（Ctrl+F5）后重试",
                    "impact": "没有任何改动。"}, 404)

    @_route
    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        body = self._body()

        if u.path == "/api/errors/clear":
            self._json({"ok": True, "removed": E.clear_log()})
            return

        if u.path == "/api/run":
            jid = _new_job("audit", body)
            _start_job(_run_audit_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/backup":
            jid = _new_job("backup", body)
            _start_job(_run_backup_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/harden/plan":
            jid = _new_job("harden-plan", body)
            _start_job(_run_harden_plan_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/harden/apply":
            if body.get("confirm") != "I-UNDERSTAND":
                self._json({"error": "缺少确认令牌，已拒绝执行。",
                            "cause": "执行加固会改系统，必须先明确确认",
                            "hint": "在界面上勾选确认后再点执行",
                            "impact": "没有做任何改动。"}, 400)
                return
            jid = _new_job("harden-apply", body)
            _start_job(_run_harden_apply_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/backup/schedule":
            from backup import default_targets, generate_schedule  # noqa: PLC0415

            try:
                p = generate_schedule(body.get("dest") or str(Path.home() / "ops-backups"),
                                      body.get("sources") or default_targets(),
                                      keep=_safe_int(body.get("keep"), 14, 1, 9999),
                                      hour=_safe_int(body.get("hour"), 3, 0, 23))
                self._json({"ok": True, "script": p})
            except Exception as e:  # noqa: BLE001
                info = E.record(e, context="生成定时备份脚本", app="ui")
                self._json({"error": info["message"], "cause": info["cause"],
                            "hint": info["hint"], "impact": "没有任何改动。",
                            "log": info["log"]}, 500)
            return

        if u.path == "/api/clean/scan":
            jid = _new_job("clean-scan", body)
            _start_job(_run_clean_scan_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/clean/plan":
            jid = _new_job("clean-plan", body)
            _start_job(_run_clean_plan_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/clean/apply":
            # 两道闸门：先要求界面确认，再要求类别被点名。
            # 少任何一道，这个接口就变成了「一键清全部」，那是我们刻意不提供的东西。
            if body.get("confirm") != "I-UNDERSTAND-CLEAN":
                self._json({"error": "缺少确认令牌，已拒绝执行。",
                            "cause": "清理会改动文件系统，必须先明确确认",
                            "hint": "在界面上勾选确认（并核对要清理的类别）后再执行",
                            "impact": "没有做任何改动。"}, 400)
                return
            if not (body.get("categories") or []):
                self._json({"error": "未点名任何类别，拒绝执行。",
                            "cause": "本工具不提供「一键清全部」",
                            "hint": "先在扫描结果里勾选要清理的类别（建议从「安全」级开始）",
                            "impact": "没有做任何改动。"}, 400)
                return
            jid = _new_job("clean-apply", body)
            _start_job(_run_clean_apply_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/clean/restore":
            from clean import restore_batch  # noqa: PLC0415

            batch = body.get("batch") or ""
            if not batch:
                self._json({"error": "缺少 batch 参数（要还原哪个隔离批次）",
                            "cause": "界面没有传批次目录",
                            "hint": "在「硬盘瘦身」页的隔离区列表里点「还原」",
                            "impact": "没有做任何改动。"}, 400)
                return
            try:
                res = restore_batch(batch)
            except Exception as e:  # noqa: BLE001
                info = E.record(e, context=f"还原隔离批次 {batch}", app="ui")
                self._json({"error": info["message"], "cause": info["cause"],
                            "hint": info["hint"],
                            "impact": "还原可能只完成了一部分 —— 请核对目标目录里的文件。",
                            "log": info["log"]}, 500)
                return
            if isinstance(res, dict) and not res.get("ok", True):
                res.setdefault("cause", "隔离批次目录不存在，或清单文件已被移动/删除")
                res.setdefault("hint", "在「硬盘瘦身」页刷新隔离区列表，选一个真实存在的批次；"
                                       "还原脚本是隔离目录里的 restore 清单，别手工改名")
                res.setdefault("impact", "没有还原任何文件。")
            self._json(res)
            return

        if u.path == "/api/clean/purge":
            from clean import purge_quarantine  # noqa: PLC0415

            if body.get("confirm") != "I-UNDERSTAND-CLEAN":
                self._json({"error": "销毁隔离内容不可逆，需要确认令牌，已拒绝执行。",
                            "cause": "销毁动作没法撤销",
                            "hint": "确认业务已正常运行几天后，再在界面上明确确认",
                            "impact": "没有做任何改动。"}, 400)
                return
            try:
                self._json(purge_quarantine(body.get("batch") or None))
            except Exception as e:  # noqa: BLE001
                info = E.record(e, context="销毁隔离区内容", app="ui")
                self._json({"error": info["message"], "cause": info["cause"],
                            "hint": info["hint"],
                            "impact": "可能只销毁了一部分；隔离区其余内容仍在磁盘上。",
                            "log": info["log"]}, 500)
            return

        if u.path == "/api/net/diag":
            jid = _new_job("net-diag", body)
            _start_job(_run_net_diag_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/net/tune":
            jid = _new_job("net-tune", body)
            _start_job(_run_net_tune_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/hunt/scan":
            jid = _new_job("hunt-scan", body)
            _start_job(_run_hunt_scan_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/hunt/forensics":
            jid = _new_job("hunt-forensics", body)
            _start_job(_run_hunt_forensics_job, jid, body)
            self._json({"id": jid})
            return

        if u.path == "/api/shutdown":
            self._json({"ok": True})
            try:
                threading.Timer(0.4, lambda: SERVER_REF.get("server") and
                                SERVER_REF["server"].shutdown()).start()
            except Exception:  # noqa: BLE001
                pass
            return

        self._json({"error": f"接口不存在：{u.path}",
                    "cause": "路径拼写有误，或控制台版本与界面文件不匹配",
                    "hint": "刷新页面（Ctrl+F5）后重试",
                    "impact": "没有任何改动。"}, 404)


def _start_job(fn, jid: str, params: dict) -> None:
    """启动后台任务线程。

    线程创建本身也可能失败（线程数上限、内存不足）——
    这种情况立刻把任务标成失败并说明原因，而不是让界面永远转圈。
    """
    def _runner():
        try:
            fn(jid, params)
        except Exception as e:  # noqa: BLE001 —— 任务函数自己也有兜底，这里是双保险
            _job_error(jid, e, getattr(fn, "__name__", "后台任务"))

    try:
        threading.Thread(target=_runner, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        _job_error(jid, e, "启动后台任务线程")


def _safe_int(value, default: int, lo: int, hi: int) -> int:
    """把界面传来的值安全转成整数并夹在范围内；非法值用 default。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


# --------------------------------------------------------------------------

def _env_info() -> dict:
    """环境信息。任何一项探不到都不能让 /api/env 挂掉 —— 前端全靠它渲染。"""
    info = {
        "hostname": "unknown", "platform": sys.platform, "os": "unknown",
        "python": "unknown", "is_admin": False, "is_windows": WINDOWS,
        "has_ssh": False, "has_powershell": False,
        "agent_path": "", "agent_exists": False,
        "output_dir": str(OUTPUT_DIR), "skill_dir": str(SKILL_DIR),
        "home": str(Path.home()), "log_dir": str(E.LOG_DIR),
        "degraded": [],
    }
    try:
        info["hostname"] = platform.node() or "unknown"
        info["os"] = platform.platform()
        info["python"] = platform.python_version()
        info["is_admin"] = is_admin()
        info["has_ssh"] = bool(shutil.which("ssh") or shutil.which("plink"))
        info["has_powershell"] = bool(shutil.which("powershell") or shutil.which("pwsh"))
    except Exception as e:  # noqa: BLE001
        info["degraded"].append(f"基础环境探测失败：{E.one_line(e)}")
    try:
        import linux_facts  # noqa: PLC0415

        info["agent_path"] = str(linux_facts.AGENT_PATH)
        info["agent_exists"] = linux_facts.AGENT_PATH.is_file()
    except Exception as e:  # noqa: BLE001
        info["degraded"].append(f"巡检探针模块不可用：{E.one_line(e)}")
    return info


def _pick_port(preferred: int = 8791) -> int:
    """挑一个能用的端口；允许端口号不合理时回退到默认值。"""
    try:
        preferred = int(preferred)
    except (TypeError, ValueError):
        preferred = 8791
    if not (1 <= preferred <= 65535):
        preferred = 8791
    for p in range(preferred, min(preferred + 40, 65536)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            break
    # 全占满就交给系统随便给一个
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv=None) -> int:
    """启动图形控制台。启动失败也必须有"人话 + 原因 + 处理"。"""
    try:
        E.set_stdio_utf8()
        E.install_hooks(app="ui")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="服务器安全维护、杀木马病毒skill 图形控制台")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--foreground", action="store_true",
                    help="前台运行（默认 Windows 下后台运行）")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    try:
        ensure_output_dir()
    except Exception as e:  # noqa: BLE001
        info = E.record(e, context="创建输出目录", app="ui")
        print(C.red(f"  启动失败：{info['cause']}"))
        print(f"  处理：{info['hint']}")
        print(C.gray(f"  日志：{info['log']}"))
        return 1

    try:
        port = _pick_port(args.port)
    except Exception as e:  # noqa: BLE001
        info = E.record(e, context="选择监听端口", app="ui")
        print(C.red(f"  无法确定可用端口：{info['cause']}"))
        print(f"  处理：{info['hint']}")
        return 1

    url = f"http://127.0.0.1:{port}/"
    try:
        httpd = ConsoleServer(("127.0.0.1", port), Handler)
    except OSError as e:
        info = E.record(e, context=f"绑定 127.0.0.1:{port}", app="ui")
        print(C.red(f"  控制台启动失败：{info['cause']}"))
        print(f"  处理：{info['hint']}")
        print(C.gray("  也可以换个端口：python cli.py ui --port 8899"))
        print(C.gray(f"  日志：{info['log']}"))
        return 1
    except Exception as e:  # noqa: BLE001
        info = E.record(e, context="创建控制台服务", app="ui")
        print(C.red(f"  控制台启动失败：{info['cause']}"))
        print(f"  处理：{info['hint']}")
        return 1

    SERVER_REF["server"] = httpd

    print(C.bold("服务器安全维护、杀木马病毒skill 控制台已启动"))
    print(f"  地址：{url}")
    print(f"  输出目录：{OUTPUT_DIR}")
    print(f"  错误日志：{E.LOG_DIR}")
    print(f"  权限：{'管理员/root ✓' if is_admin() else '普通用户（部分检查与加固不可用）'}")
    print(C.gray("  按 Ctrl+C 退出；或在页面上点「关闭控制台」。"))
    print(C.gray("  出错不会崩：任何异常都会写进错误日志，界面上「错误日志」页可查看。"))

    if not args.no_open:
        try:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:  # noqa: BLE001 —— 打不开浏览器不影响服务
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    except Exception as e:  # noqa: BLE001
        info = E.record(e, context="控制台主循环", app="ui")
        print(C.red(f"  主循环异常退出：{info['cause']}"))
        print(f"  处理：{info['hint']}")
        print(C.gray(f"  日志：{info['log']}"))
        return 1
    finally:
        try:
            httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
