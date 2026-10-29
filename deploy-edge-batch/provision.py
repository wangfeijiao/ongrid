#!/usr/bin/env python3
"""向主控批量签发 edge 身份（每台一个独立 edge_id + access/secret）。

主控当前只暴露 HTTP（http://10.6.54.118:8080），而 ongrid-edge 的 enroll 客户端
拒绝非 loopback 的 http 地址，因此不走 enroll，改由主控 API 直接签发。

输出 credentials.json：{ip: {name, edge_id, access_key, secret_key}}
"""
import json
import os
import urllib.request

MANAGER = os.environ.get("ONGRID_MANAGER", "http://127.0.0.1:8080/api/v1")
EMAIL = os.environ.get("ONGRID_ADMIN_EMAIL", "admin@ongrid.local")
PASSWORD = os.environ.get("ONGRID_ADMIN_PASSWORD", "ongrid@2026")
BASE = os.path.dirname(os.path.abspath(__file__))

# 禁用环境注入的代理，直连本机主控
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(MANAGER + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with opener.open(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    token = api("/auth/login", "POST", {"email": EMAIL, "password": PASSWORD})["access_token"]
    print("logged in as", EMAIL)

    existing = {}
    listing = api("/edges", token=token)
    # 列表响应是根级 {"items":[...]}，不是 data.items —— 解析错会导致重复建同名 edge
    items = listing.get("items") or (listing.get("data") or {}).get("items") or []
    for edge in items:
        existing[edge["name"]] = edge
    print(f"existing edges on manager: {len(existing)}")

    with open(os.path.join(BASE, "probe-result.json"), encoding="utf-8") as fh:
        hosts = [h for h in json.load(fh)["hosts"] if h.get("ok")]
    hosts.sort(key=lambda h: tuple(int(p) for p in h["ip"].split(".")))

    creds = {}
    for host in hosts:
        ip = host["ip"]
        name = f"srv-{ip.replace('.', '-')}"
        if name in existing:
            # secret_key 只在创建时返回一次，已存在的 edge 不能重装，只能 rotate
            print(f"SKIP   {ip}  already enrolled as {name} "
                  f"(edge_id={existing[name]['id']}, status={existing[name].get('status')})")
            creds[ip] = {"name": name, "edge_id": existing[name]["id"], "skip": True}
            continue
        resp = api("/edges", "POST", {"name": name}, token=token)
        data = resp.get("data") or resp
        creds[ip] = {
            "name": name,
            "edge_id": data.get("id"),
            "access_key": data.get("access_key_id") or data.get("access_key"),
            "secret_key": data.get("secret_key"),
            "skip": False,
        }
        if not creds[ip]["access_key"] or not creds[ip]["secret_key"]:
            print(f"FAIL   {ip}  unexpected response: {str(data)[:300]}")
            continue
        print(f"CREATE {ip}  edge_id={creds[ip]['edge_id']}  key={creds[ip]['access_key'][:8]}...")

    with open(os.path.join(BASE, "credentials.json"), "w", encoding="utf-8") as fh:
        json.dump(creds, fh, ensure_ascii=False, indent=2)

    created = sum(1 for c in creds.values() if not c.get("skip"))
    print(f"\ncreated {created}, skipped {len(creds) - created}")
    print("written: credentials.json")


if __name__ == "__main__":
    main()
