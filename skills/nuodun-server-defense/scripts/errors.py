#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
errors.py — 统一错误处理层（说清原因 · 绝不崩 · 留痕可查）。

为什么单独一个模块：
  * 本技能的定位是「给别人的生产机器做维护」。一个甩满屏 traceback 的报错，
    在运维现场等于什么都没说 —— 用户看不到「哪一步失败、为什么、接下来怎么办」。
  * 所以全技能统一走这里：**任何异常都被翻译成人话**（发生了什么 / 可能的原因 /
    怎么处理 / 影响范围），并写进 outputs/logs/ 的错误日志，供事后复盘。

三条硬约束（所有实现都要遵守）：
  1. **本模块自身绝不抛异常**。它坏掉了就等于兜底网破了 —— 所有函数
     内部 try/except 全覆盖，最坏情况返回一个"降级"的描述文本。
  2. 不依赖任何第三方库，也不能 import common（避免循环依赖）。
  3. 错误日志只落在本机 outputs/logs/，不外传。

对外主要接口：
  set_stdio_utf8()        终端编码兜底（中文/符号打不出来时降级为 ?，而不是崩）
  install_hooks()         安装 sys.excepthook / threading.excepthook
  diagnose(exc, ...)      异常 -> 结构化诊断信息
  record(exc, ...)        diagnose + 落盘 + 入内存环形缓冲
  render(info)            诊断信息 -> 终端可读的中文多段文本
  one_line(exc)           一行中文摘要（给 UI 的任务错误字段用）
  explain_rc(rc, ...)     外部脚本退出码 -> 原因（0/1/2/126/127/255…）
  read_log() / clear_log() / log_path()
  OpsError                主动抛出的、带原因和建议的业务异常
  guard() / attempt()     安全调用包装器
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# 路径（刻意不 import common，避免循环依赖）
# --------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = SKILL_DIR / "outputs"
LOG_DIR = OUTPUT_DIR / "logs"

MAX_LOG_BYTES = 2 * 1024 * 1024  # 单个日志文件上限，超过就滚动
MAX_TB_CHARS = 4000              # 单条堆栈最多存这么多字符

_RECENT: list[dict] = []         # 内存里的最近错误（给 /api/errors 用）
_RECENT_MAX = 50
_LOCK = threading.Lock()
_HOOKS_INSTALLED = False


# --------------------------------------------------------------------------
# 终端兜底：编码问题不该让程序崩掉
# --------------------------------------------------------------------------

def set_stdio_utf8() -> None:
    """给 stdout / stderr 加 errors='replace'。

    真实场景：中文 Windows 上把输出重定向到文件/管道时，Python 会按 GBK 编码写，
    遇到 ``✓`` ``⚠`` 这类字符直接抛 UnicodeEncodeError —— 看上去就是"程序崩了"，
    其实只是打不出来。加上 replace 后这类字符降级成 ``?``，程序继续跑。
    保留原编码（不强行改 UTF-8），这样 cmd.exe 里中文也不会变成乱码。
    """
    for name in ("stdout", "stderr"):
        try:
            s = getattr(sys, name, None)
            rc = getattr(s, "reconfigure", None)
            if callable(rc):
                rc(errors="replace")
        except Exception:  # noqa: BLE001 —— 兜底函数不许抛
            continue


# --------------------------------------------------------------------------
# 业务异常
# --------------------------------------------------------------------------

class OpsError(Exception):
    """带「原因 + 建议 + 影响」的业务异常。

    只在能明确说清原因的地方用；说不清的照旧抛原生异常，
    由 diagnose() 去猜（并标注为「推测」）。
    """

    def __init__(self, message: str, cause: str = "", hint: str = "",
                 context: str = "", exit_code: int = 2):
        super().__init__(message)
        self.message = message
        self.cause = cause
        self.hint = hint
        self.context = context
        self.exit_code = int(exit_code)


# --------------------------------------------------------------------------
# 异常 -> 人话
# --------------------------------------------------------------------------

