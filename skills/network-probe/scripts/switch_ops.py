#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交换机 SNMP 操作：定位私接路由器所在的交换机端口，并可关闭/恢复该端口。

依赖（可选，见 env_setup.py）：
  - pysnmp（Python 原生，Windows 友好，优先）
  - 或系统 snmpwalk/snmpset（net-snmp，需额外安装）

核心 MIB：
  BRIDGE-MIB
    1.3.6.1.2.1.17.4.3.1.1  dot1dTpFdbAddress     MAC 地址表（需把 6 字节 MAC 转 OID 后缀）
    1.3.6.1.2.1.17.4.3.1.2  dot1dTpFdbPort        MAC -> 交换机本地端口号
    1.3.6.1.2.1.17.1.4.1.2  dot1dBasePortIfIndex 本地端口号 -> ifIndex
  IF-MIB
    1.3.6.1.2.1.2.2.1.2     ifDescr               ifIndex -> 端口名（Gig1/0/12）
    1.3.6.1.2.1.2.2.1.7     ifAdminStatus         1=up 2=down（可写）
    1.3.6.1.2.1.2.2.1.8     ifOperStatus          1=up 2=down（只读）

用法（作为模块 import）：
  from switch_ops import Switch
  sw = Switch("172.16.8.254", "public", "private", ver=2)
  tbl = sw.mac_to_port()          # {mac: ifIndex}
  print(sw.locate("aa:bb:cc:dd:ee:ff"))
  sw.set_port_admin(ifindex, down=True)   # 关闭端口（断网）
