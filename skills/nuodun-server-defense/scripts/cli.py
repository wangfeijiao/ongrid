#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py — 服务器安全维护、杀木马病毒skill 统一入口。

  doctor      环境自检（决定哪些能力可用）
  audit       体检（本机或远程 Linux/UNIX），出 JSON + HTML 报告
  perf/sec/malware/logs/crash   专项体检（audit 的快捷方式）
  backup      备份 / 校验 / 恢复 / 定时
  harden      加固方案生成与执行（默认只看不改）
  clean       硬盘瘦身：扫描 / 计划 / 隔离清理 / 还原（默认只扫不删）
  net         上网提速：网络只读诊断 / 生成调优脚本（默认不改配置）
  hunt        木马深度查杀：挖矿/Webshell/勒索/取证包（默认只读）
  report      由 JSON 重新渲染 HTML
  ui          启动图形控制台
  selftest    自检回归

约定：
  * 所有体检命令都是**只读**的。
  * clean scan 也只读；clean clean 默认进隔离区而非删除。
  * 只有 harden --apply / backup restore / clean clean --apply 会改系统，且都需要显式确认。

出错时的表现（本入口的硬性保证）：
  * **绝不把裸 traceback 甩给用户**。任何异常都会被翻译成
    「发生了什么 / 可能的原因 / 怎么处理 / 影响范围」四段中文说明。
  * 每条错误同时写进 outputs/logs/errors-YYYYMMDD.jsonl，
    用 `cli.py errors` 可以回看；加 `--debug` 可当场打印完整堆栈。
  * 子命令模块坏了（被删/被改坏）不影响其他子命令 —— 只有那一个命令报错。
  * 退出码见 SKILL.md：0 正常 / 1 环境或参数问题 / 2 执行失败 / 3 缺确认 / 130 人为中断。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import errors as E  # noqa: E402
from common import (  # noqa: E402
    ASSETS_DIR,
    C,
    OUTPUT_DIR,
    WINDOWS,
    ensure_output_dir,
    is_admin,
    print_findings,
    summarize,
)

SKILL_DIR = Path(__file__).resolve().parent.parent
DEBUG = False


# --------------------------------------------------------------------------
# 输出（统一的"汇报"口吻；出错时也走这里）
# --------------------------------------------------------------------------

def say(msg: str = "") -> None:
    """普通汇报。print 本身失败（管道被关/编码异常）时绝不让程序崩。"""
    try:
        print(msg)
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _report_error(exc: BaseException, args=None, context: str = "") -> dict:
    """统一错误出口：打印中文说明 + 落盘，返回诊断信息。"""
    ctx = context or _context_of(args)
    info = E.record(exc, context=ctx, app="cli")
    print()
    say(E.render(info, color=C.ON))
    if DEBUG:
        say(C.gray(info.get("traceback", "")))
    elif info.get("certain") is False:
        say(C.gray("  提示：这条原因属于推测。加 --debug 重跑可看到完整堆栈。"))
    print()
    return info


