#!/usr/bin/env python3
"""探测目标机 SSH 连通性、可用密码、架构、sudo 能力。只读，不做任何修改。"""
import concurrent.futures as cf
import ipaddress
import json
import sys

import paramiko

HOSTS_FILE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "hosts.txt"
USERS = ["ubuntu"]
PASSWORDS = ["Ahjkii@2026", "Ahjkii@2025"]  # 优先试新密码

PROBE_CMD = (
    "echo __BEGIN__;"
    "uname -s; uname -m;"
    "cat /etc/os-release 2>/dev/null | grep -E '^(ID|VERSION_ID)=';"
    "sudo -n true 2>/dev/null && echo SUDO_NOPASSWD_OK || echo SUDO_NEEDS_PASSWORD;"
    "command -v systemctl >/dev/null && echo HAS_SYSTEMD || echo NO_SYSTEMD;"
    "ls -1 /usr/local/bin/ongrid-edge 2>/dev/null || echo EDGE_NOT_INSTALLED;"
    "systemctl is-active ongrid-edge 2>/dev/null || echo EDGE_SVC_UNKNOWN;"
    "echo __END__"
)


def probe(ip):
    result = {"ip": ip, "ok": False, "password": None, "user": None, "raw": "", "error": ""}
    for user in USERS:
        for pwd in PASSWORDS:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    ip, username=user, password=pwd,
                    port=22, timeout=12, banner_timeout=12,
                    auth_timeout=12, allow_agent=False, look_for_keys=False,
                )
                _, stdout, stderr = client.exec_command(PROBE_CMD, timeout=30)
                out = stdout.read().decode("utf-8", "replace")
                err = stderr.read().decode("utf-8", "replace")
                # root 登录本身就是特权，不再探测 sudo；其余用户验证能否用同一口令提权
                if user == "root":
                    sudo_cmd = "echo SUDO_MODE=root"
                else:
                    sudo_cmd = (
                        "if sudo -n true 2>/dev/null; then echo SUDO_MODE=nopasswd; "
                        f"elif printf '%s\\n' '{pwd}' | sudo -S -p '' true 2>/dev/null; "
                        "then echo SUDO_MODE=password; else echo SUDO_MODE=denied; fi"
                    )
                _, so, _ = client.exec_command(sudo_cmd, timeout=30)
                out += "\n" + so.read().decode("utf-8", "replace")
                result.update(ok=True, password=pwd, user=user, raw=out, error=err.strip())
                client.close()
                return result
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"{type(exc).__name__}: {exc}"
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
    return result


def parse(result):
    if not result["ok"]:
        return result
    raw = result["raw"]
    body = raw.split("__BEGIN__")[-1]  # sudo 结果追加在 __END__ 之后，不能按 __END__ 截断
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    info = {
        "ip": result["ip"],
        "ok": True,
        "password": result["password"],
        "user": result.get("user") or "ubuntu",
        "kernel": lines[0] if len(lines) > 0 else "?",
        "arch": lines[1] if len(lines) > 1 else "?",
        "os": ";".join(ln for ln in lines if ln.startswith(("ID=", "VERSION_ID="))),
        "sudo": ("nopasswd" if any("SUDO_MODE=nopasswd" in ln for ln in lines)
                 else "password" if any("SUDO_MODE=password" in ln for ln in lines)
                 else "denied" if any("SUDO_MODE=denied" in ln for ln in lines) else "?"),
        "systemd": "yes" if any("HAS_SYSTEMD" in ln for ln in lines) else "no",
        "edge_installed": not any("EDGE_NOT_INSTALLED" in ln for ln in lines),
        "edge_active": any(ln.strip() == "active" for ln in lines),
        "raw_error": result["error"],
    }
    info["arch_norm"] = {"x86_64": "amd64", "aarch64": "arm64"}.get(info["arch"], info["arch"])
    return info


def main():
    global USERS, PASSWORDS
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        globals()["HOSTS_FILE"] = args[0]
    for flag in sys.argv[1:]:
        if flag.startswith("--users="):
            USERS = [x.strip() for x in flag.split("=", 1)[1].split(",") if x.strip()]
        elif flag.startswith("--passwords="):
            PASSWORDS = [x.strip() for x in flag.split("=", 1)[1].split(",") if x.strip()]
    print(f"users={USERS} passwords={len(PASSWORDS)} 个")

    with open(HOSTS_FILE, encoding="utf-8") as fh:
        hosts = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    for host in hosts:
        ipaddress.ip_address(host)  # 校验
    print(f"probing {len(hosts)} hosts ...\n", flush=True)

    results = []
    with cf.ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(probe, h): h for h in hosts}
        for fut in cf.as_completed(futures):
            res = parse(fut.result())
            results.append(res)
            if res.get("ok"):
                print(f"OK   {res['ip']:>15}  user={res.get('user', '?'):<7} arch={res['arch_norm']:<6} "
                      f"sudo={res['sudo']:<14} systemd={res['systemd']:<3} "
                      f"edge_installed={res['edge_installed']} pwd={res['password']}", flush=True)
            else:
                print(f"FAIL {res['ip']:>15}  {res.get('error', '')[:90]}", flush=True)

    ok = [r for r in results if r.get("ok")]
    summary = {
        "total": len(results),
        "reachable": len(ok),
        "failed": len(results) - len(ok),
        "arch": {},
        "sudo": {},
        "passwords": {},
    }
    for r in ok:
        summary["arch"][r["arch_norm"]] = summary["arch"].get(r["arch_norm"], 0) + 1
        summary["sudo"][r["sudo"]] = summary["sudo"].get(r["sudo"], 0) + 1
        summary["passwords"][r["password"]] = summary["passwords"].get(r["password"], 0) + 1
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    with open("probe-result.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "hosts": results}, fh, ensure_ascii=False, indent=2)
    print("\nwritten: probe-result.json")


if __name__ == "__main__":
    main()