# 文本特征 -> (原因, 处理建议)。用于外部命令 stderr 这类拿不到异常类型的情况。
_TEXT_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("permission denied", "access is denied", "拒绝访问", "需要管理员", "operation not permitted"),
     "权限不足 —— 当前账户没有读/写该对象的权限",
     "以管理员（Windows）或 root/sudo（Linux）重新运行；确认文件/服务的 ACL 或属主"),
    (("not found", "找不到", "没有那个文件", "cannot find", "no such file", "is not recognized",
      "不是内部或外部命令", "command not found"),
     "目标文件或命令不存在（路径拼错、未安装、或 PATH 里没有）",
     "核对路径拼写；确认对应软件/命令已安装并在 PATH 中"),
    (("no space left", "空间不足", "disk full", "磁盘已满", "insufficient disk"),
     "磁盘空间不足",
     "先跑 `cli.py clean scan` 看清空间去哪了，再按分级清单清理；或换一个剩余空间更大的目标目录"),
    (("timed out", "timeout", "超时", "connection timed out"),
     "操作超时（目标无响应 / 网络不通 / 被防火墙丢包）",
     "确认目标可达（ping、telnet 端口）；必要时加大 --timeout；排查防火墙与安全组"),
    (("connection refused", "拒绝连接", "actively refused"),
     "目标端口没有服务在监听（或只监听了别的地址）",
     "确认服务已启动、监听地址与端口正确（`netstat -ano` / `ss -lntp`）"),
    (("could not resolve", "name or service not known", "getaddrinfo", "临时失败",
      "无法解析", "dns"),
     "DNS 解析失败（域名写错 / DNS 服务器不可达）",
     "核对域名拼写；用 nslookup/dig 测一下当前 DNS；内网域名只能在域内 DNS 解析"),
    (("connection reset", "broken pipe", "10054", "远程主机强迫关闭"),
     "连接被对端或中间设备断开（会话超时 / 被安全设备切断）",
     "重试一次；若稳定复现，查中间防火墙、负载均衡的超时设置"),
    (("already in use", "address already in use", "10048", "已被占用", "端口占用"),
     "端口已被占用",
     "换一个端口（如 `--port 8792`），或找出占用进程（netstat -ano | findstr :端口 / ss -lntp）"),
    (("being used by another process", "另一个程序正在使用", "10032", "文件正被占用"),
     "文件被其他进程占用，无法写入/删除",
     "关掉占用它的进程（任务管理器 / handle.exe / lsof）后重试"),
    (("access to the registry key", "registry", "注册表"),
     "注册表访问失败（权限不足或键不存在）",
     "以管理员运行；确认目标键路径在当前系统版本上确实存在"),
    (("the service cannot be started", "服务无法启动", "service",
      "无法启动服务"),
     "服务启动失败（依赖项缺失 / 账户密码失效 / 被安全软件拦截）",
     "看服务日志与事件查看器；确认依赖服务、登录账户；安全软件可能拦截了加载"),
    (("ssh: connect", "permission denied (publickey", "host key verification failed"),
     "SSH 连接失败（网络不通 / 认证被拒 / 首次连接的 host key 未确认）",
     "确认 user@host:port 可连通；密钥认证请带 --key；首次连接先手工 ssh 一次确认指纹"),
    (("cannot execute binary file", "bad interpreter", "no such file or directory: /bin"),
     "脚本解析器不对或脚本带了 BOM（换行/编码问题）",
     "确认 shebang 指向的解释器存在；本技能生成的 .sh 不带 BOM，若被手工改成 CRLF 需转回 LF"),
    (("not supported", "不支持", "unknown", "无效"),
     "当前系统版本不支持该项操作",
     "该项可跳过；报告里会把该条标为「未生效」，不影响其他项"),
]

