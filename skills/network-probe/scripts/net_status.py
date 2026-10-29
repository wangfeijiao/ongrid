#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络状态自动识别（自动 ping，分层探测）

分层模型（用于快速定位断网层级）：
  L0 本地协议栈  -> 127.0.0.1
  L1 默认网关    -> 本机网关（自动读取），ARP/路由可达
  L2 DNS 解析    -> DNS 服务器可达 + 域名解析
  L3 外网连通    -> 外部目标可达（ICMP/HTTP）

输出 JSON（--json）+ 人类可读摘要；任一上层依赖下层，逐层判定。

用法：
  python net_status.py                      # 自动探测本机网关/DNS 并分层 ping
  python net_status.py --gw 172.16.8.1 --dns 223.5.5.5 --target 8.8.8.8
  python net_status.py --json --out status.json
"""
import argparse
import json
import platform
import re
import subprocess
import sys


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=8)
        raw = (r.stdout or b"") + (r.stderr or b"")
        # Windows ipconfig/nslookup 为 CP936(GBK)；Git Bash 下 utf-8 会"成功解码成乱码"，
        # 故 Windows 优先 cp936/gbk，再回退 utf-8/latin-1
        order = ("cp936", "gbk", "utf-8", "latin-1") if platform.system() == "Windows" \
            else ("utf-8", "gbk", "latin-1")
        for enc in order:
            try:
                return r.returncode, raw.decode(enc, errors="ignore")
            except (UnicodeDecodeError, LookupError):
                continue
        return r.returncode, raw.decode("latin-1", errors="ignore")
    except Exception as e:
        return -1, str(e)


def ping(host, count=2, timeout_ms=1500):
    """返回 (ok, latency_ms_or_None, detail)"""
    if platform.system() == "Windows":
        rc, out = run(["ping", "-n", str(count), "-w", str(timeout_ms), host])
    else:
        rc, out = run(["ping", "-c", str(count), "-W", str(timeout_ms // 1000 or 1), host])
    if rc != 0:
        return False, None, out.strip().splitlines()[-1] if out.strip() else "无响应"
    m = re.search(r"时间[=<]?(\d+(?:\.\d+)?)\s*ms", out) or re.search(r"time[=<]?(\d+(?:\.\d+)?)\s*ms", out, re.I)
    lat = float(m.group(1)) if m else None
    return True, lat, ""


def split_adapters(out):
    """把 ipconfig 输出按适配器分块（适配器头行不缩进、含"适配器"/"Adapter"）。
    兼容中文/英文 ipconfig 与解码产生的前导组合字符。"""
    blocks, cur = [], []
    for line in out.splitlines():
        is_header = (not line.startswith((" ", "\t"))) and (
            "适配器" in line or "Adapter" in line)
        if is_header and cur:
            blocks.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def get_gateway_dns():
    gw, dns = None, []
    if platform.system() == "Windows":
        rc, out = run(["ipconfig"])
        if rc == 0:
            for blk in split_adapters(out):
                # 跳过无默认网关的适配器（媒体断开、VMware 虚拟网卡、仅 IPv6）
                if "默认网关" not in blk and "Default Gateway" not in blk:
                    continue
                gi = blk.find("默认网关")
                if gi < 0:
                    gi = blk.find("Default Gateway")
                tail = blk[gi:]
                gws = re.findall(r"(\d+\.\d+\.\d+\.\d+)", tail)
                if not gws:
                    continue  # 网关行为空（该适配器未配置网关）
                gw = gws[0]
                m_ip = re.search(r"IPv4 地址[^\n]*?(\d+\.\d+\.\d+\.\d+)", blk)
                if m_ip and m_ip.group(1).startswith("169.254"):
                    continue  # APIPA，非有效地址
                # DNS：本适配器内 DNS 服务器后的所有 IPv4
                for mm in re.finditer(r"DNS 服务器[^\n]*?(\d+\.\d+\.\d+\.\d+)", blk):
                    if mm.group(1) not in dns:
                        dns.append(mm.group(1))
                break  # 已找到有效网关适配器
    else:
        rc, out = run(["ip", "-4", "route", "show", "default"])
        if rc == 0:
            m = re.search(r"via (\d+\.\d+\.\d+\.\d+)", out)
            if m:
                gw = m.group(1)
        rc, out = run(["resolvectl", "dns"])
        if rc != 0:
            rc, out = run(["cat", "/etc/resolv.conf"])
        if rc == 0:
            for m in re.finditer(r"nameserver (\d+\.\d+\.\d+\.\d+)", out):
                dns.append(m.group(1))
    # 去重
    seen, dns2 = set(), []
    for d in dns:
        if d not in seen:
            seen.add(d); dns2.append(d)
    return gw, dns2


def resolve_test(domain="workbuddy.cn"):
    rc, out = run(["nslookup", domain, "8.8.8.8"] if platform.system() == "Windows"
                  else ["getent", "hosts", domain])
    ok = rc == 0 and ("Address" in out or "address" in out.lower())
    return ok, out.strip().splitlines()[-1] if out.strip() else ""


def main():
    ap = argparse.ArgumentParser(description="网络状态自动识别（自动 ping）")
    ap.add_argument("--gw", default=None, help="默认网关（不填自动读取）")
    ap.add_argument("--dns", nargs="*", default=None, help="DNS 服务器列表（不填自动读取）")
    ap.add_argument("--target", nargs="*", default=None, help="外网探测目标（默认 8.8.8.8 223.5.5.5 1.1.1.1）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="输出 JSON 文件")
    args = ap.parse_args()

    auto_gw, auto_dns = get_gateway_dns()
    gw = args.gw or auto_gw
    dns = args.dns or auto_dns or ["223.5.5.5", "8.8.8.8"]
    targets = args.target or ["8.8.8.8", "223.5.5.5", "1.1.1.1"]

    result = {"layers": {}, "summary": "", "gateway": gw, "dns": dns, "external_targets": targets}

    def layer(name, ok, detail, loss=False):
        result["layers"][name] = {"ok": ok, "detail": detail}

    # L0
    ok0, *_ = ping("127.0.0.1", 1)
    layer("L0_local", ok0, "本地协议栈" if ok0 else "本地协议栈异常")

    # L1 gateway
    if not gw:
        layer("L1_gateway", False, "未获取到默认网关（检查网卡/IP 配置）", True)
        result["summary"] = "无法获取默认网关，疑似本地 IP/网卡配置问题或网卡未连接"
        print_summary(result)
        maybe_out(args, result); sys.exit(0)
    gok, glat, gdet = ping(gw)
    layer("L1_gateway", gok, (f"网关 {gw} 可达，延迟 {glat}ms" if gok else f"网关 {gw} 不可达：{gdet}"))

    # L2 DNS
    dns_oks = []
    for d in dns[:2]:
        dok, dlat, _ = ping(d)
        dns_oks.append(dok)
    rok, rdet = resolve_test()
    l2_ok = any(dns_oks) and rok
    layer("L2_dns", l2_ok, (f"DNS {','.join(dns[:2])} 可达且域名解析正常" if l2_ok
                            else f"DNS 解析异常（可达={dns_oks}，解析={rok}）：{rdet}"))

    # L3 external
    ext_oks, ext_det = [], ""
    for t in targets:
        tok, tlat, tdet = ping(t)
        ext_oks.append(tok)
        if not tok:
            ext_det = tdet
    l3_ok = any(ext_oks)
    layer("L3_external", l3_ok, (f"外网目标 {sum(ext_oks)}/{len(targets)} 可达"
                                 if l3_ok else f"外网全部不可达：{ext_det}"))

    # 结论判定
    if not gok:
        result["summary"] = "断网层级：网关不可达（L1）。本地到网关链路故障：检查物理连线/交换机端口/本机 IP 与 VLAN 配置。"
    elif not l2_ok:
        result["summary"] = "断网层级：DNS（L2）。网关可达但 DNS 异常：检查 DNS 服务器、本机 DNS 设置或防火墙对 53 端口的拦截。"
    elif not l3_ok:
        result["summary"] = "断网层级：外网出口（L3）。网关/DNS 正常但出网失败：检查 NAT/防火墙策略、上联带宽或运营商链路。"
    else:
        result["summary"] = "网络状态正常：本地→网关→DNS→外网 各层均连通。"

    print_summary(result)
    maybe_out(args, result)


def print_summary(result):
    print("=== 网络状态分层探测 ===")
    names = {"L0_local": "本地协议栈", "L1_gateway": "默认网关", "L2_dns": "DNS 解析", "L3_external": "外网连通"}
    for k, label in names.items():
        lv = result["layers"].get(k)
        if not lv:
            continue
        mark = "✓" if lv["ok"] else "✗"
        print(f"  [{mark}] {label:<10} {lv['detail']}")
    print(f"\n结论：{result['summary']}")


def maybe_out(args, result):
    if args.json or args.out:
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"\n[JSON] -> {args.out}")
        else:
            print("\n" + text)


if __name__ == "__main__":
    main()
