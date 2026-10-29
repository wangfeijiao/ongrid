#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境自检 / 一键安装 network-probe 的可选依赖。

本技能大部分功能（自动 ping、ARP 扫描、设备诊断）零依赖、纯标准库即可运行。
仅"交换机 SNMP 定位端口 / 关闭端口断网"需要 pysnmp。

安装到 WorkBuddy 托管 venv（不污染系统环境）：
  C:/Users/12168/.workbuddy/binaries/python/envs/network-probe/

用法：
  python env_setup.py            # 检查 + 缺啥装啥（幂等）
  python env_setup.py --check    # 只检查不安装
"""
import subprocess
import sys
import os

PY_MGR = r"C:\Users\12168\.workbuddy\binaries\python\versions\3.13.12\python.exe"
VENV = r"C:\Users\12168\.workbuddy\binaries\python\envs\network-probe"

NEEDED = {"pysnmp": "pysnmp"}  # 仅交换机 SNMP 集成需要


def venv_python():
    p = os.path.join(VENV, "Scripts", "python.exe")
    return p if os.path.exists(p) else None


def ensure_venv():
    if venv_python():
        return venv_python()
    print(f"[1/3] 创建 venv → {VENV}")
    r = subprocess.run([PY_MGR, "-m", "venv", VENV])
    if r.returncode != 0 or not venv_python():
        print("[error] venv 创建失败", file=sys.stderr)
        sys.exit(1)
    return venv_python()


def check_missing(py):
    missing = []
    for mod, pkg in NEEDED.items():
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        print(f"  {mod:<10} {'OK' if r.returncode == 0 else '缺失'}")
        if r.returncode != 0:
            missing.append(pkg)
    return missing


def main():
    check_only = "--check" in sys.argv
    py = sys.executable
    missing = check_missing(py)
    if not missing:
        print("[OK] 当前解释器已具备可选依赖（pysnmp）")
        return
    if check_only:
        print(f"[check] 缺失：{missing}")
        sys.exit(1)

    py = ensure_venv()
    print(f"[2/3] venv 解释器：{py}")
    missing = check_missing(py)
    if not missing:
        print("[OK] venv 依赖已齐全")
        return
    print(f"[3/3] 安装：{missing}（官方源，清华镜像回退）")
    base = [py, "-m", "pip", "install", "--disable-pip-version-check"]
    r = subprocess.run(base + missing)
    if r.returncode != 0:
        r = subprocess.run(base + ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] + missing)
    if r.returncode != 0:
        print("[error] 安装失败，检查网络", file=sys.stderr)
        sys.exit(1)
    check_missing(py)
    print(f"[DONE] 依赖就绪。交换机集成脚本用该解释器运行：\n  {py}")


if __name__ == "__main__":
    main()
