#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汇总生成《网络探针诊断报告》（Markdown / 纯文本）

合并三类产物：
  --status   net_status.py 的 JSON（网络分层状态）
  --rogue    rogue_scan.py 的 JSON（私接路由候选）
  --diag     diagnose.py 的 JSON（设备无网络诊断，单设备为 dict，批量为 list）

报告章节：
  一、网络整体状态（自动 ping）
  二、私接无线路由器排查
  三、设备无网络诊断
  四、处置建议汇总

用法：
  python make_report.py --status status.json --rogue rogue.json --diag diag.json --out 报告.md
"""
import argparse
import json
import os


def load(p):
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 {p} 失败：{e}")
        return None


def fmt_status(s):
    if not s:
        return ["（未提供网络状态数据）"]
    out = []
    names = {"L0_local": "本地协议栈", "L1_gateway": "默认网关", "L2_dns": "DNS 解析", "L3_external": "外网连通"}
    for k, label in names.items():
        lv = (s.get("layers") or {}).get(k)
        if not lv:
            continue
        mark = "✓" if lv["ok"] else "✗"
        out.append(f"  [{mark}] {label:<8} {lv['detail']}")
    out.append(f"\n  结论：{s.get('summary', '—')}")
    return out


def fmt_rogue(r):
    if not r:
        return ["（未提供私接路由排查数据）"]
    cands = r.get("candidates", [])
    if not cands:
        return [f"网段 {r.get('subnet','?')}：未发现私接无线路由器候选。"]
    out = [f"网段 {r.get('subnet','?')}：发现 {len(cands)} 个候选（按置信度排序）"]
    for c in cands:
        out.append(f"  [{c['confidence']}] {c['ip']}  MAC {c['mac']}  厂商={c['vendor_oui'] or '未知'}"
                   f"  端口={c['switch_port'] or '未定位'}  下挂={c['clients_behind'] or '-'}")
        for reason in c.get("reasons", []):
            out.append(f"        - {reason}")
    return out


def fmt_diag(d):
    if not d:
        return ["（未提供设备诊断数据）"]
    if isinstance(d, dict):
        d = [d]
    out = []
    for r in d:
        out.append(f"  设备 {r.get('target')}：{'在线' if r.get('online') else '不可达'}"
                   + (f"  MAC {r['arp_mac']}" if r.get('arp_mac') else "")
                   + (f"  端口 {r['switch_port']}" if r.get('switch_port') else ""))
        out.append(f"    推断根因：{r.get('cause')}")
        if r.get("actions"):
            out.append("    处置：")
            for a in r["actions"]:
                out.append(f"      > {a}")
    return out


def main():
    ap = argparse.ArgumentParser(description="汇总网络探针报告")
    ap.add_argument("--status", default=None)
    ap.add_argument("--rogue", default=None)
    ap.add_argument("--diag", default=None)
    ap.add_argument("--out", default="网络探针诊断报告.md")
    args = ap.parse_args()

    status, rogue, diag = load(args.status), load(args.rogue), load(args.diag)
    if not any([status, rogue, diag]):
        print("[error] 至少提供一个 --status/--rogue/--diag 输入", file=__import__("sys").stderr)
        raise SystemExit(2)

    lines = ["# 网络探针诊断报告", ""]
    lines.append("## 一、网络整体状态（自动 ping）")
    lines += fmt_status(status)
    lines.append("")
    lines.append("## 二、私接无线路由器排查")
    lines += fmt_rogue(rogue)
    lines.append("")
    lines.append("## 三、设备无网络诊断")
    lines += fmt_diag(diag)
    lines.append("")
    lines.append("## 四、处置建议汇总")
    lines.append("  1. 私接路由确认为 unauthorized 后，经授权在交换机关闭对应端口（断网），")
    lines.append("     或将其 MAC 加入网康 ICG / 网关黑名单。")
    lines.append("  2. 设备无网络优先排查端口级（交换机 admin/oper 状态、错包），")
    lines.append("     再查终端侧网关/DNS/DHCP 与 IP 冲突。")
    lines.append("  3. 所有断网/关闭端口操作须走变更审批，操作前确认影响范围。")

    text = "\n".join(lines) + "\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"报告已生成 → {args.out}")
    print(text)


if __name__ == "__main__":
    main()