def _context_of(args) -> str:
    """从命令行参数推断场景，用于判断「是只读还是已经改了系统」。"""
    if args is None:
        return "cli 启动"
    cmd = getattr(args, "cmd", "") or ""
    action = getattr(args, "action", "") or ""
    scopes = []
    if getattr(args, "apply", False):
        scopes.append("apply")
    if getattr(args, "yes", False):
        scopes.append("yes")
    if getattr(args, "purge", False):
        scopes.append("purge")
    if getattr(args, "target", None):
        scopes.append("remote")
    tail = ("·" + ",".join(scopes)) if scopes else ""
    return f"{cmd} {action}".strip() + tail


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    rows: list[tuple[str, str, str]] = []  # (level, name, detail)

    def add(level, name, detail=""):
        rows.append((level, name, detail))

    v = sys.version_info
    add("OK" if v >= (3, 8) else "FAIL", "Python",
        f"{v.major}.{v.minor}.{v.micro}（需 ≥ 3.8）")

    add("OK" if is_admin() else "WARN", "权限",
        "管理员 / root（完整能力）" if is_admin() else
        "普通用户 —— 部分检查读不到（如 /etc/shadow、iptables、部分 CIM），加固也无法执行")

    add("OK", "平台", f"{sys.platform}")

    if WINDOWS:
        add("OK" if (shutil.which("powershell") or shutil.which("pwsh")) else "FAIL",
            "PowerShell", "用于读取 CIM / 防火墙 / Defender 状态；缺失将损失较多检查项")
        add("OK" if (shutil.which("netstat") and shutil.which("tasklist")) else "WARN",
            "netstat/tasklist", "用于监听端口与进程归属判定")
    else:
        add("OK" if shutil.which("sh") else "FAIL", "POSIX sh", "执行本机巡检探针")

    add("OK" if (shutil.which("ssh") or shutil.which("plink")) else "WARN",
        "SSH 客户端", "用于巡检远程 Linux/UNIX；缺失则只能体检本机")
    add("OK" if shutil.which("sshpass") else "WARN", "sshpass",
        "仅当你需要用密码（而非密钥）连接远程主机时才是必需")

    try:
        import linux_facts  # noqa: PLC0415

        ag = linux_facts.AGENT_PATH
        add("OK" if ag.is_file() else "FAIL", "巡检探针",
            str(ag) if ag.is_file() else f"{ag} —— 文件不存在，远程巡检不可用；"
                                        f"处理：重新安装技能包")
    except Exception as e:  # noqa: BLE001
        cause, hint, _c = E.explain(e)
        add("FAIL", "巡检探针", f"模块 linux_facts 不可用：{cause}；处理：{hint}")
    add("OK" if ASSETS_DIR.joinpath("ui.html").is_file() else "FAIL", "控制台界面",
        str(ASSETS_DIR / "ui.html") if ASSETS_DIR.joinpath("ui.html").is_file()
        else f"{ASSETS_DIR / 'ui.html'} 不存在 —— 图形控制台不可用（命令行功能不受影响）")

    try:
        ensure_output_dir()
        probe = OUTPUT_DIR / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("OK", "输出目录可写", str(OUTPUT_DIR))
    except Exception as e:  # noqa: BLE001
        cause, hint, _c = E.explain(e)
        add("FAIL", "输出目录不可写", f"{OUTPUT_DIR} —— {cause}；处理：{hint}")

    # 错误日志：出错时的第一条线索，能读能写才说明"出错不崩"的保障在工作
    try:
        files = E.log_files()
        total = sum(f["entries"] for f in files if f["entries"] > 0)
        if total:
            add("WARN", "错误日志",
                f"{E.LOG_DIR} 有 {total} 条历史错误 —— 用 `cli.py errors` 查看原因")
        else:
            add("OK", "错误日志", f"{E.LOG_DIR}（暂无错误记录）")
    except Exception as e:  # noqa: BLE001
        add("WARN", "错误日志", f"不可用：{type(e).__name__}: {e}")

    # 终端编码兜底：中文 Windows 上输出重定向会按 GBK 编码，符号打不出来会崩
    try:
        enc = getattr(sys.stdout, "encoding", "") or "?"
        replaceable = not getattr(sys.stdout, "errors", "") or \
            getattr(sys.stdout, "errors", "") == "replace"
        add("OK" if replaceable else "WARN", "终端编码",
            f"stdout={enc}，errors={'replace' if replaceable else 'strict（遇到打不出的字符可能中断）'}")
    except Exception:  # noqa: BLE001
        add("WARN", "终端编码", "无法探测")

    # 报告模块可导入性
    for mod in ("collect_win", "linux_facts", "linux_rules", "plan",
                "report", "backup", "harden", "clean", "net", "malware", "ui", "errors"):
        try:
            __import__(mod)
            add("OK", f"模块 {mod}", "")
        except Exception as e:  # noqa: BLE001
            cause, hint, _c = E.explain(e)
            add("FAIL", f"模块 {mod}", f"{cause}；处理：{hint}")

    ok = sum(1 for lv, _, _ in rows if lv == "OK")
    warn = sum(1 for lv, _, _ in rows if lv == "WARN")
    fail = sum(1 for lv, _, _ in rows if lv == "FAIL")

    if args.json:
        print(json.dumps({"ok": ok, "warn": warn, "fail": fail,
                          "checks": [{"level": l, "name": n, "detail": d} for l, n, d in rows]},
                         ensure_ascii=False, indent=2))
        return 0 if fail == 0 else 1

    print(C.bold("服务器安全维护、杀木马病毒skill 环境自检"))
    print(C.gray("=" * 68))
    for lv, name, detail in rows:
        tag = {"OK": C.green("[ OK ]"), "WARN": C.orange("[WARN]"),
               "FAIL": C.red("[FAIL]")}[lv]
        print(f"{tag} {name:<20} {detail}")
    print(C.gray("=" * 68))
    print(f"通过 {ok} · 警告 {warn} · 失败 {fail}")
    if fail:
        print(C.red("存在 [FAIL] 项，相关能力不可用，请先修复。"))
    elif warn:
        print(C.orange("存在 [WARN] 项：功能可降级运行，但结论覆盖度会下降 —— "
                       "报告里的「未覆盖清单」会如实列出。"))
    else:
        print(C.green("全部通过，可以开始体检。"))
    return 0 if fail == 0 else 1


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