"""
import subprocess
import re


def _mac_to_oid_suffix(mac):
    return ".".join(f"{int(x, 16)}" for x in mac.split(":"))


def _oid_suffix_to_mac(suffix):
    return ":".join(f"{int(x):02x}" for x in suffix.split(".")[-6:])


class Switch:
    def __init__(self, host, community_ro="public", community_rw="public", ver=2,
                 use_binaries=False, timeout=3, retries=2):
        self.host = host
        self.ro = community_ro
        self.rw = community_rw
        self.ver = ver
        self.use_bin = use_binaries
        self.timeout = timeout
        self.retries = retries
        self._cache = {}

    # ---------- 底层 SNMP ----------
    def _walk(self, oid):
        """返回 [(oid_suffix_str, value_str), ...]"""
        rows = []
        if self.use_bin or not _have_pysnmp():
            rows = self._walk_bin(oid)
        else:
            try:
                rows = self._walk_pysnmp(oid)
            except Exception as e:
                print(f"[warn] pysnmp walk {oid} 失败：{e}，回退系统 snmpwalk")
                rows = self._walk_bin(oid)
        return rows

    def _walk_pysnmp(self, oid):
        from pysnmp.hlapi import (SnmpEngine, CommunityData, UdpTransportTarget,
                                  ContextData, ObjectType, ObjectIdentity,
                                  nextCmd, OctetString, Integer32)
        rows = []
        for (errInd, errStat, errIdx, varBinds) in nextCmd(
            SnmpEngine(),
            CommunityData(self.ro, mpModel=1 if self.ver == 2 else 0),
            UdpTransportTarget((self.host, 161), timeout=self.timeout, retries=self.retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            if errInd:
                raise RuntimeError(str(errInd))
            for vb in varBinds:
                oidstr = str(vb[0])
                val = vb[1]
                if isinstance(val, OctetString):
                    val = val.asOctets().hex()
                else:
                    val = str(val)
                suffix = oidstr[len(oid):].lstrip(".")
                rows.append((suffix, val))
        return rows

    def _walk_bin(self, oid):
        cmd = ["snmpwalk", "-v", str(self.ver), "-c", self.ro,
               "-t", str(self.timeout), "-r", str(self.retries), self.host, oid]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        rows = []
        for line in r.stdout.splitlines():
            m = re.search(rf"^{re.escape(oid)}\.?([\d.]+)\s*=\s*(?:Hex-STRING|STRING|INTEGER|Gauge32|Counter32)?\s*(.*)$", line)
            if m:
                suffix, val = m.group(1), m.group(2).strip().strip('"')
                if re.fullmatch(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", val):
                    val = val.replace("-", "").lower()  # mac hex
                rows.append((suffix, val))
        return rows

    def _set(self, oid, value):
        if self.use_bin or not _have_pysnmp():
            cmd = ["snmpset", "-v", str(self.ver), "-c", self.rw, self.host, oid,
                   "i", str(value)]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            return r.returncode == 0
        from pysnmp.hlapi import (SnmpEngine, CommunityData, UdpTransportTarget,
                                  ContextData, ObjectType, ObjectIdentity, Integer32)
        from pysnmp.proto.rfc1902 import Integer
        from pysnmp.hlapi import setCmd
        for (errInd, *_rest) in setCmd(
            SnmpEngine(),
            CommunityData(self.rw, mpModel=1 if self.ver == 2 else 0),
            UdpTransportTarget((self.host, 161), timeout=self.timeout, retries=self.retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid), Integer(value)),
        ):
            if errInd:
                raise RuntimeError(str(errInd))
        return True

    # ---------- 高层 API ----------
    def mac_to_port(self):
        """返回 {mac_lower: ifIndex(int)}，需要 BRIDGE-MIB 可读 + SNMP RW/RO 社区"""
        addrs = {r[0]: r[1] for r in self._walk("1.3.6.1.2.1.17.4.3.1.1")}  # suffix->mac hex
        ports = {r[0]: int(r[1]) for r in self._walk("1.3.6.1.2.1.17.4.3.1.2")}  # suffix->bridge port
        bp2if = {int(r[0]): int(r[1]) for r in self._walk("1.3.6.1.2.1.17.1.4.1.2")}  # bridge port->ifIndex
        out = {}
        for suffix, mac in addrs.items():
            bp = ports.get(suffix)
            if bp is None:
                continue
            ifidx = bp2if.get(bp)
            if ifidx is None:
                continue
            out[mac.lower()] = ifidx
        self._cache["mac_to_port"] = out
        return out

    def ifindex_to_descr(self):
        return {int(r[0]): r[1] for r in self._walk("1.3.6.1.2.1.2.2.1.2")}

    def locate(self, mac):
        """返回 (ifIndex, port_name) 或 None，mac 形如 aa:bb:cc:dd:ee:ff"""
        m2p = self._cache.get("mac_to_port") or self.mac_to_port()
        ifidx = m2p.get(mac.lower())
        if ifidx is None:
            return None
        descr = self.ifindex_to_descr().get(ifidx, f"ifIndex {ifidx}")
        return ifidx, descr

    def port_clients_count(self):
        """统计每个 ifIndex 下学习到的 MAC 数（多 MAC = 后面可能接了交换机/AP/集线器）"""
        m2p = self._cache.get("mac_to_port") or self.mac_to_port()
        cnt = {}
        for ifidx in m2p.values():
            cnt[ifidx] = cnt.get(ifidx, 0) + 1
        return cnt

    def set_port_admin(self, ifindex, down=True):
        """关闭/恢复端口（断网/恢复联网）。down=True 关闭。需 SNMP 写社区。"""
        oid = f"1.3.6.1.2.1.2.2.1.7.{ifindex}"
        return self._set(oid, 2 if down else 1)

    def port_status(self, ifindex):
        """返回 {admin, oper, descr, in_errors, out_errors}（1/2 分别对应 up/down）"""
        admin = self._get(f"1.3.6.1.2.1.2.2.1.7.{ifindex}")
        oper = self._get(f"1.3.6.1.2.1.2.2.1.8.{ifindex}")
        descr = self._get(f"1.3.6.1.2.1.2.2.1.2.{ifindex}")
        inerr = self._get(f"1.3.6.1.2.1.2.2.1.14.{ifindex}")
        outerr = self._get(f"1.3.6.1.2.1.2.2.1.20.{ifindex}")
        return {
            "ifindex": ifindex,
            "admin": int(admin) if admin and admin.isdigit() else None,
            "oper": int(oper) if oper and oper.isdigit() else None,
            "descr": descr,
            "in_errors": int(inerr) if inerr and inerr.isdigit() else 0,
            "out_errors": int(outerr) if outerr and outerr.isdigit() else 0,
        }

    def _get(self, oid):
        rows = self._walk(oid)
        if not rows:
            return None
        return rows[0][1]


def _have_pysnmp():
    try:
        import pysnmp  # noqa
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="交换机 SNMP 操作")
    ap.add_argument("host", help="交换机管理 IP")
    ap.add_argument("--ro", default="public")
    ap.add_argument("--rw", default="public")
    ap.add_argument("--ver", type=int, default=2)
    ap.add_argument("--bin", action="store_true", help="强制用系统 snmpwalk/snmpset")
    ap.add_argument("--locate", help="查某 MAC 的端口，如 aa:bb:cc:dd:ee:ff")
    ap.add_argument("--cut", help="关闭该 MAC 所在端口（断网，需确认）")
    ap.add_argument("--restore", help="恢复该 MAC 所在端口")
    args = ap.parse_args()
    sw = Switch(args.host, args.ro, args.rw, args.ver, use_binaries=args.bin)
    if args.locate:
        r = sw.locate(args.locate)
        print(r)
    elif args.cut:
        ifidx, name = sw.locate(args.cut)
        print(f"将关闭端口 {name} (ifIndex {ifidx}) ...")
        print("OK" if sw.set_port_admin(ifidx, down=True) else "FAILED")
    elif args.restore:
        ifidx, name = sw.locate(args.restore)
        print(f"将恢复端口 {name} (ifIndex {ifidx}) ...")
        print("OK" if sw.set_port_admin(ifidx, down=False) else "FAILED")
    else:
        tbl = sw.mac_to_port()
        print(json.dumps(tbl, ensure_ascii=False, indent=2))
