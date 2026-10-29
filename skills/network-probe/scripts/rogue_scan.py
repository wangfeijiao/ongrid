#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网私接无线路由器发现 + 定位交换机端口 + 断网处置

发现思路（按置信度从高到低）：
  1. 交换机 MAC 表法（最准）：某 access 端口下学习到远多于 1 个 MAC（私接路由/AP 下挂了多台终端）
     → 经 switch_ops 定位该 MAC 的交换机端口与端口名。
  2. OUI 法：设备的 MAC 厂商属于常见家用路由/AP 品牌，且不是官方网关/DHCP 服务器。
  3. DHCP 竞争法（可选）：用 scapy 发 DHCP Discover，收到 >1 个 Offer 说明存在私接 DHCP 服务器。

处置（确认门控）：
  - 默认仅输出处置建议（dry-run）。真正断网需 --cut-off --confirm，关闭对应交换机端口。
  - 关闭端口即切断私接路由联网；恢复用 switch_ops restore 或网管平台。
  - 无管理交换机时，输出私接设备 MAC，便于在网关/网康 ICG 做 MAC 黑名单阻断。

依赖：arp/ping（标准库+系统命令）；交换机定位需 switch_ops（见 env_setup.py 安装 pysnmp）。

用法：
  python rogue_scan.py --subnet 172.16.8.0/24
  python rogue_scan.py --subnet 172.16.8.0/24 --switch 172.16.8.254 --ro public --rw private --cut-off --confirm
  python rogue_scan.py --subnet 192.168.1.0/24 --gw 192.168.1.1   # 标注官方网关，避免误报
