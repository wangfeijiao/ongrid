#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cloud_ip_checker.py — 判断一个 IP 是否为「云服务主机 IP」（即云厂商虚机/实例所用网段），
并输出具体的云服务归属单位；若不是，则输出「否」。

判定思路（多源融合，互相印证）：
  1. RDAP 查询（跨 RIR 统一走 rdap.arin.net，能返回 APNIC/RIPE 等区域的数据）：
     - 网络名称（name）：如 ALISOFT / GOGL / MSFT / AT-88-Z / TencentCloud
     - 实体 handle（entities）：如 ZM1015-AP / APA7-AP
     - remarks 备注：常含完整公司名与地址，如 "Aliyun Computing Co., LTD"
     - 实体 vcard 中的 org 字段
  2. 反向 DNS（PTR）：云主机常带品牌后缀，如
     *.compute.amazonaws.com / *.bc.googleusercontent.com / *.aliyuncs.com
     / *.tencentcloud.com / *.myhuaweicloud.com

输出：标准 JSON（含判定结论与证据），末尾打印一行 RESULT 供人/智能体直接读取。

用法：
  python3 check_cloud_ip.py 47.119.176.109
  python3 check_cloud_ip.py 8.8.8.8
  cat ip_list.txt | python3 check_cloud_ip.py -      # 每行一个 IP，批量