# 退出码 -> (原因, 建议)
_RC_RULES: dict[int, tuple[str, str]] = {
    0: ("成功", ""),
    1: ("命令已执行但报了错（通用错误）",
        "看它上面输出的具体错误行；多数是权限不足、参数不对或目标对象不存在"),
    2: ("命令行用法/参数错误，或脚本自身用 2 表示「有步骤未成功」",
        "核对参数拼写；若是本技能生成的脚本，上面会逐项列出哪一步没成功"),
    126: ("命令找到了但没有执行权限", "对脚本执行 chmod +x，或以 sh 脚本名 的方式运行"),
    127: ("命令不存在（PATH 里找不到）", "确认该命令已安装；Windows 上确认路径正确"),
    128: ("退出码超出常规范围，或进程被信号终止", "看系统日志里是否有 OOM、被 kill 的记录"),
    130: ("被 Ctrl+C 中断", "这是人为中断，不是故障；重新跑一次即可"),
    137: ("进程被强制杀掉（常见：内存不足被 OOM Killer 干掉）",
          "确认内存与 swap；必要时调小并发或加内存"),
    255: ("SSH 连接失败（认证被拒或网络不通）",
          "确认 user@host:port 可达、密钥正确、目标 sshd 允许该认证方式"),
}


def _exc_text(exc: BaseException) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:  # noqa: BLE001
        return "未知异常"


def _has_cjk(text: str) -> bool:
    """消息里有没有汉字 —— 有就说明这是工具写给用户看的说明，不是库的英文报错。"""
    try:
        return any("\u4e00" <= ch <= "\u9fff" for ch in (text or "")[:200])
    except Exception:  # noqa: BLE001
        return False


def _errno_of(exc: BaseException) -> tuple[int, int]:
    """尽力取出 (errno, winerror)。"""
    errno = 0
    winerr = 0
    try:
        errno = int(getattr(exc, "errno", 0) or 0)
    except Exception:  # noqa: BLE001
        errno = 0
    try:
        winerr = int(getattr(exc, "winerror", 0) or 0)
    except Exception:  # noqa: BLE001
        winerr = 0
    return errno, winerr


_WIN_ERRNO = {
    2: "系统找不到指定的文件",
    3: "系统找不到指定的路径",
    5: "拒绝访问（权限不足）",
    32: "文件正被另一个进程占用",
    112: "磁盘空间不足",
    123: "文件名或路径无效",
    1314: "缺少所需的特权（需要以管理员身份运行）",
    10013: "端口/套接字被拒绝访问（被防火墙或权限拦截）",
    10048: "端口已被占用",
    10049: "无法分配请求的地址（本机没有这个 IP）",
    10054: "对端强制关闭了连接",
    10060: "连接超时（目标不可达或被防火墙丢包）",
    10061: "目标主动拒绝连接（该端口没有服务在监听）",
    1053: "服务没有及时响应启动或控制请求",
    1069: "服务启动失败：登录账户或密码无效",
}


def _cause_from_text(text: str) -> tuple[str, str] | None:
    low = text.lower()
    for keys, cause, hint in _TEXT_RULES:
        for k in keys:
            if k in low:
                return cause, hint
    return None


