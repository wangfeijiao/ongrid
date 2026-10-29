#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网设备"无网络"原因自动诊断

诊断模型（从探针主机侧对目标设备做分层推理）：
  L0 设备是否在线  -> ping 目标 IP（不通再查 ARP 是否有该 MAC）
       ├─ ARP 也无   => L2 物理/链路问题（端口 down / VLAN 错配 / 网线 / 802.1x 隔离）
       └─ ARP 有但 ping 不通 => 主机防火墙/ACL 拦截 ICMP 或 IP 冲突
  L1 网关可达性    -> 探针侧网关可达（目标与本机同网段时，网关共享，可间接佐证）
  L2 DNS/出网      -> 见 net_status.py（整网层面）
  S  交换机端口    -> 若提供 --switch，查目标 MAC 所在端口的 admin/oper 状态、错包计数
                      端口 err-disable / admin down / 大量错包 => 端口级故障或安全策略

说明：本工具从"外部探针"视角诊断，无法替代登录目标设备排查。它能定位：
  设备是否离线、离线是否端口级、是否 IP 冲突/防火墙。真正的终端侧 DNS/DHCP
  错配需到终端上确认，脚本会给出"需到终端核查"的提示。

用法：
  python diagnose.py --target 172.16.8.56
  python diagnose.py --target 172.16.8.56 --mac aa:bb:cc:dd:ee:ff --switch 172.16.8.254 --ro public
  python diagnose.py --subnet 172.16.8.0/24 --switch 172.16.8.254   # 批量：标出不可达设备
