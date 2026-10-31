#!/usr/bin/env python3
"""ongrid-edge 批量部署编排器。

从 probe-result.json 读取可达主机及其可用密码 / sudo 方式，
并发完成：上传脚本 → 种子机准备二进制源 → 逐台 enroll + install → 验证。

用法:
  python deploy.py --manager-url https://10.7.30.5:443 --token <ENROLLMENT_TOKEN> \
      [--cloud-addr-fallback 10.7.30.5:40012] [--seed 10.7.30.13] [--limit 3] \
      [--phase all|seed|install|verify] [--hosts hosts.txt]
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shlex
import sys
import time

import paramiko

BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE_DIR = os.path.join(BASE, "bundle")
SCRIPTS = ["install-edge.sh", "apply-pending-upgrade.sh", "ongrid-edge.service",
           "ongrid-edge-upgrade.service", "ongrid-edge.env.example"]
REMOTE_UPLOAD = "/tmp/ongrid-edge-upload"
REMOTE_BUNDLE = "/opt/ongrid-edge-bundle"

UPLOAD_EXTRA = ["remote-install.sh"]
SEED_SCRIPT = "seed-setup.sh"

VERIFY_CMD = (
    "s=$(systemctl is-active ongrid-edge 2>/dev/null); echo SERVICE=$s;"
    "echo BINARY=$(test -x /usr/local/bin/ongrid-edge && echo yes || echo no);"
    "echo PLUGINS=$(ls /usr/local/lib/ongrid-edge 2>/dev/null | wc -l);"
    "echo CLOUD=$(sed -n 's/^ONGRID_EDGE_CLOUD_ADDR=//p' /etc/ongrid-edge/ongrid-edge.env 2>/dev/null);"
    "echo ACCESS_KEY_PREFIX=$(sed -n 's/^ONGRID_EDGE_ACCESS_KEY=\\(....\\).*/\\1/p' /etc/ongrid-edge/ongrid-edge.env 2>/dev/null);"
    "echo TAIL=$(journalctl -u ongrid-edge -n 3 --no-pager 2>/dev/null | tail -3 | tr '\\n' '|')"
)


def sudo_wrap(cmd, sudo_mode, password, user="ubuntu"):
    """按主机 sudo 能力包装命令。root 登录本身已是特权，不再套 sudo。"""
    inner = shlex.quote(cmd)
    if user == "root":
        return f"bash -c {inner}"
    if sudo_mode == "nopasswd":
        return f"sudo -n bash -c {inner}"
    return f"printf '%s\\n' {shlex.quote(password)} | sudo -S -p '' bash -c {inner}"


def connect(host, password, user="ubuntu"):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, port=22,
                   timeout=15, banner_timeout=15, auth_timeout=15,
                   allow_agent=False, look_for_keys=False)
    return client


def run(client, cmd, timeout=1800):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def upload(client, files, remote_dir):
    """上传脚本并统一转成 LF —— 仓库在 Windows 上 checkout 是 CRLF，
    bash 会把 \\r 当成命令/参数的一部分（`$'\\r': command not found`）。"""
    sftp = client.open_sftp()
    try:
        sftp.mkdir(remote_dir)
    except OSError:
        pass
    for path in files:
        with open(path, "rb") as fh:
            data = fh.read().replace(b"\r\n", b"\n")
        remote = f"{remote_dir}/{os.path.basename(path)}"
        with sftp.open(remote, "wb") as out:
            out.write(data)
        sftp.chmod(remote, 0o755)
    sftp.close()


def stage_scripts(client, sudo_mode, password, user="ubuntu", extra=()):
    files = [os.path.join(BUNDLE_DIR, name) for name in SCRIPTS]
    files += [os.path.join(BASE, name) for name in UPLOAD_EXTRA + list(extra)]
    upload(client, files, REMOTE_UPLOAD)
    copy_cmd = (f"mkdir -p {REMOTE_BUNDLE} && cp {REMOTE_UPLOAD}/* {REMOTE_BUNDLE}/ "
                f"&& chmod +x {REMOTE_BUNDLE}/*.sh")
    rc, out, err = run(client, sudo_wrap(copy_cmd, sudo_mode, password, user), timeout=120)
    if rc != 0:
        raise RuntimeError(f"stage scripts failed rc={rc}: {err[:200]}")
    return out


def do_seed(host, password, sudo_mode, user="ubuntu"):
    client = connect(host, password, user)
    try:
        stage_scripts(client, sudo_mode, password, user, extra=[SEED_SCRIPT])
        rc, out, err = run(client, sudo_wrap(
            f"bash {REMOTE_BUNDLE}/{SEED_SCRIPT}", sudo_mode, password, user), timeout=1800)
        return {"ip": host, "ok": rc == 0, "stdout": out, "stderr": err[-800:]}
    finally:
        client.close()


def do_install(host, password, sudo_mode, cloud_addr, access_key, secret_key, seed_ip, edge_id, user="ubuntu"):
    started = time.time()
    client = connect(host, password, user)
    try:
        stage_scripts(client, sudo_mode, password, user)
        cmd = (f"bash {REMOTE_BUNDLE}/remote-install.sh "
               f"{shlex.quote(cloud_addr)} {shlex.quote(access_key)} {shlex.quote(secret_key)} "
               f"{shlex.quote(seed_ip or '-')}")
        rc, out, err = run(client, sudo_wrap(cmd, sudo_mode, password, user), timeout=2400)
        return {"ip": host, "ok": rc == 0, "edge_id": edge_id,
                "seconds": round(time.time() - started, 1),
                "stdout": out[-2500:], "stderr": err[-1200:]}
    except Exception as exc:  # noqa: BLE001
        return {"ip": host, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - started, 1)}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def do_verify(host, password, sudo_mode, user="ubuntu"):
    client = connect(host, password, user)
    try:
        rc, out, err = run(client, sudo_wrap(VERIFY_CMD, sudo_mode, password, user), timeout=180)
        info = dict(re.findall(r"(\w+)=(.*)", out))
        return {"ip": host, "ok": info.get("SERVICE") == "active", "info": info, "stderr": err[-400:]}
    except Exception as exc:  # noqa: BLE001
        return {"ip": host, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def load_reachable():
    with open(os.path.join(BASE, "probe-result.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    hosts = [h for h in data["hosts"] if h.get("ok")]
    hosts.sort(key=lambda h: tuple(int(p) for p in h["ip"].split(".")))
    return hosts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud-addr", default="10.6.54.118:40012",
                        help="主控隧道地址 host:port，edge 用它回连")
    parser.add_argument("--seed", default="", help="种子机 IP，为空则自动选第一台可达主机")
    parser.add_argument("--limit", type=int, default=0, help="只部署前 N 台（灰度用）")
    parser.add_argument("--only", default="", help="只处理指定 IP，逗号分隔（补做失败机器用）")
    parser.add_argument("--phase", default="all", choices=["all", "seed", "install", "verify"])
    parser.add_argument("--parallel", type=int, default=8)
    args = parser.parse_args()

    hosts = load_reachable()
    print(f"reachable hosts: {len(hosts)}")

    with open(os.path.join(BASE, "credentials.json"), encoding="utf-8") as fh:
        creds = json.load(fh)
    skipped = [ip for ip, c in creds.items() if c.get("skip")]
    if skipped:
        print(f"skip (no fresh credentials): {skipped}")

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        hosts = [h for h in hosts if h["ip"] in wanted]
        print(f"only: {len(hosts)} hosts")
    elif args.limit:
        hosts = hosts[:args.limit]
        print(f"limited to {len(hosts)} hosts")

    seed_ip = args.seed or (hosts[0]["ip"] if hosts else "")
    print(f"seed host: {seed_ip}\n")

    result = {"seed": None, "install": [], "verify": []}

    if args.phase in ("all", "seed"):
        seed_host = next((h for h in hosts if h["ip"] == seed_ip), None)
        if not seed_host:
            seed_host = {"ip": seed_ip, "password": None, "sudo": "password"}
        if seed_host.get("password") is None:
            print("seed host not in probe result; skipping seed phase")
        else:
            print(f"=== seed phase: {seed_ip} ===", flush=True)
            res = do_seed(seed_ip, seed_host["password"], seed_host.get("sudo", "password"), seed_host.get("user", "ubuntu"))
            result["seed"] = res
            print(res["stdout"][-1500:])
            if not res["ok"]:
                print(f"SEED FAILED: {res['stderr'][:500]}")
                print("后续主机会自动回退到官方 CNB 下载（仍能装，只是慢）")

    if args.phase in ("all", "install"):
        targets = [h for h in hosts if not creds.get(h["ip"], {}).get("skip")]
        print(f"\n=== install phase: {len(targets)} hosts, parallel={args.parallel} ===", flush=True)
        print(f"cloud_addr: {args.cloud_addr}", flush=True)
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(do_install, h["ip"], h["password"], h.get("sudo", "password"),
                            args.cloud_addr, creds[h["ip"]]["access_key"],
                            creds[h["ip"]]["secret_key"], seed_ip,
                            creds[h["ip"]].get("edge_id"), h.get("user", "ubuntu")): h["ip"]
                for h in targets
            }
            for fut in cf.as_completed(futures):
                res = fut.result()
                result["install"].append(res)
                if res["ok"]:
                    print(f"OK   {res['ip']:>15}  edge_id={res.get('edge_id')}  {res.get('seconds')}s", flush=True)
                else:
                    print(f"FAIL {res['ip']:>15}  {res.get('error') or res.get('stderr', '')[:200]}", flush=True)

    if args.phase in ("all", "verify"):
        print(f"\n=== verify phase ===", flush=True)
        with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(do_verify, h["ip"], h["password"], h.get("sudo", "password"), h.get("user", "ubuntu")): h["ip"]
                       for h in hosts}
            for fut in cf.as_completed(futures):
                res = fut.result()
                result["verify"].append(res)
                info = res.get("info", {})
                print(f"{'OK  ' if res['ok'] else 'FAIL'} {res['ip']:>15}  "
                      f"svc={info.get('SERVICE', '?'):<10} bin={info.get('BINARY', '?')} "
                      f"plugins={info.get('PLUGINS', '?')} cloud={info.get('CLOUD', '?')}", flush=True)

    ok = sum(1 for r in result["install"] if r["ok"])
    print(f"\n=== SUMMARY === install ok {ok}/{len(result['install'])}")
    with open(os.path.join(BASE, "deploy-result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print("written: deploy-result.json")
    return 0 if ok == len(result["install"]) else 1


if __name__ == "__main__":
    sys.exit(main())