def explain(exc: BaseException) -> tuple[str, str, bool]:
    """返回 (原因, 处理建议, 是否确定)。

    第三个值表示这条原因是**确定的**（异常类型/错误码直接对应）还是
    **推测的**（靠文本特征猜的）—— 猜的必须让用户知道是猜的。
    """
    if isinstance(exc, OpsError):
        return (exc.cause or exc.message, exc.hint or "按上面的说明处理后重试", True)

    errno, winerr = _errno_of(exc)
    msg = ""
    try:
        msg = str(exc)
    except Exception:  # noqa: BLE001
        msg = ""

    # ---- 1. 精确错误码优先 ----
    if winerr and winerr in _WIN_ERRNO:
        detail = _WIN_ERRNO[winerr]
        hint = _hint_for_code(winerr)
        return f"系统错误 {winerr}：{detail}", hint, True
    if errno:
        if errno == 2:
            return ("路径或命令不存在（ENOENT）",
                    "核对路径拼写；确认命令已安装", True)
        if errno == 13:
            return ("权限不足（EACCES/EPERM）",
                    "以管理员或 root/sudo 运行；检查文件属主与权限位", True)
        if errno == 28:
            return ("磁盘空间不足（ENOSPC）",
                    "先 `cli.py clean scan` 定位占用，再按清单清理或换目标目录", True)
        if errno == 30:
            return ("文件系统只读（EROFS），无法写入",
                    "确认挂载选项；把输出目录改到可写分区", True)
        if errno == 98:
            return ("端口已被占用（EADDRINUSE）",
                    "换端口，或先停掉占用该端口的进程", True)
        if errno in (110, 111):
            return ("连接超时 / 被拒绝（网络层）",
                    "确认目标可达、端口开放、防火墙与安全组放行", True)
        if errno == 12:
            return ("系统内存不足（ENOMEM）",
                    "关掉占内存的进程，或加内存/swap 后重试", True)

    # ---- 2. 异常类型 ----
    if isinstance(exc, PermissionError):
        return ("权限不足：当前账户没有权限读/写该对象",
                "以管理员（Windows）或 root/sudo（Linux）重新运行", True)
    if isinstance(exc, FileNotFoundError):
        return ("路径或命令不存在",
                "核对路径拼写；确认对应软件已安装、且在当前用户可见的 PATH 中", True)
    if isinstance(exc, IsADirectoryError):
        return ("把目录当文件用了", "检查参数：这里需要文件路径，不是目录", True)
    if isinstance(exc, NotADirectoryError):
        return ("路径中间有一段不是目录", "检查路径拼写，逐级确认存在", True)
    if isinstance(exc, FileExistsError):
        return ("目标已存在，操作被拒绝", "换个名字/目录，或先清理已有对象", True)
    if isinstance(exc, MemoryError):
        return ("内存不足：程序拿不到需要的内存",
                "关掉其他占内存的进程后重试；本机内存较小的请加 --quick 降低采样量", True)
    if isinstance(exc, RecursionError):
        return ("递归过深（数据里可能有环或异常嵌套）",
                "把出问题的数据文件路径发给维护者；临时可换一个较小的输入重试", True)
    if isinstance(exc, UnicodeDecodeError):
        return (f"文件/输出编码不是 UTF-8（实际按 {getattr(exc, 'encoding', '未知')} 解不出来）",
                "确认文件真实编码；本技能对外部命令输出已做多编码回退，若仍失败请提供该文件", True)
    if isinstance(exc, UnicodeEncodeError):
        return ("终端编码打不出某些字符（不是功能故障）",
                "本技能已自动降级为 ?；如需正常显示，把控制台切到 UTF-8（chcp 65001）", True)
    if isinstance(exc, json.JSONDecodeError):
        return ("JSON 数据文件损坏或不是合法 JSON",
                "确认文件没有被截断或手工编辑错；重新跑一次生成该文件的命令", True)
    if isinstance(exc, ModuleNotFoundError):
        missing = getattr(exc, "name", "") or ""
        return (f"缺少 Python 模块（{missing or '未知'}）",
                "确认技能目录完整；本技能只用标准库，多数情况是技能被不完整复制", True)
    if isinstance(exc, ImportError):
        return ("模块导入失败（技能文件可能不完整或被改动）",
                "重新安装技能包；或跑 `python scripts/cli.py doctor` 看哪个模块坏了", True)

    try:
        import socket as _socket

        if isinstance(exc, _socket.gaierror):
            return ("域名解析失败（DNS 查不到这个主机）",
                    "核对域名拼写；测一下当前 DNS（nslookup / dig）；内网域名要用内网 DNS", True)
        if isinstance(exc, _socket.timeout):
            return ("网络操作超时", "确认目标可达、端口开放；必要时加大超时时间", True)
    except Exception:  # noqa: BLE001
        pass

    if isinstance(exc, TimeoutError):
        return ("操作超时：目标在约定时间内没有响应",
                "确认目标可达；加大 --timeout；排查网络与对端负载", True)
    if isinstance(exc, ConnectionRefusedError):
        return ("连接被拒绝：目标端口没有服务在监听",
                "确认服务已启动并监听正确地址与端口（netstat -ano / ss -lntp）", True)
    if isinstance(exc, ConnectionResetError):
        return ("连接被对端断开", "重试一次；稳定复现则查中间防火墙/负载均衡的超时策略", True)
    if isinstance(exc, BrokenPipeError):
        return ("输出管道被关闭（一般是下游先退出了）",
                "这是无害的：正常退出即可；如果用 | head 造成，可忽略", True)
    if isinstance(exc, InterruptedError):
        return ("系统调用被信号中断", "重试即可", True)

    try:
        import subprocess as _sp

        if isinstance(exc, _sp.TimeoutExpired):
            cmd = getattr(exc, "cmd", "")
            t = getattr(exc, "timeout", "")
            return (f"外部命令超过 {t} 秒没返回（{str(cmd)[:80]}）",
                    "确认该命令不会卡住；必要时加大超时；远程任务检查网络与目标负载", True)
        if isinstance(exc, _sp.CalledProcessError):
            rc = getattr(exc, "returncode", -1)
            cause, hint = _RC_RULES.get(rc, (f"外部命令返回非零退出码 {rc}", "看它的错误输出"))
            return cause, hint, True
    except Exception:  # noqa: BLE001
        pass

    if isinstance(exc, OSError):
        return (f"系统调用失败（OSError，错误码 {errno or '未知'}）",
                "按错误码判断：5/13=权限，2=路径不存在，28=空间不足，32=文件被占用", True)

    if isinstance(exc, (ValueError, TypeError, KeyError, IndexError, AttributeError)):
        # 本技能大量使用「带中文说明的异常」做业务性拒绝（例如"找不到扫描结果，请重新扫描"）。
        # 这类信息本身就是原因，不该被当成"程序内部问题"。
        if _has_cjk(msg):
            return (f"工具主动中止了这次操作：{msg}",
                    "按上面这句话补齐前置条件后重试（例如先做一次扫描再出计划）；"
                    "如果确认前置步骤都做过，请把这条日志提供给维护者", True)
        return ("参数或数据不符合预期（属于程序内部判定问题，不是你的操作错）",
                "把上面这条报错和你的执行命令发给维护者；错误日志里已有完整堆栈", False)

    if isinstance(exc, AssertionError):
        return ("内部自检未通过（说明输入数据超出了预期范围）",
                "把出问题的输入发给维护者；错误日志里有完整堆栈", False)

    # ---- 3. 文本兜底 ----
    hit = _cause_from_text(f"{type(exc).__name__} {msg}")
    if hit:
        return hit[0], hit[1], False

    return ("未能自动识别这个错误（属于未预期的情况）",
            "错误日志里已保存完整堆栈，请把它提供给维护者；先按「暂时可用」的办法继续："
            "换用其他子命令（如先跑 doctor / audit --scope perf）", False)


