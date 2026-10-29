#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup.py — 开跑前的一次性准备。

本技能**只用 Python 标准库**，所以没有 pip 依赖要装。setup 要做的是：
  1. 校验 Python 版本
  2. 建立 outputs / snapshots 目录
  3. 给 shell 探针与脚本补可执行位（Linux/macOS）
  4. 检查可选的系统命令（ssh / PowerShell / sshpass / plink）
  5. 打印当前环境下「能做 / 不能做」的清单

用法：
  python setup.py            # 准备 + 体检
  python setup.py --check    # 只看不建
"""

from __future__ import annotations

import argparse
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import C, OUTPUT_DIR, SKILL_DIR, WINDOWS, ensure_output_dir, is_admin

SCRIPTS = SKILL_DIR / "scripts"


def _chmod_exec(p: Path) -> None:
    if WINDOWS or not p.is_file():
        return
    try:
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不创建目录")
    args = ap.parse_args()

    print(C.bold("服务器安全维护、杀木马病毒skill 环境准备"))
    print(C.gray("=" * 68))

    ok = True

    # 1. Python
    v = sys.version_info
    if v >= (3, 8):
        print(C.green(f"[ OK ] Python {v.major}.{v.minor}.{v.micro}"))
    else:
        print(C.red(f"[FAIL] Python {v.major}.{v.minor} 过低，需要 3.8+"))
        ok = False

    # 2. 目录
    if not args.check:
        ensure_output_dir()
        (OUTPUT_DIR / "snapshots").mkdir(parents=True, exist_ok=True)
    print(C.green(f"[ OK ] 输出目录 {OUTPUT_DIR}"))

    # 3. 探针可执行位（巡检探针 + 网络探针）
    for name in ("linux_agent.sh", "net_agent.sh"):
        agent = SCRIPTS / name
        if not args.check:
            _chmod_exec(agent)
        print(C.green(f"[ OK ] 探针 {name}"
                      f"{'' if agent.is_file() else '（缺失！）'}"))

    # 4. 可选命令
    print()
    print(C.bold("系统命令检查"))
    checks = [
        ("ssh", "巡检远程 Linux/UNIX 主机", False),
        ("plink", "Windows 上的 SSH 替代（PuTTY）", False),
        ("sshpass", "用密码而非密钥连接远程主机", False),
        ("powershell", "Windows 侧深度采集（CIM/防火墙/Defender）", False),
        ("pwsh", "PowerShell 7（可选）", False),
        ("sh", "执行本机（UNIX）巡检探针", False),
    ]
    for cmd, why, required in checks:
        found = shutil.which(cmd)
        if found:
            print(C.green(f"[ OK ] {cmd:<12} {found}"))
        else:
            lv = C.red("[FAIL]") if required else C.orange("[WARN]")
            print(f"{lv} {cmd:<12} 未找到 —— {why}")
            if required:
                ok = False

    # 5. 能力清单
    print()
    print(C.bold("当前环境能力"))
    can, cannot = [], []
    if WINDOWS:
        can.append("Windows 本机全量体检")
        cannot.append("本机巡检（本机已是 Windows）")
    else:
        can.append("UNIX/Linux 本机巡检")
    can.append("远程 Linux/UNIX 巡检" if shutil.which("ssh") or shutil.which("plink")
               else "（无 SSH 客户端）")
    can.append("备份 / 校验 / 恢复 / 定时脚本生成")
    can.append("加固方案生成（默认不改系统）")
    can.append("硬盘瘦身：只读扫描 + 分级清单 + 隔离区可还原")
    can.append("上网提速：只读诊断（网卡/DNS/丢包/TCP/BBR/偷流量）+ 可回滚调优脚本")
    can.append("木马深度查杀：挖矿/Webshell/勒索/隐藏持久化 + 应急取证包")
    can.append("图形控制台（python cli.py ui）")
    if is_admin():
        can.append("加固执行（当前具备管理员/root 权限）")
    else:
        cannot.append("加固执行与部分深度检查（需要管理员/root）")
    if not (shutil.which("ssh") or shutil.which("plink")):
        cannot.append("远程主机巡检（缺 ssh / plink）")

    for x in can:
        if x:
            print(C.green(f"  ✓ {x}"))
    for x in cannot:
        print(C.orange(f"  ✗ {x}"))

    print()
    print(C.gray("=" * 68))
    print(C.gray("下一步："))
    print(C.gray("  python scripts/cli.py doctor        # 详细自检"))
    print(C.gray("  python scripts/cli.py audit         # 全面体检"))
    print(C.gray("  python scripts/cli.py clean scan    # 硬盘瘦身（只读）"))
    print(C.gray("  python scripts/cli.py net diag      # 上网提速（只读）"))
    print(C.gray("  python scripts/cli.py hunt scan      # 木马查杀（只读）"))
    print(C.gray("  python scripts/cli.py ui            # 图形控制台"))
    print(C.gray("  python scripts/selftest.py          # 自检回归"))
    return 0 if ok else 1


if __name__ == "__main__":
    # 入口兜底：任何异常都翻译成「原因 + 处理」，不留裸 traceback
    try:
        import errors as E

        E.set_stdio_utf8()
        E.install_hooks(app="setup")
    except Exception:  # noqa: BLE001
        E = None  # type: ignore[assignment]
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n已中断。")
        raise SystemExit(130)
    except Exception as _e:  # noqa: BLE001
        if E is not None:
            print()
            print(E.render(E.record(_e, context="setup 准备阶段", app="setup"),
                           color=C.ON))
        else:
            print(f"出错了：{type(_e).__name__}: {_e}")
        raise SystemExit(2)