def _build_audit_report(args, log_fn=print):
    from common import Report  # noqa: PLC0415

    target = getattr(args, "target", None)
    mode = "audit"
    if target:
        mode = "audit-ssh"
    rep = Report(mode=mode)
    t0 = time.time()

    if target:
        from linux_facts import Target, collect as lcollect  # noqa: PLC0415
        from linux_rules import apply_all  # noqa: PLC0415

        user_host, _, port = target.partition(":")
        user = "root"
        if "@" in user_host:
            user, _, user_host = user_host.partition("@")
        t = Target(kind="ssh", host=user_host, user=user or "root",
                   port=int(port or 22), key=getattr(args, "key", "") or "",
                   label=target)
        rep.host["hostname"] = t.display
        rep.note(f"远程目标 {t.display}：探针通过 SSH 管道下发执行，不在目标机落盘。")
        log_fn(C.gray(f"  · 连接 {t.display} 并下发只读巡检探针 ..."))
        facts = lcollect(t, deep=args.deep, timeout=args.timeout)
        if facts.error:
            log_fn(C.orange(f"  · 提示：{facts.error}"))
        log_fn(C.gray(f"  · 探针返回 {facts.total_lines} 行 / 解析 {len(facts.data)} 键 / "
                      f"失败 {facts.bad_lines} 行"))
        for cr in apply_all(facts, deep=args.deep):
            rep.add_result(cr)
    else:
        if WINDOWS:
            import collect_win  # noqa: PLC0415

            collect_win.collect_all(rep, scope=args.scope, quick=args.quick,
                                    log_hours=args.log_hours, deep=args.deep)
        else:
            from linux_facts import Target, collect as lcollect  # noqa: PLC0415
            from linux_rules import apply_all  # noqa: PLC0415

            log_fn(C.gray("  · 本机为 UNIX/Linux，执行本地巡检探针 ..."))
            facts = lcollect(Target(kind="local"), deep=args.deep, timeout=args.timeout)
            for cr in apply_all(facts, deep=args.deep):
                rep.add_result(cr)

    rep.finished = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    rep.duration_s = time.time() - t0
    return rep