def _hint_for_code(winerr: int) -> str:
    return {
        5: "以管理员身份重新运行（右键 → 以管理员身份运行，或 sudo）",
        32: "用任务管理器 / handle.exe / lsof 找出占用它的进程并关闭后重试",
        112: "先 `cli.py clean scan` 清理空间，或把目标目录改到其他盘",
        1314: "以管理员身份运行（本操作需要「备份文件和目录」等特权）",
        10013: "换一个端口，或临时关闭拦截它的安全软件/防火墙规则",
        10048: "换一个端口（如 `--port 8792`），或先停掉占用进程",
        10049: "本机没有这个 IP —— 检查监听的绑定地址写错了没有",
        10054: "重试；若稳定复现，查中间设备（防火墙/负载均衡）的超时设置",
        10060: "确认目标可达（ping）、端口开放、安全组放行；必要时加大超时",
        10061: "确认服务已启动；用 netstat -ano | findstr :端口 看是否有监听",
        1053: "看服务日志与事件查看器；确认依赖服务与登录账户是否有效",
        1069: "在「服务」里重新填写登录账户与密码（常见于改过系统密码之后）",
    }.get(winerr, "按错误码含义处理；如不确定，把这条错误日志提供给维护者")


def explain_rc(rc: int, stderr: str = "") -> tuple[str, str]:
    """给外部脚本/命令的退出码+stderr 生成 (原因, 建议)。"""
    try:
        rc = int(rc)
    except Exception:  # noqa: BLE001
        rc = -1
    if rc == 0:
        return "成功", ""
    hit = _cause_from_text(stderr or "")
    if hit:
        return hit[0], hit[1]
    if rc in _RC_RULES:
        return _RC_RULES[rc]
    if rc < 0:
        return (f"进程被信号终止（rc={rc}）", "查系统日志是否有 OOM 或被安全软件拦截")
    return (f"命令返回非零退出码 {rc}（未识别到更具体的原因）",
            "看它输出的错误行；本技能生成的脚本会在失败处打印具体原因")