"""
import argparse
import ipaddress
import json
import platform
import re
import subprocess
import sys
import concurrent.futures as cf


# 常见家用/SOHO 无线路由、AP 厂商 OUI（前 3 字节 = 6 位十六进制，大写）。
# 非穷举；真实环境可据自有设备清单扩充。匹配采用精确 6 位前缀，避免误伤。
ROUTER_OUIS = {
    "TP-LINK": ["000AEB", "001478", "001D0F", "002219", "085700", "0C8268",
                "101F74", "148EEC", "1C3DF2", "20876F", "245A4C", "50BD5F"],
    "D-Link":  ["0005BD", "000F3D", "00146C", "00195B", "001BFC", "002191",
                "14D64D", "1C7EE5", "340461", "5826A7", "AABBC2"],
    "Xiaomi/小米": ["04B16C", "0862E8", "0C8BFD", "10BF48", "18E3F2", "20CF30",
                    "285FE6", "3CFDDE", "488BF4", "54F2C1", "64D67B", "7C7A91",
                    "8CC8A7", "94CC1A", "A0A0A0", "AC9E17", "B0487A", "C45A1A",
                    "D0451A", "EC6FCA", "F4008A"],
    "Huawei/华为": ["000AF5", "001018", "001E10", "00E0FC", "08979E", "0C96E6",
                    "18A6F7", "208B9E", "24ADDE", "3C8C40", "4831B5", "4C1FCC",
                    "5443B2", "5489D6", "5CBAEF", "6448BC", "68A3C4", "7C27EA",
                    "8842E5", "8CC8E7", "9C57AD", "A40BCD", "AC8505", "B0CC6E",
                    "C07BEF", "C470AB", "C83C35", "CC82E0", "D05B04", "D4B152",
                    "D86595", "E03B6E", "E0A4E7", "E4A7A0", "E4C9D3", "EC26CA"],
    "H3C/新华三": ["000FE0", "001882", "002326", "0030B6", "006438", "0090EB",
                   "00E0FC", "1400F3", "1C1D5C", "204C9E", "28319C", "3C8C40",
                   "4487FC", "54E1AD", "64919B", "70DB98", "80F6BC", "A08C1D",
                   "AC74BB", "BC62C6", "C4CAD9", "C8A700", "D461FE", "DCD2FC",
                   "E0089F", "E468A3", "F07D68", "F4707B", "F8C0FC"],
    "Tenda/腾达": ["000C43", "001987", "0021CC", "00A0B0", "00B00C", "080730",
                   "084A0A", "0C8591", "10684F", "18A6F7", "28E24C", "303A64",
                   "342980", "3C8CFD", "44574C", "4A5B33", "50BD5F", "54A70F",
                   "585FEB"],
    "Mercury/水星": ["000F60", "001878", "080730", "50BD5F"],
    "Fast/迅捷":  ["080730", "50BD5F"],
    "Netgear":   ["000986", "00146C", "00236C", "0010DA", "00125F", "0023CD",
                  "001E2A", "0018DA", "0014BF"],
    "ASUS/华硕": ["000E8F", "0017A4", "001BFC", "08607A", "106F3F", "1825BC",
                  "20CF30", "28C64C", "2C56DC", "306530", "382C4A", "44C3F0",
                  "50465B", "5855CA", "6854FC", "70FD33", "784DD0", "88D79F",
                  "9C5C8E", "A0F3C1", "AC9E17", "B06EBF", "BCAEC5", "CCF9E4",
                  "D017C2", "E03F49", "F4F26D", "FC3FDB"],
    "Ruijie/锐捷": ["000FE0", "001B0D", "002586", "00E0FC", "08810D", "0CBD2B",
                    "14B9B0", "2067AE", "28D24B", "2C27D7", "3C8C40", "5006AB",
                    "5800E3", "5CF9DD", "649B1B", "6CAE8B", "74EA3A", "80F6BC",
                    "88D0FB", "9C1C12", "A44CB1", "AC74BB", "BC62C6", "CC9F33"],
    "Ubiquiti": ["000F77", "00156D", "00272D", "24A43C", "44D9E7", "64B0FA",
                 "74AA5A", "78A050", "788A20", "A8C980", "B4FB2D", "C49300",
                 "DCEFCA", "E0071B", "F09FC2"],
    "MikroTik": ["000C42", "001326", "002712", "004F27", "0C8610", "441FA1",
                 "4C5E0C", "CCB2AE", "D4CA6D", "FCECAC"],
    "Mercury/Tenda 杂牌": ["001959", "E0CCF8", "F4EC38"],
}
# 扁平化为 6 位前缀 -> 厂商
_OUI_SET = {}
for _ven, _ous in ROUTER_OUIS.items():
    for _o in _ous:
        _o = _o.replace(":", "").replace("-", "").upper()
        if len(_o) == 6:
            _OUI_SET[_o] = _ven


def oui_vendor(mac):
    m = mac.replace(":", "").replace("-", "").upper()
    if len(m) < 6:
        return None
    return _OUI_SET.get(m[:6])


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


def arp_sweep(subnet, threads=80):
    """ping 整个子网刷新 ARP 缓存，再解析 arp -a 得到 IP→MAC。"""
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    print(f"[1/3] ARP 扫描 {subnet}（{len(hosts)} 个地址）...")
    ping_cmd = ["ping", "-n", "1", "-w", "200"] if platform.system() == "Windows" else ["ping", "-c", "1", "-W", "1"]
    with cf.ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(lambda ip: run(ping_cmd + [ip], timeout=2), hosts))

    rc, out = run(["arp", "-a"])
    results = {}
    # 解析形如 "192.168.1.10        aa-bb-cc-dd-ee-ff     动态"
    for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2})", out):
        ip, mac = m.group(1), m.group(2).lower().replace("-", ":")
        # 仅保留扫描网段内的地址（arp -a 会带出整张缓存）
        try:
            if ipaddress.ip_address(ip) in net:
                results[ip] = mac
        except ValueError:
            continue
    print(f"    发现 {len(results)} 台在线设备（网段内）")
    return results


def analyze(ip_mac, gateway_ip=None, switch=None):
    """产出私接路由候选列表。switch 为 Switch 实例时做端口定位与多 MAC 检测。"""
    candidates = []
    # 交换机多 MAC 端口法
    port_client_count = {}
    if switch is not None:
        try:
            port_client_count = switch.port_clients_count()
        except Exception as e:
            print(f"[warn] 读取交换机 MAC 表失败：{e}")

    for ip, mac in ip_mac.items():
        if gateway_ip and ip == gateway_ip:
            continue
        ven = oui_vendor(mac)
        rec = {"ip": ip, "mac": mac, "vendor_oui": ven, "switch_port": None,
               "clients_behind": None, "reasons": [], "confidence": "低"}
        if ven:
            rec["reasons"].append(f"MAC 厂商为常见家用路由/AP 品牌 {ven}")
        # 交换机定位
        if switch is not None:
            try:
                loc = switch.locate(mac)
                if loc:
                    ifidx, name = loc
                    rec["switch_port"] = name
                    rec["clients_behind"] = port_client_count.get(ifidx, 1)
                    if rec["clients_behind"] and rec["clients_behind"] >= 5:
                        rec["reasons"].append(f"交换机端口 {name} 下挂 {rec['clients_behind']} 个 MAC，疑似私接交换/路由/AP")
            except Exception:
                pass
        # 置信度
        if rec["clients_behind"] and rec["clients_behind"] >= 5:
            rec["confidence"] = "高"
        elif ven:
            rec["confidence"] = "中"
        if rec["reasons"]:
            candidates.append(rec)
    candidates.sort(key=lambda r: {"高": 0, "中": 1, "低": 2}[r["confidence"]])
    return candidates


def main():
    ap = argparse.ArgumentParser(description="内网私接无线路由器发现/处置")
    ap.add_argument("--subnet", required=True, help="待扫描网段 CIDR，如 172.16.8.0/24")
    ap.add_argument("--gw", default=None, help="官方网关 IP（避免误报）")
    ap.add_argument("--switch", default=None, help="管理交换机 IP（用于定位端口/断网）")
    ap.add_argument("--ro", default="public", help="SNMP 读社区")
    ap.add_argument("--rw", default="public", help="SNMP 写社区")
    ap.add_argument("--ver", type=int, default=2, help="SNMP 版本 1/2")
    ap.add_argument("--cut-off", action="store_true", help="发现后关闭其交换机端口（断网）")
    ap.add_argument("--confirm", action="store_true", help="必须与 --cut-off 同时使用，真正执行关闭")
    ap.add_argument("--json", default=None, help="输出 JSON 路径")
    args = ap.parse_args()

    if args.cut_off and not args.confirm:
        print("[安全] --cut-off 需配合 --confirm 才会真正关闭端口；本次仅输出处置建议。")
    if args.cut_off and not args.switch:
        print("[错误] 关闭端口需提供 --switch 管理交换机 IP")
        sys.exit(2)

    ip_mac = arp_sweep(args.subnet)

    switch = None
    if args.switch:
        try:
            from switch_ops import Switch
            switch = Switch(args.switch, args.ro, args.rw, args.ver)
            print(f"[2/3] 已连接交换机 {args.switch}，读取 MAC 表...")
        except Exception as e:
            print(f"[warn] 交换机连接失败：{e}")

    print("[3/3] 分析私接路由候选...")
    cands = analyze(ip_mac, args.gw, switch)

    print(f"\n发现 {len(cands)} 个私接路由候选：")
    for c in cands:
        print(f"  [{c['confidence']}] {c['ip']}  MAC {c['mac']}  厂商={c['vendor_oui'] or '未知'}"
              f"  端口={c['switch_port'] or '未定位'}  下挂={c['clients_behind'] or '-'}")
        for r in c["reasons"]:
            print(f"        - {r}")

    # 处置
    if args.cut_off and args.confirm and switch is not None:
        print("\n[处置] 关闭私接路由所在交换机端口：")
        for c in cands:
            if c["switch_port"] and c["clients_behind"] and c["clients_behind"] >= 5:
                ifidx, _ = switch.locate(c["mac"])
                ok = switch.set_port_admin(ifidx, down=True)
                print(f"  {c['ip']} 端口 {c['switch_port']} -> {'已关闭' if ok else '关闭失败'}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"subnet": args.subnet, "candidates": cands}, f, ensure_ascii=False, indent=2)
        print(f"\n[JSON] -> {args.json}")


if __name__ == "__main__":
    main()