"""
import argparse
import ipaddress
import json
import platform
import re
import subprocess
import sys


def run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        raw = (r.stdout or b"") + (r.stderr or b"")
        order = ("cp936", "gbk", "utf-8", "latin-1") if platform.system() == "Windows" else ("utf-8", "latin-1")
        for enc in order:
            try:
                return r.returncode, raw.decode(enc, errors="ignore")
            except Exception:
                continue
        return r.returncode, raw.decode("latin-1", errors="ignore")
    except Exception as e:
        return -1, str(e)


def ping(host, count=3, timeout_ms=1500):
    cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host] if platform.system() == "Windows" \
        else ["ping", "-c", str(count), "-W", str(timeout_ms // 1000 or 1), host]
    rc, out = run(cmd)
    return rc == 0, out.strip().splitlines()[-3:] if out.strip() else []


def arp_lookup(ip):
    rc, out = run(["arp", "-a"])
    m = re.search(rf"{re.escape(ip)}\s+([0-9a-fA-F-]{{17}})", out)
    if m:
        return m.group(1).lower().replace("-", ":")
    return None


def get_local_gw_subnet():
    """粗略拿本机首个非环回 IPv4 与其网关所在网段（用于判断目标是否同网段）。"""
    rc, out = run(["ipconfig"] if platform.system() == "Windows" else ["ip", "-4", "-o", "addr"])
    return out


def diagnose_one(target, mac=None, switch=None, verbose=False):
    rep = {"target": target, "mac": mac, "online": None, "arp_mac": None,
           "switch_port": None, "port_status": None, "cause": None, "evidence": [], "actions": []}
    ok, detail = ping(target)
    rep["online"] = ok
    if ok:
        rep["evidence"].append("目标 IP 可 ping 通，设备在线（ICMP 未被防火墙拦截）。")
        rep["cause"] = "设备在线；若用户仍报'无网络'，多为终端侧 DNS/DHCP/静态网关错配或浏览器/代理问题"
        rep["actions"].append("到终端执行 ipconfig /all 核对网关/DNS 与 DHCP 租约；确认无代理/Hosts 异常")
        rep["actions"].append("确认终端能否解析域名（nslookup 工作域名），区分 DNS 故障与出网故障")
    else:
        rep["evidence"].append("目标 IP 不可达：" + " / ".join(detail)[-120:])
        amac = arp_lookup(target) or mac
        rep["arp_mac"] = amac
        if not amac:
            rep["cause"] = "L2 不可达：ARP 表中无该 IP 的 MAC，本机与设备不在同一广播域或设备离线"
            rep["actions"].append("检查设备网线/电源/网卡指示灯；确认接入交换机端口 UP")
            rep["actions"].append("核对设备所属 VLAN 与本机一致；排查 802.1x/端口安全是否将其隔离")
            if switch is not None:
                rep["evidence"].append("（交换机侧：未学到该 MAC，可能端口 down 或 VLAN 隔离）")
        else:
            rep["cause"] = "设备在线（ARP 有 MAC）但 ICMP 不通：疑似主机防火墙拦截或 IP 地址冲突"
            rep["actions"].append("确认终端防火墙未禁 ICMP；排查同网段 IP 冲突（局域网内两台同 IP）")
            rep["actions"].append("若为打印机/摄像头等嵌入式设备，某些默认不回 ping，可改用端口探测确认服务")

    # 交换机端口级诊断
    if switch is not None and rep["arp_mac"]:
        try:
            loc = switch.locate(rep["arp_mac"])
            if loc:
                ifidx, name = loc
                rep["switch_port"] = name
                st = switch.port_status(ifidx)
                rep["port_status"] = st
                admin_up = st["admin"] in (1, None)
                oper_up = st["oper"] == 1
                if st["admin"] == 2:
                    rep["evidence"].append(f"交换机端口 {name} 被管理员 shutdown（admin=down）")
                    rep["cause"] = "交换机端口被手动关闭（admin down）导致设备断网"
                    rep["actions"].append(f"在交换机上 no shutdown 端口 {name} 恢复（switch_ops restore）")
                elif st["oper"] != 1 and st["admin"] == 1:
                    rep["evidence"].append(f"端口 {name} admin up 但 oper down（链路/协商/err-disable）")
                    rep["cause"] = "端口物理链路故障或触发 err-disable（如 BPDU Guard/环路保护）"
                    rep["actions"].append("检查对端网线/光模块；排查环路；err-disable 需 clear 或重启端口")
                elif (st["in_errors"] or 0) > 100 or (st["out_errors"] or 0) > 100:
                    rep["evidence"].append(f"端口 {name} 错包计数高（in={st['in_errors']}, out={st['out_errors']}）")
                    rep["cause"] = "端口存在大量错包，可能线缆质量差/双工不匹配"
                    rep["actions"].append("更换网线/水晶头；强制双工与速率匹配")
                else:
                    rep["evidence"].append(f"交换机端口 {name} 状态正常（admin/oper up，错包少）")
        except Exception as e:
            rep["evidence"].append(f"交换机查询失败：{e}")
    return rep


def main():
    ap = argparse.ArgumentParser(description="内网设备无网络原因诊断")
    ap.add_argument("--target", help="待诊断设备 IP（单设备模式）")
    ap.add_argument("--mac", help="目标 MAC（可选，加快交换机定位）")
    ap.add_argument("--subnet", help="批量扫描网段（标出不可达设备）")
    ap.add_argument("--switch", default=None, help="管理交换机 IP（端口级诊断）")
    ap.add_argument("--ro", default="public", help="SNMP 读社区")
    ap.add_argument("--ver", type=int, default=2)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    switch = None
    if args.switch:
        try:
            from switch_ops import Switch
            switch = Switch(args.switch, args.ro, args.ro, args.ver)
            print(f"已连接交换机 {args.switch}")
        except Exception as e:
            print(f"[warn] 交换机连接失败：{e}")

    reports = []
    if args.target:
        r = diagnose_one(args.target, args.mac, switch)
        reports.append(r)
        _print_report(r)
    elif args.subnet:
        net = ipaddress.ip_network(args.subnet, strict=False)
        print(f"批量诊断 {args.subnet}（仅标记不可达设备）...")
        import concurrent.futures as cf
        def check(ip):
            ok, _ = ping(str(ip), 1, 800)
            return str(ip), ok
        with cf.ThreadPoolExecutor(max_workers=80) as ex:
            res = list(ex.map(check, list(net.hosts())))
        down = [ip for ip, ok in res if not ok]
        print(f"共 {len(res)} 台探测，{len(down)} 台不可达：")
        for ip in down:
            r = diagnose_one(ip, None, switch)
            reports.append(r)
            print(f"  ✗ {ip}  {r['cause']}")
    else:
        ap.error("需 --target 或 --subnet")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        print(f"\n[JSON] -> {args.json}")


def _print_report(r):
    print(f"\n===== 设备 {r['target']} 诊断 =====")
    print(f"  在线状态 : {'在线' if r['online'] else '不可达'}")
    if r["arp_mac"]:
        print(f"  ARP MAC : {r['arp_mac']}")
    if r["switch_port"]:
        print(f"  交换机端口: {r['switch_port']}  {r['port_status']}")
    print("  证据:")
    for e in r["evidence"]:
        print(f"    - {e}")
    print(f"  推断根因: {r['cause']}")
    print("  处置建议:")
    for a in r["actions"]:
        print(f"    > {a}")


if __name__ == "__main__":
    main()