# --------------------------------------------------------------------------
# 诊断信息
# --------------------------------------------------------------------------

# 出现这些关键字就认为「可能已经改了系统」，报错时要说清影响面
_MUTATING_HINTS = ("apply", "clean", "purge", "restore", "harden", "block",
                   "kill", "tune", "install", "加固", "清理", "恢复", "阻断", "处置")


def impact_of(context: str) -> str:
    low = (context or "").lower()
    if any(k in low for k in _MUTATING_HINTS):
        return ("本次属于「会改系统」的操作，可能已部分生效 —— 请用对应的回滚脚本 / "
                "隔离区还原脚本收尾，然后重跑一次确认现状。")
    return "本次属于「只读」操作，没有改动系统，可以直接重试。"


def log_path(ts: datetime | None = None) -> Path:
    t = ts or datetime.now()
    return LOG_DIR / f"errors-{t.strftime('%Y%m%d')}.jsonl"


def diagnose(exc: BaseException, context: str = "", app: str = "skill",
             tb=None) -> dict:
    """把异常整理成结构化诊断（不落盘、不打印）。本函数绝不抛异常。"""
    now = datetime.now()
    try:
        cause, hint, certain = explain(exc)
    except Exception:  # noqa: BLE001
        cause, hint, certain = "错误解析本身失败了（降级显示）", "请把完整堆栈发给维护者", False
    try:
        tb_text = "".join(traceback.format_exception(type(exc), exc,
                                                     tb or exc.__traceback__))[-MAX_TB_CHARS:]
    except Exception:  # noqa: BLE001
        tb_text = "(堆栈不可用)"
    return {
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "app": app,
        "context": context or "未标注场景",
        "type": type(exc).__name__,
        "message": _safe_str(exc),
        "cause": cause,
        "certain": bool(certain),
        "hint": hint,
        "impact": impact_of(context),
        "traceback": tb_text,
        "log": str(log_path(now)),
    }


def _safe_str(v) -> str:
    try:
        s = str(v)
    except Exception:  # noqa: BLE001
        return "（内容无法转成文本）"
    return s if len(s) <= 2000 else s[:2000] + " …(已截断)"


