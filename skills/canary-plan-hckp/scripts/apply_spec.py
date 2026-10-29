#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「专业规范包」生成结构化交付物。

用法：
    python3 apply_spec.py --spec <spec.json> --input <input.json> --out <out.md>

缺少规范包时以退出码 3 终止且不产出任何文件。
仅用 Python 标准库。
"""
import os
import argparse
import json
import sys
from pathlib import Path

EXIT_OK, EXIT_USAGE, EXIT_NO_SPEC, EXIT_ERROR = 0, 2, 3, 4


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def score_item(item, dims):
    """按维度判据关键词核对 evidence，返回每条命中情况与加权得分。"""
    ev = str(item.get("evidence", ""))
    rows = []
    total = 0.0
    for d in dims:
        keys = [k for k in str(d.get("criteria", "")).replace("，", ",").replace("、", ",")
                .replace(" ", "").split(",") if k]
        hit = any(k and k in ev for k in keys) if keys else bool(ev)
        total += float(d.get("weight", 0)) * (100.0 if hit else 0.0) / 100.0
        rows.append((d.get("name", ""), d.get("weight", 0), "命中" if hit else "未命中", 100 if hit else 0))
    return rows, round(total, 1)


def render(spec, data):
    dims = spec.get("dimensions", [])
    items = data.get("items", [])
    subject = data.get("subject", "（未命名）")
    out = [f"# {spec.get('title','交付物')} · 交付物", "",
           f"对象：{subject}",
           f"规范包：{spec.get('goods_name','')}（{spec.get('archetype','')} / 单价 "
           f"{spec.get('price_fen',0)/100:.2f} 元）", ""]

    out += ["## 规范依据", ""]
    for i, s in enumerate(spec.get("standards", []), 1):
        out.append(f"{i}. {s}")
    if spec.get("benchmarks"):
        out += ["", "**参考基准**", "", "| 指标 | 参考值 |", "|------|--------|"]
        for k, v in spec["benchmarks"].items():
            out.append(f"| {k} | {v} |")
    out.append("")

    # 逐项分析表
    out += [f"## 逐项分析（{len(items)} 项）", ""]
    scored = []
    for it in items:
        rows, total = score_item(it, dims)
        scored.append((it.get("name", ""), total))
        out.append(f"### {it.get('name','')}　得分 {total}")
        out.append("")
        out.append("| 维度 | 权重 | 核对结果 | 得分 |")
        out.append("|------|------|----------|------|")
        for name, w, r, sc in rows:
            out.append(f"| {name} | {w} | {r} | {sc} |")
        out.append("")
    if not items:
        out.append("（未提供条目，仅输出规范依据）")
        out.append("")

    # 结论与建议
    out += ["## 结论与建议", ""]
    for sec in spec.get("sections", []):
        out.append(f"- **{sec}**：__（依据上表填写）__")
    if scored:
        avg = round(sum(s for _, s in scored) / len(scored), 1)
        low = [n for n, s in scored if s < 60]
        out += ["", f"整体平均得分：**{avg}**"]
        if low:
            out.append(f"优先改进项：{('、'.join(low))}")
    out += ["", "> 本交付物由本地脚本依据付费规范包生成，数值须由使用者复核后使用。", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="规范包驱动的交付物生成器")
    ap.add_argument("--spec", required=False, default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "canary-plan-hckp_spec.json"))
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if not Path(a.spec).exists():
        sys.stderr.write("缺少专业规范包（spec）——（WorkBuddy 免费版：规范包已随包提供，请确认 references/canary-plan-hckp_spec.json 存在）。\n")
        return EXIT_NO_SPEC
    try:
        spec = load(a.spec)
        data = load(a.input) if Path(a.input).exists() else {}
    except Exception as e:
        sys.stderr.write(f"输入解析失败：{e}\n")
        return EXIT_ERROR
    md = render(spec, data)
    Path(a.out).write_text(md, encoding="utf-8", newline="")
    print(f"已生成 {a.out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