def cmd_audit(args) -> int:
    scope = args.scope
    print(C.bold(f"体检 · 范围={scope} · "
                 f"目标={'远程 ' + args.target if args.target else '本机'}"))
    if not is_admin():
        print(C.orange("  提示：当前非管理员/root，部分检查项读不到，"
                       "结论覆盖度会下降（报告会列出未覆盖项）。"))
    print()

    try:
        rep = _build_audit_report(args)
    except Exception as e:  # noqa: BLE001 —— 采集中断也要说清原因，并给出缩小范围的办法
        info = E.record(e, context=f"体检采集（scope={scope}）", app="cli")
        print()
        print(C.red("  体检在采集阶段出错，已停在这一步（未改动系统）。"))
        print(f"  【错误】{info['type']}: {info['message']}")
        print(f"  【原因】{info['cause']}")
        print(f"  【处理】{info['hint']}")
        print(C.gray(f"  【日志】{info['log']}"))
        print(C.gray("  建议：先 `cli.py doctor` 确认环境；再用 --scope perf 逐步缩小范围重试。"))
        return 2

    # JSON 先落盘：后面就算 HTML 出问题，体检数据也不会白跑一趟
    try:
        json_path = rep.save(prefix=f"audit-{rep.mode}")
    except Exception as e:  # noqa: BLE001
        info = E.record(e, context="保存体检 JSON", app="cli")
        print(C.orange(f"  ⚠ 体检数据落盘失败：{info['cause']}"))
        print(C.gray(f"    处理：{info['hint']}"))
        json_path = Path("(未能保存)")

    print()
    print(C.bold("发现的问题"))
    print(C.gray("=" * 68))
    if not rep.findings:
        print(C.green("  未命中任何检查项。"))
    else:
        print_findings(rep.findings[: args.max_show])

    payload = summarize(rep, prefix=str(json_path))
    payload["json_file"] = str(json_path)

    if not args.no_html:
        # HTML 只是交付物之一；它出问题不能连累已经跑完的体检结论
        try:
            from report import write_report  # noqa: PLC0415

            html_path = write_report(rep.to_dict())
            payload["html_file"] = html_path
            print(C.gray("HTML: " + html_path))
            if args.open == "report":
                try:
                    import webbrowser  # noqa: PLC0415

                    webbrowser.open(Path(html_path).as_uri())
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            info = E.record(e, context="生成 HTML 报告", app="cli")
            print(C.orange("  ⚠ HTML 报告生成失败，但体检结论已完整保存在 JSON 里。"))
            print(f"     原因：{info['cause']}")
            print(f"     处理：{info['hint']}")
            print(C.gray(f"     可用 JSON 重出报告： python cli.py report \"{json_path}\""))
            print(C.gray(f"     错误日志：{info['log']}"))

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))

    # 高优先级时给出明确下一步
    c = rep.counts()
    if c["CRITICAL"]:
        print()
        print(C.red("  ⚠ 存在严重问题。建议顺序：① 先备份/做快照 ② 处理 P0 项 "
                    "③ 重跑体检确认分数提升。"))
    print()
    print(C.gray("  下一步："))
    print(C.gray("    · 看可视化： cli.py ui"))
    print(C.gray("    · 出加固方案： cli.py harden --profile baseline"))
    print(C.gray("    · 先做备份： cli.py backup run --dest <目录>"))
    return 0


# --------------------------------------------------------------------------
# ui / selftest
# --------------------------------------------------------------------------

def cmd_ui(args) -> int:
    import ui  # noqa: PLC0415

    argv = ["--port", str(args.port)]
    if args.no_open:
        argv.append("--no-open")
    return ui.main(argv)


def cmd_selftest(args) -> int:
    import selftest  # noqa: PLC0415

    return selftest.main(verbose=not args.quiet)


def cmd_targets(args) -> int:
    from backup import default_targets  # noqa: PLC0415

    print(C.bold("本机默认备份清单"))
    for t in default_targets():
        mark = "✓" if Path(t).expanduser().exists() else "·"
        print(f"  {mark} {t}")
    print()
    print(C.gray("说明：标 · 的路径本机不存在，备份时会自动跳过，不影响其他项。"))
    return 0



# --------------------------------------------------------------------------
# 子命令装配：一个模块坏了，不能让整个 CLI 都用不了
# --------------------------------------------------------------------------