def _append(info: dict) -> None:
    """追加一条 JSON 行到当天错误日志。失败静默（兜底函数不许再抛）。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = Path(info.get("log") or log_path())
        try:
            if p.is_file() and p.stat().st_size > MAX_LOG_BYTES:
                p.replace(p.with_suffix(".jsonl.1"))
        except Exception:  # noqa: BLE001
            pass
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def record(exc: BaseException, context: str = "", app: str = "skill",
           tb=None) -> dict:
    """诊断 + 落盘 + 记入内存。返回诊断信息（永远不抛）。"""
    info = diagnose(exc, context=context, app=app, tb=tb)
    _append(info)
    try:
        with _LOCK:
            _RECENT.append(info)
            if len(_RECENT) > _RECENT_MAX:
                del _RECENT[0:len(_RECENT) - _RECENT_MAX]
    except Exception:  # noqa: BLE001
        pass
    return info


def recent() -> list[dict]:
    """本次进程内最近的错误（新→旧）。"""
    try:
        with _LOCK:
            return list(reversed(_RECENT))
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def one_line(exc: BaseException, with_cause: bool = True) -> str:
    """一行摘要：类型 + 内容（+ 原因）。给 UI 的 error 字段、日志行用。"""
    try:
        base = _exc_text(exc)
        if not with_cause:
            return base
        cause, _hint, _c = explain(exc)
        return f"{base} —— 原因：{cause}"
    except Exception:  # noqa: BLE001
        return "未知错误（详情见错误日志）"


def render(info: dict, color: bool = True, width: int = 78) -> str:
    """把诊断信息渲染成终端可读的中文文本（多段，不带 traceback）。"""
    def _c(code: str, s: str) -> str:
        if not color:
            return s
        return f"\033[{code}m{s}\033[0m"

    try:
        lines = []
        lines.append(_c("1;31", "═" * width))
        lines.append(_c("1;31", "  出错了 —— 已安全停下，没有继续做后续动作"))
        lines.append(_c("1;31", "═" * width))
        lines.append(f"  【场景】  {info.get('context', '')}")
        lines.append(f"  【错误】  {info.get('type', '')}: {info.get('message', '')}")
        tag = "确定" if info.get("certain") else "推测"
        lines.append(f"  【原因·{tag}】{info.get('cause', '')}")
        lines.append(f"  【处理】  {info.get('hint', '')}")
        lines.append(f"  【影响】  {info.get('impact', '')}")
        lines.append(f"  【日志】  {info.get('log', '')}")
        lines.append(_c("90", "           完整堆栈已写入上面这个文件；"
                              "查看：python cli.py errors ；加 --debug 可直接打印堆栈"))
        lines.append(_c("1;31", "═" * width))
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return f"出错了：{info.get('type')}: {info.get('message')}（渲染失败，见错误日志）"


def render_exc(exc: BaseException, context: str = "", app: str = "skill",
               color: bool = True) -> str:
    return render(diagnose(exc, context=context, app=app), color=color)


# --------------------------------------------------------------------------
# 全局钩子
# --------------------------------------------------------------------------

def install_hooks(app: str = "skill") -> None:
    """安装全局异常钩子：未捕获异常也变成「中文说明 + 落盘」，而不是裸 traceback。"""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True

    def _hook(exc_type, exc, tb_):
        if issubclass(exc_type, KeyboardInterrupt):
            try:
                sys.stderr.write("\n已中断（Ctrl+C）。\n")
            except Exception:  # noqa: BLE001
                pass
            return
        info = record(exc, context=f"{app} 未捕获异常", app=app, tb=tb_)
        try:
            sys.stderr.write("\n" + render(info) + "\n")
        except Exception:  # noqa: BLE001
            pass

    try:
        sys.excepthook = _hook
    except Exception:  # noqa: BLE001
        pass

    try:
        def _thread_hook(args):
            if issubclass(args.exc_type, SystemExit):
                return
            info = record(args.exc_value, context=f"{app} 后台线程",
                          app=app, tb=args.exc_traceback)
            try:
                sys.stderr.write("\n" + render(info) + "\n")
            except Exception:  # noqa: BLE001
                pass

        threading.excepthook = _thread_hook
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# 调用包装器
# --------------------------------------------------------------------------

def guard(context: str = "", default=None, on_error=None):
    """装饰器：异常不外溢，自动记录并返回 default（或 on_error 的结果）。"""
    def deco(fn):
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                info = record(e, context=context or getattr(fn, "__name__", "?"))
                if on_error is not None:
                    try:
                        return on_error(e, info)
                    except Exception:  # noqa: BLE001
                        return default
                return default
        try:
            wrapper.__name__ = getattr(fn, "__name__", "wrapper")
            wrapper.__doc__ = getattr(fn, "__doc__", None)
        except Exception:  # noqa: BLE001
            pass
        return wrapper
    return deco


def attempt(fn, *a, context: str = "", **kw):
    """调用 fn，返回 (ok, 结果或None, 诊断信息或None)。"""
    try:
        return True, fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001
        return False, None, record(e, context=context or getattr(fn, "__name__", "?"))


# --------------------------------------------------------------------------
# 错误日志读取
# --------------------------------------------------------------------------

def _iter_log_files(all_days: bool = False) -> list[Path]:
    try:
        if not LOG_DIR.is_dir():
            return []
        files = sorted(LOG_DIR.glob("errors-*.jsonl"))
        return files if all_days else files[-1:]
    except Exception:  # noqa: BLE001
        return []


def read_log(limit: int = 10, all_days: bool = False) -> list[dict]:
    """读取错误日志（新→旧）。文件坏了也不会抛异常，坏行直接跳过。"""
    out: list[dict] = []
    try:
        for p in reversed(_iter_log_files(all_days)):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:  # noqa: BLE001
                continue
            for line in reversed(raw):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
                if len(out) >= limit:
                    return out
    except Exception:  # noqa: BLE001
        pass
    return out


def log_files() -> list[dict]:
    """列出全部错误日志文件及条目数。"""
    infos = []
    try:
        files = sorted(LOG_DIR.glob("errors-*.jsonl")) if LOG_DIR.is_dir() else []
        for p in files:
            cnt = 0
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    for _ in f:
                        cnt += 1
            except Exception:  # noqa: BLE001
                cnt = -1
            try:
                size = p.stat().st_size
            except Exception:  # noqa: BLE001
                size = 0
            infos.append({"file": str(p), "name": p.name, "entries": cnt, "size": size})
    except Exception:  # noqa: BLE001
        pass
    return infos


def clear_log(keep_days: int = 0) -> int:
    """清空错误日志。keep_days=0 表示全部清掉；返回删除的文件数。"""
    n = 0
    try:
        files = sorted(LOG_DIR.glob("errors-*.jsonl"))
        if keep_days > 0:
            files = files[:-keep_days]
        for p in files:
            try:
                p.unlink()
                n += 1
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        with _LOCK:
            _RECENT.clear()
    except Exception:  # noqa: BLE001
        pass
    return n


# --------------------------------------------------------------------------
# CLI：查看 / 清理错误日志
# --------------------------------------------------------------------------

def cmd_errors(args) -> int:
    import common

    app = os.path.basename(sys.argv[0]) or "cli.py"
    if getattr(args, "clear", False):
        n = clear_log()
        print(common.C.green(f"  已清理 {n} 个错误日志文件。"))
        return 0

    items = read_log(limit=int(getattr(args, "last", 10) or 10),
                     all_days=bool(getattr(args, "all_days", False)))
    if getattr(args, "json", False):
        print(json.dumps({"items": items, "files": log_files()},
                         ensure_ascii=False, indent=2))
        return 0

    print(common.C.bold("错误日志"))
    print(common.C.gray("=" * 68))
    infos = log_files()
    if infos:
        for f in infos[-7:]:
            print(common.C.gray(f"  {f['name']}  {f['entries']} 条  {f['size']} 字节"))
    else:
        print(common.C.gray(f"  暂无错误日志（{LOG_DIR}）。这是好事 —— 说明没出过错。"))
    print()
    if not items:
        print(common.C.green("  最近没有错误记录。"))
        print(common.C.gray("  提示：真出错时，本工具会把「原因 + 处理建议 + 完整堆栈」"
                            "写进上面这些文件，并在这里展示。"))
        return 0

    for it in items:
        sev = common.C.red("●") if not it.get("certain", True) else common.C.orange("●")
        print(f"{sev} {it.get('ts','')}  {it.get('context','')}")
        print(f"    {it.get('type','')}: {it.get('message','')}")
        print(f"    原因：{it.get('cause','')}")
        print(f"    处理：{it.get('hint','')}")
        print()
    print(common.C.gray(f"  查看完整堆栈：直接打开 {items[0].get('log','')}"))
    print(common.C.gray("  清空日志：python cli.py errors --clear"))
    print(common.C.gray(f"  （进程：{app}）"))
    return 0


def add_errors_parser(sub) -> None:
    p = sub.add_parser("errors", help="查看本工具的错误日志（出错原因都记在这里）")
    p.add_argument("--last", type=int, default=10, help="显示最近 N 条（默认 10）")
    p.add_argument("--all", dest="all_days", action="store_true", help="跨天读取全部日志")
    p.add_argument("--clear", action="store_true", help="清空错误日志")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_errors)


# 模块导入即给终端上保险（任何 entrypoint import 到本模块都会受益）
try:
    set_stdio_utf8()
except Exception:  # noqa: BLE001
    pass


if __name__ == "__main__":
    from common import C  # noqa: PLC0415

    print(C.bold("errors.py — 统一错误处理层（由 cli.py 调用）"))
    print(C.gray(f"  日志目录：{LOG_DIR}"))