"""

import sys
import json
import socket
import ipaddress
import argparse
import requests

RDAP_PRIMARY = "https://rdap.arin.net/registry"
RDAP_FALLBACK = [
    "https://rdap.db.ripe.net",
    "https://rdap.apnic.net",
    "https://rdap.afrinic.net/rdap",
    "https://rdap.lacnic.net/rdap",
]

# ---------------------------------------------------------------------------
# 云服务厂商特征库
#   cloud=True  : 专用云品牌，命中即判定为云主机
#   cloud=False : 运营商/ISP，仅在出现云专属子域/关键词时才算云（否则视为普通宽带/IDC，判定为「否」）
# patterns 统一小写匹配（name/handle/remarks/ptr/实体org 拼接后的文本）
# ---------------------------------------------------------------------------
CLOUD_PROVIDERS = [
    # ---- 国内主流公有云（专用云品牌，命中即云） ----
    {
        "cn": "阿里云（Alibaba Cloud）", "en": "Alibaba Cloud",
        "cloud": True,
        "patterns": ["alibaba", "aliyun", "alisoft", "alibabacloud",
                     ".aliyuncs.com", ".aliyun.com", ".alicdn.com", ".alibabadns.com"],
    },
    {
        "cn": "腾讯云（Tencent Cloud）", "en": "Tencent Cloud",
        "cloud": True,
        "patterns": ["tencent", "aceville", "tencentcloud",
                     ".tencentcloud.com", ".tencentcloudapi.com", ".myqcloud.com",
                     "shenzhen tencent", "tencent cloud"],
    },
    {
        "cn": "华为云（Huawei Cloud）", "en": "Huawei Cloud",
        "cloud": True,
        "patterns": ["huawei", ".myhuaweicloud.com", "huawei cloud", "huawei technologies co"],
    },
    {
        "cn": "百度智能云（Baidu AI Cloud）", "en": "Baidu Cloud",
        "cloud": True,
        "patterns": ["baidu", ".baidubce.com", "baidu online network", "baidu cloud", "baidu netcom"],
    },
    {
        "cn": "京东云（JD Cloud）", "en": "JD Cloud",
        "cloud": True,
        "patterns": ["jingdong", "jd cloud", ".jdcloud.com", "beijing jingdong", "jd.com"],
    },
    {
        "cn": "金山云（Kingsoft Cloud）", "en": "Kingsoft Cloud",
        "cloud": True,
        "patterns": ["kingsoft", ".ksyun.com", "kingsoft cloud"],
    },
    {
        "cn": "火山引擎（火山引擎/字节跳动）", "en": "Volcano Engine",
        "cloud": True,
        "patterns": ["volcano engine", "beijing volcano", ".volcengine.com", "bytedance",
                     ".byted.org", ".tiktokcdn.com", ".bytecdn.com"],
    },
    {
        "cn": "UCloud（优刻得）", "en": "UCloud",
        "cloud": True,
        "patterns": ["ucloud", ".ucloud.cn", "shanghai ucloud", "ucloud information technology"],
    },
    {
        "cn": "青云（QingCloud）", "en": "QingCloud",
        "cloud": True,
        "patterns": ["qingcloud", "yunify", ".qingcloud.com"],
    },
    {
        "cn": "七牛云（Qiniu）", "en": "Qiniu Cloud",
        "cloud": True,
        "patterns": [".qiniu.com", "qiniu", "qiniu cloud"],
    },
    {
        "cn": "又拍云（UPYun）", "en": "UPYun",
        "cloud": True,
        "patterns": [".upyun.com", "upyun"],
    },
    {
        "cn": "白山云（BaishanCloud）", "en": "Baishan Cloud",
        "cloud": True,
        "patterns": ["baishan", ".baishancloud.com", "baishan cloud"],
    },
    {
        "cn": "网宿科技（Wangsu/CDN）", "en": "Wangsu",
        "cloud": True,
        "patterns": ["wangsu", ".wscdn.com", "chinanetcenter", "netcenter"],
    },
    {
        "cn": "天翼云（中国电信云）", "en": "China Telecom e-Surfing Cloud",
        "cloud": True,
        "patterns": [".ctyun.cn", "ctyun", "e-surfing cloud", "chinatelecom cloud", ".ctyun.com"],
    },
    {
        "cn": "移动云（中国移动云）", "en": "China Mobile Cloud",
        "cloud": True,
        "patterns": [".cmic.chinamobile.com", ".cmcccloud.com", "mobile cloud", "chinamobile cloud"],
    },
    {
        "cn": "联通云（中国联通云）", "en": "China Unicom Cloud",
        "cloud": True,
        "patterns": [".chinaunicom.cn", ".cucccloud.com", "unicom cloud", "china unicom cloud"],
    },
    # ---- 国际主流公有云 ----
    {
        "cn": "亚马逊云科技（AWS）", "en": "Amazon Web Services",
        "cloud": True,
        "patterns": ["amazon", "amazon technologies", ".amazonaws.com", "amazon.com", "at-"],
    },
    {
        "cn": "微软云（Microsoft Azure）", "en": "Microsoft Azure",
        "cloud": True,
        "patterns": ["microsoft", "msft", ".azure", ".cloudapp.net", ".msftncsi.com",
                     "microsoft corporation", ".windows.net"],
    },
    {
        "cn": "谷歌云（Google Cloud）", "en": "Google Cloud",
        "cloud": True,
        "patterns": ["google", "googl", ".googleusercontent.com", ".bc.google",
                     "google cloud", ".google.com"],
    },
    {
        "cn": "Oracle Cloud（甲骨文云）", "en": "Oracle Cloud",
        "cloud": True,
        "patterns": ["oracle", ".oracle.com", "oracle cloud", "oracle cloud infrastructure"],
    },
    {
        "cn": "IBM Cloud（SoftLayer）", "en": "IBM Cloud",
        "cloud": True,
        "patterns": ["ibm", ".ibm.com", "softlayer", ".softlayer.com"],
    },
    {
        "cn": "Cloudflare", "en": "Cloudflare",
        "cloud": True,
        "patterns": ["cloudflare", ".cloudflare.com"],
    },
    {
        "cn": "DigitalOcean", "en": "DigitalOcean",
        "cloud": True,
        "patterns": ["digitalocean", ".digitalocean.com"],
    },
    {
        "cn": "Vultr", "en": "Vultr",
        "cloud": True,
        "patterns": ["vultr", ".vultr.com"],
    },
    {
        "cn": "Linode/Akamai", "en": "Linode",
        "cloud": True,
        "patterns": ["linode", ".linode.com", ".akamai.com"],
    },
    # ---- 运营商/ISP（普通宽带/专线，命中但无云专属信号 → 判定为「否」）----
    {
        "cn": "中国电信（运营商骨干网，非云主机）", "en": "China Telecom (ISP)",
        "cloud": False,
        "patterns": ["chinatelecom", "china telecom", ".chinatelecom.cn", ".telecom.cn",
                     "chinanet", ".chinanet.cn"],
    },
    {
        "cn": "中国联通（运营商骨干网，非云主机）", "en": "China Unicom (ISP)",
        "cloud": False,
        "patterns": ["chinaunicom", "china unicom", ".unicom.cn", "cncgroup"],
    },
    {
        "cn": "中国移动（运营商骨干网，非云主机）", "en": "China Mobile (ISP)",
        "cloud": False,
        "patterns": ["chinamobile", "china mobile", ".cmnet.com", ".chinamobile.com",
                     "cmnet", "china mobile communications"],
    },
    {
        "cn": "中国铁通/中国广电（运营商骨干网，非云主机）", "en": "China Railcom / Cable (ISP)",
        "cloud": False,
        "patterns": ["railcom", "chinabtn", "crcnet", "catv", ".cbn.cn", "cable television"],
    },
]


def is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip.strip())
        return True
    except ValueError:
        return False


def rdap_lookup(ip: str) -> dict:
    """依次尝试 RDAP 服务器，返回首个成功的 JSON，否则返回 {}。"""
    servers = [RDAP_PRIMARY] + RDAP_FALLBACK
    last_err = None
    for base in servers:
        try:
            r = requests.get(f"{base}/ip/{ip}", timeout=15,
                             headers={"Accept": "application/rdap+json"})
            if r.status_code == 200:
                return r.json()
            # 部分服务器对跨区 IP 返回 3xx/重定向链接
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                loc = r.headers["Location"]
                r2 = requests.get(loc, timeout=15, headers={"Accept": "application/rdap+json"})
                if r2.status_code == 200:
                    return r2.json()
            last_err = f"{base} -> {r.status_code}"
        except Exception as e:
            last_err = f"{base} -> {e}"
    return {}


def extract_text(j: dict) -> tuple:
    """从 RDAP JSON 抽取用于匹配的文本与证据片段。"""
    if not j:
        return "", []

    parts = []
    evidence = []

    name = j.get("name")
    if name:
        parts.append(str(name))
        evidence.append(f"network.name={name}")

    # 实体 handle + vcard org
    for ent in j.get("entities", []) or []:
        handle = ent.get("handle")
        if handle:
            parts.append(str(handle))
            evidence.append(f"entity.handle={handle}")
        vc = ent.get("vcardArray")
        if isinstance(vc, list) and len(vc) > 1:
            for item in vc[1]:
                if isinstance(item, list) and item and item[0] == "org":
                    org = item[3] if len(item) > 3 else (item[-1] if len(item) > 1 else None)
                    if org:
                        parts.append(str(org))
                        evidence.append(f"entity.org={org}")

    # remarks 备注（常含完整公司名与地址）
    for rm in j.get("remarks", []) or []:
        for line in rm.get("description", []) or []:
            if line:
                parts.append(str(line))
                if len(evidence) < 8:
                    evidence.append(f"remark={line[:60]}")

    # status
    for st in j.get("status", []) or []:
        parts.append(str(st))

    return " \n ".join(parts), evidence


def ptr_lookup(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def classify(text: str, ptr: str) -> dict:
    """基于融合文本 + PTR 匹配厂商库，返回判定结果。"""
    blob = (text + " " + (ptr or "")).lower()

    cloud_hits = []      # 命中专用云品牌
    isp_hits = []        # 命中运营商（普通网络）
    for p in CLOUD_PROVIDERS:
        matched = [pat for pat in p["patterns"] if pat in blob]
        if matched:
            entry = {"cn": p["cn"], "en": p["en"], "matched": matched}
            if p["cloud"]:
                cloud_hits.append(entry)
            else:
                isp_hits.append(entry)

    if cloud_hits:
        # 优先取匹配关键词最多的厂商
        best = max(cloud_hits, key=lambda x: len(x["matched"]))
        return {
            "is_cloud": True,
            "provider_cn": best["cn"],
            "provider_en": best["en"],
            "matched_patterns": best["matched"],
            "other_hits": [h["cn"] for h in cloud_hits if h["cn"] != best["cn"]] + [h["cn"] for h in isp_hits],
        }

    if isp_hits:
        # 仅命中运营商/ISP，无云专属信号 → 判定为「否」
        return {
            "is_cloud": False,
            "provider_cn": "否",
            "provider_en": "No",
            "matched_patterns": isp_hits[0]["matched"],
            "note": "命中运营商骨干网/ISP，但无云主机专属特征（如 *.ctyun.cn / *.cmcccloud.com 等），判定为普通网络而非云主机。",
        }

    return {
        "is_cloud": False,
        "provider_cn": "否",
        "provider_en": "No",
        "matched_patterns": [],
        "note": "未匹配到任何已知云服务厂商特征。",
    }


def check_one(ip: str) -> dict:
    ip = ip.strip()
    if not ip:
        return {"ip": ip, "error": "空 IP"}
    if not is_valid_ip(ip):
        return {"ip": ip, "error": "非法 IP 格式"}

    j = rdap_lookup(ip)
    text, evidence = extract_text(j)
    ptr = ptr_lookup(ip)
    result = classify(text, ptr)

    out = {
        "ip": ip,
        "is_cloud": result["is_cloud"],
        "provider": result["provider_cn"],
        "provider_en": result["provider_en"],
        "ptr": ptr,
        "matched_patterns": result.get("matched_patterns", []),
        "other_hits": result.get("other_hits", []),
        "note": result.get("note", ""),
        "evidence": evidence,
        "rdap_ok": bool(j),
    }
    if not j:
        out["warning"] = "RDAP 查询失败（网络受限或该 IP 无公开 RDAP 数据），结果仅基于 PTR/本地特征，可能不准确。"
    return out


def main():
    parser = argparse.ArgumentParser(description="判断 IP 是否为云服务主机 IP")
    parser.add_argument("ip", nargs="?", help="IP 地址；或用 - 从标准输入读取（每行一个）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    if args.ip == "-" or (args.ip is None):
        ips = [line.strip() for line in sys.stdin if line.strip()]
    else:
        ips = [args.ip]

    results = [check_one(ip) for ip in ips]

    if args.json:
        print(json.dumps(results if len(results) != 1 else results[0],
                         ensure_ascii=False, indent=2))
        return

    for r in results:
        if "error" in r:
            print(f"[ERROR] {r['ip']}: {r['error']}")
            continue
        if r["is_cloud"]:
            line = f"{r['ip']}  =>  是云服务主机 IP，归属单位：{r['provider']}"
            if r.get("other_hits"):
                line += f"（其他疑似：{', '.join(r['other_hits'])}）"
        else:
            line = f"{r['ip']}  =>  否"
            if r.get("note"):
                line += f"（{r['note']}）"
        print(line)
        extra = []
        if r.get("ptr"):
            extra.append(f"PTR={r['ptr']}")
        if r.get("matched_patterns"):
            extra.append(f"命中特征={r['matched_patterns']}")
        if extra:
            print("         " + "  ".join(extra))
        if r.get("warning"):
            print("         ⚠ " + r["warning"])
    # 末尾统一 RESULT 行，便于智能体直接解析
    if len(results) == 1 and "error" not in results[0]:
        r = results[0]
        print(f"RESULT: {r['provider'] if r['is_cloud'] else '否'}")


if __name__ == "__main__":
    main()