def _make_broken_func(cmd_name: str, info: dict):
    def _broken(args) -> int:
        print()
        print(C.red(f"  子命令 `{cmd_name}` 暂不可用：它的模块加载失败了。"))
        print(f"  【原因】{info.get('cause', '')}")
        print(f"  【处理】{info.get('hint', '')}")
        print(C.gray(f"  【日志】{info.get('log', '')}"))
        print(C.gray("  其他子命令不受影响 —— 可以先跑 doctor 看看哪个模块坏了。"))
        print()
        return 1
    return _broken


def _register(sub, module_name: str, attr: str, cmd_name: str) -> None:
    """把某个子命令模块挂进解析器。

    模块被删/被改坏时：只把**这一个**子命令标记为不可用，
    其他子命令照常工作 —— 而不是整个 CLI 一启动就 ImportError。
    """
    try:
        before = set(getattr(sub, "choices", {}) or {})
        mod = importlib.import_module(module_name)
        getattr(mod, attr)(sub)
        return
    except Exception as e:  # noqa: BLE001
        info = E.record(e, context=f"装配子命令 {cmd_name}（模块 {module_name}）", app="cli")
        # 清掉半路注册进去的残缺 parser，避免留下一个"能选中但半残"的命令
        try:
            choices = getattr(sub, "choices", {}) or {}
            removed = set(choices) - before
            for name in removed:
                choices.pop(name, None)
            acts = getattr(sub, "_choices_actions", None)
            if isinstance(acts, list) and removed:
                sub._choices_actions = [a for a in acts
                                        if getattr(a, "dest", None) not in removed]
        except Exception:  # noqa: BLE001
            pass

    try:
        stub = sub.add_parser(cmd_name, help=f"[不可用] {module_name} 模块加载失败")
        stub.set_defaults(func=_make_broken_func(cmd_name, info))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """参数写错时说人话，并统一退出码（1 = 环境/参数问题）。

    argparse 默认会把一大段英文 usage 甩出来然后 exit(2)，
    对"只是想看看有什么命令"的用户很不友好，这里改成中文提示 + 指路。
    """

    def error(self, message):
        print(C.red(f"  参数有误：{message}"))
        print(C.gray("  先看能做什么： python cli.py --help"))
        print(C.gray("  常用命令： doctor / audit / backup / harden / clean / net / "
                     "hunt / report / errors / ui / selftest"))
        print(C.gray("  出错想看原因： python cli.py errors"))
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="server-ops",
        description="服务器 / 电脑 运维、性能优化与安全加固工具（默认只读）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""出错时怎么查：
  server-ops errors              # 看最近的报错原因与处理建议
  server-ops errors --last 30    # 多看几条
  server-ops --debug audit       # 当场打印完整堆栈
  server-ops doctor              # 先确认环境是否可用；模块坏了会点名

示例：
  server-ops doctor
  server-ops audit
  server-ops audit --scope sec
  server-ops audit --target root@192.168.1.10 --deep
  server-ops backup run --dest D:\\ops-backups
  server-ops backup verify-latest --dest D:\\ops-backups
  server-ops harden --profile webserver
  server-ops harden --profile baseline --apply --yes
  server-ops clean scan
  server-ops clean scan --big --path D:\\
  server-ops clean plan --categories thumb_cache,win_logs
  server-ops clean clean --categories thumb_cache --apply --yes
  server-ops net diag
  server-ops net tune
  server-ops hunt scan
  server-ops hunt forensics
  server-ops ui
