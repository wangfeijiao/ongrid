# -*- coding: utf-8 -*-
"""对目标主机做常见端口 TCP 连通性探测（超时 0.5s）。"""
import argparse, socket

COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 443, 445,
                3306, 3389, 5432, 6379, 8080, 8443, 9000, 9200, 27017]

def main():
    ap = argparse.ArgumentParser(description="端口连通性扫描")
    ap.add_argument("--host", required=True)
    ap.add_argument("--ports", default="", help="逗号分隔自定义端口，缺省扫常见端口")
    args = ap.parse_args()
    ports = [int(x) for x in args.ports.split(",") if x.strip()] or COMMON_PORTS
    print(f"扫描 {args.host} 的 {len(ports)} 个端口:")
    open_ports = []
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect((args.host, p))
            print(f"  [开放] {p}")
            open_ports.append(p)
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
        finally:
            s.close()
    print(f"\n开放端口: {open_ports if open_ports else '无'}")

if __name__ == "__main__":
    main()
