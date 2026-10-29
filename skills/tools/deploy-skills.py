#!/usr/bin/env python3
"""deploy-skills.py — 将运维技能部署包安装到 ongrid 管理节点并验证。

流程：
  1. 重新生成最新部署包（默认执行 prepare-skills.py；--skip-prepare 跳过）
  2. SFTP 上传 tar.gz 到目标主机 /tmp/
  3. sudo 解包到技能目录（默认 /var/lib/ongrid/skills）
  4. 校正属主（65532:65532，manager 容器用户）与权限（目录 755 / 文件 644）
  5. docker restart ongrid 触发技能重载
  6. 轮询容器日志验证加载结果（技能数量 / 告警）

依赖：paramiko。密码通过 --password 或环境变量 ONGRID_REMOTE_PASSWORD 提供。

用法（在仓库根目录执行）：
  python skills/tools/deploy-skills.py --host 10.7.30.47 --user ubuntu --password '***'
  python skills/tools/deploy-skills.py --host 10.7.30.47 --skip-prepare   # 复用现有包
  python skills/tools/deploy-skills.py --host 10.7.30.47 --no-restart     # 仅投放文件
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREPARE = Path(__file__).resolve().parent / "prepare-skills.py"
DEFAULT_TAR = REPO_ROOT / ".cache" / "ongrid-ops-skills.tar.gz"
REMOTE_TMP = "/tmp/ongrid-ops-skills.tar.gz"
DEFAULT_SKILLS_DIR = "/var/lib/ongrid/skills"
MANAGER_UID = "65532:65532"


def local_run(argv: list[str], timeout: int = 600) -> str:
    print("\n$ " + " ".join(argv))
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if out:
        print(out[:4000])
    if err:
        print("[stderr]", err[:1500])
    if r.returncode != 0:
        raise SystemExit(f"local command failed (exit {r.returncode})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="deploy ops skills to an ongrid management node")
    ap.add_argument("--host", required=True, help="target host, e.g. 10.7.30.47")
    ap.add_argument("--user", default="ubuntu", help="ssh user (default: ubuntu)")
    ap.add_argument("--password", default=None, help="ssh/sudo password (or env ONGRID_REMOTE_PASSWORD)")
    ap.add_argument("--skills-dir", default=DEFAULT_SKILLS_DIR,
                    help=f"remote skills dir (default: {DEFAULT_SKILLS_DIR})")
    ap.add_argument("--tar", default=str(DEFAULT_TAR), help="local tarball path")
    ap.add_argument("--skip-prepare", action="store_true", help="reuse existing tarball")
    ap.add_argument("--no-restart", action="store_true", help="skip docker restart + log verification")
    args = ap.parse_args()

    pw = args.password or os.environ.get("ONGRID_REMOTE_PASSWORD")
    if not pw:
        print("ERROR: password required (--password or ONGRID_REMOTE_PASSWORD)", file=sys.stderr)
        return 1

    tar = Path(args.tar)
    if not args.skip_prepare:
        local_run([sys.executable, str(PREPARE)])
    if not tar.is_file():
        print(f"ERROR: tarball not found: {tar}", file=sys.stderr)
        return 1
    print(f"\npackage: {tar} ({tar.stat().st_size / 1024 / 1024:.2f} MB)")

    try:
        import paramiko
    except ImportError:
        print("ERROR: paramiko is required (pip install paramiko)", file=sys.stderr)
        return 1

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(args.host, username=args.user, password=pw, timeout=20, banner_timeout=30)

    def srun(cmd: str, timeout: int = 600, check: bool = True) -> str:
        wrapped = f"printf '%s\\n' {shlex.quote(pw)} | sudo -S bash -c {shlex.quote(cmd)}"
        _, so, se = cli.exec_command(wrapped, timeout=timeout)
        out = so.read().decode(errors="replace")
        err = se.read().decode(errors="replace")
        err = "\n".join(l for l in err.splitlines() if "[sudo]" not in l)
        label = cmd if len(cmd) < 150 else cmd[:150] + " …"
        print(f"\n» {label}")
        if out.strip():
            print(out.strip()[:4000])
        if err.strip():
            print("[stderr]", err.strip()[:1500])
        if check and "command not found" in err:
            raise SystemExit(f"remote command failed: {cmd}")
        return out

    try:
        # 1) upload
        sftp = cli.open_sftp()
        print(f"\nuploading → {args.host}:{REMOTE_TMP}")
        sftp.put(str(tar), REMOTE_TMP)
        sftp.close()

        # 2) extract
        srun(f"mkdir -p {args.skills_dir} && tar -xzf {REMOTE_TMP} -C {args.skills_dir} "
             f"&& rm -f {REMOTE_TMP}")

        # 3) fix ownership + permissions
        srun(f"find {args.skills_dir} -type d -exec chmod 755 {{}} + "
             f"&& find {args.skills_dir} -type f -exec chmod 644 {{}} + "
             f"&& chown -R {MANAGER_UID} {args.skills_dir}")

        # 4) inventory on target
        srun(f"ls {args.skills_dir} | wc -l; ls {args.skills_dir} | head -8")

        if args.no_restart:
            print("\n--no-restart given; skipped restart & verification.")
            return 0

        # 5) restart manager
        srun("docker restart ongrid", timeout=300)

        # 6) poll logs for the load result
        print("\nwaiting for manager boot & skill load …")
        loaded_line = ""
        warn_lines = ""
        for attempt in range(15):
            time.sleep(6)
            out = srun('docker logs ongrid --since 5m 2>&1 | '
                       'grep -E "chatruntime: loaded|skill warning|subprocess skills loaded" | tail -30',
                       timeout=120, check=False)
            if "chatruntime: loaded" in out:
                loaded_line = out
                warn_lines = out
                break
        if loaded_line:
            print("\n=== skill load result ===")
            print(loaded_line.strip())
        else:
            print("\nWARN: load line not found in logs yet; check manually:")
            print("  sudo docker logs ongrid | grep -E 'chatruntime|skill' | tail -50")
    finally:
        cli.close()

    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