""")
    ap.add_argument("--debug", action="store_true",
                    help="出错时把完整堆栈也打印出来（排查用；写在命令前后都可以）")
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    a = sub.add_parser("audit", help="体检（本机或远程 Linux/UNIX）")
    a.add_argument("--scope", default="all",
                   choices=["all", "perf", "sec", "malware", "log", "crash"])
    a.add_argument("--target", default=None, help="远程目标 user@host[:port]")
    a.add_argument("--key", default="", help="SSH 私钥路径")
    a.add_argument("--deep", action="store_true", help="深扫（慢，能挖出内存马/无文件持久化）")
    a.add_argument("--quick", action="store_true", help="减少采样时间")
    a.add_argument("--log-hours", type=int, default=48)
    a.add_argument("--timeout", type=int, default=300, help="远程探针超时秒数")
    a.add_argument("--no-html", action="store_true")
    a.add_argument("--open", choices=["report", "none"], default="none",
                   help="是否自动打开 HTML 报告")
    a.add_argument("--max-show", type=int, default=25, help="终端展示条目上限")
    a.add_argument("--json", action="store_true", help="额外输出一行 JSON 摘要")
    a.set_defaults(func=cmd_audit)

    for name, scope, help_text in (
        ("perf", "perf", "只看性能"),
        ("sec", "sec", "只看安全"),
        ("malware", "malware", "只看恶意代码与持久化"),
        ("logs", "log", "只看日志"),
        ("crash", "crash", "只看崩溃与稳定性"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--target", default=None)
        p.add_argument("--key", default="")
        p.add_argument("--deep", action="store_true")
        p.add_argument("--quick", action="store_true")
        p.add_argument("--log-hours", type=int, default=48)
        p.add_argument("--timeout", type=int, default=300)
        p.add_argument("--no-html", action="store_true")
        p.add_argument("--open", choices=["report", "none"], default="none")
        p.add_argument("--max-show", type=int, default=25)
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=cmd_audit, scope=scope)

    # 这些子命令各自一个模块：模块坏了只影响它自己，其他照常可用
    _register(sub, "backup", "add_backup_parser", "backup")
    _register(sub, "harden", "add_harden_parser", "harden")
    _register(sub, "report", "add_report_parser", "report")
    _register(sub, "clean", "add_clean_parser", "clean")
    _register(sub, "net", "add_net_parser", "net")
    _register(sub, "malware", "add_hunt_parser", "hunt")

    u = sub.add_parser("ui", help="启动图形控制台")
    u.add_argument("--port", type=int, default=8791)
    u.add_argument("--no-open", action="store_true")
    u.set_defaults(func=cmd_ui)

    t = sub.add_parser("targets", help="显示本机默认备份清单")
    t.set_defaults(func=cmd_targets)

    s = sub.add_parser("selftest", help="自检回归（不改动系统）")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_selftest)

    E.add_errors_parser(sub)

    return ap


def bootstrap() -> None:
    """任何命令执行前的第一步：终端兜底 + 全局异常钩子。绝不抛异常。"""
    try:
        E.set_stdio_utf8()
    except Exception:  # noqa: BLE001
        pass
    try:
        E.install_hooks(app="cli")
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    """统一入口。

    这里的 try/except 是**最后一道兜底**：任何未预期异常都会变成
    「中文原因 + 处理建议 + 影响范围 + 日志位置」，而不是裸 traceback。
    """
    global DEBUG
    bootstrap()

    raw = list(sys.argv[1:] if argv is None else argv)
    # --debug 写在命令前后都认：先摘出来，再解析
    if "--debug" in raw:
        DEBUG = True
        raw = [x for x in raw if x != "--debug"]

    ap = build_parser()
    try:
        args = ap.parse_args(raw)
    except SystemExit as e:  # --help / 参数错误的正常退出路径
        return int(e.code or 0)

    if not getattr(args, "func", None):
        ap.print_help()
        return 0

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print()
        print(C.orange("  已中断（Ctrl+C）。"))
        print(C.gray("  · 只读操作没有副作用，可以直接重跑；"
                     "变更类操作请用回滚脚本核对现状后再继续。"))
        return 130
    except BrokenPipeError:
        # 典型场景：输出被 | head 截断。不是故障，安静退出。
        return 0
    except SystemExit as e:  # 子命令内部主动退出
        return int(e.code or 0)
    except E.OpsError as e:
        _report_error(e, args, context=e.context or _context_of(args))
        return e.exit_code
    except Exception as e:  # noqa: BLE001 —— 兜底就是为这一刻存在的
        _report_error(e, args)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
