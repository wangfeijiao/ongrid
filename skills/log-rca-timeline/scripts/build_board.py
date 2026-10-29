#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""log-rca-timeline / 日志分析与故障根因定位 —— data.json -> report.html

用法:
    python build_board.py <run_dir> [--out report.html]

功能:
  1. 字段与枚举校验（meta / buckets / patterns / hypotheses / timeline / flags / sources）
  2. 补齐派生字段（bucket t_label、pattern unit 缺省、hypothesis rank）
  3. 一致性校验：pattern 时间窗必须落在 buckets 时间范围内；假设分数必须为 0-100
  4. 渲染 assets/board_template.html -> <run_dir>/report.html

仅依赖标准库。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REQUIRED_META = ["title", "subtitle", "scope", "window", "generated_at"]
SEVS = ["高", "中", "低"]
CONFS = ["A", "B", "C"]

WARNINGS = []
ERRORS = []


def warn(m):
    WARNINGS.append(m)


def err(m):
    ERRORS.append(m)


def check_url(u, where):
    if not u:
        return False
    if not (str(u).startswith("http://") or str(u).startswith("https://")):
        warn("%s 的 URL 不是 http(s)：%s" % (where, u))
        return False
    return True


def validate(data):
    meta = data.get("meta") or {}
    for k in REQUIRED_META:
        if not meta.get(k):
            err("meta.%s 缺失" % k)
    for k in ("parsed", "parse_rate", "buckets", "bucket_size", "span_label"):
        if meta.get(k) is None:
            warn("meta.%s 缺失，看板将显示为「—」" % k)

    buckets = data.get("buckets") or []
    if not buckets:
        err("buckets 为空，至少需要 1 个时间窗")
        return
    if len(buckets) < 6:
        warn("时间窗仅 %d 个，统计基线可靠性有限" % len(buckets))
    t0 = min(b["t"] for b in buckets)
    t1 = max(b["t"] for b in buckets)
    bs = meta.get("bucket_size") or 60
    for i, b in enumerate(buckets):
        if b.get("t") is None:
            err("buckets[%d] 缺 t" % i)
            continue
        if not b.get("t_label"):
            b["t_label"] = dt.datetime.fromtimestamp(b["t"]).strftime("%Y-%m-%d %H:%M:%S")
        for k in ("total", "errors", "error_rate"):
            if b.get(k) is None:
                b[k] = 0
    data["buckets"] = sorted(buckets, key=lambda x: x["t"])

    patterns = data.get("patterns") or []
    for i, p in enumerate(patterns):
        tag = p.get("id") or ("#%d" % i)
        for k in ("type", "name", "severity", "sev", "window_start", "window_end", "why"):
            if p.get(k) is None:
                err("patterns[%s] 缺字段 %s" % (tag, k))
        if p.get("severity") and p["severity"] not in SEVS:
            err("patterns[%s] 的 severity=%r 不在 高/中/低 内" % (tag, p["severity"]))
        if p.get("unit") is None:
            p["unit"] = ""
        if p.get("sev") is None:
            p["sev"] = {"高": 3, "中": 2, "低": 1}.get(p.get("severity"), 1)
        ws, we = p.get("window_start"), p.get("window_end")
        if ws is not None and (ws < t0 - bs or ws > t1 + bs):
            warn("patterns[%s] 的起始时间落在聚合区间之外，时间线可能错位" % tag)
        if ws is not None and we is not None and we < ws:
            err("patterns[%s] 的 window_end 早于 window_start" % tag)
        if p.get("duration_min") is None and ws is not None and we is not None:
            p["duration_min"] = round((we - ws) / 60.0, 1)

    hyps = data.get("hypotheses") or []
    for i, h in enumerate(hyps):
        tag = h.get("id") or ("#%d" % i)
        for k in ("name", "cat", "score", "confidence", "verify", "falsify"):
            if h.get(k) is None:
                err("hypotheses[%s] 缺字段 %s" % (tag, k))
        s = h.get("score")
        if s is not None:
            if not isinstance(s, int) or s < 0 or s > 100:
                err("hypotheses[%s] 的 score=%r 必须是 0-100 的整数" % (tag, s))
        if h.get("confidence") and h["confidence"] not in CONFS:
            err("hypotheses[%s] 的 confidence=%r 不在 A/B/C 内" % (tag, h["confidence"]))
        if h.get("rank") is None:
            h["rank"] = i + 1
        if not h.get("evidence_items"):
            warn("hypotheses[%s] 没有任何证据项，看板将显示「无直接证据」" % tag)
    ranks = [h.get("rank") for h in hyps]
    if ranks and sorted(ranks) != list(range(1, len(hyps) + 1)):
        warn("hypotheses 的 rank 不是 1..N 连续序列，已按列表顺序重排")
        for i, h in enumerate(hyps):
            h["rank"] = i + 1
    scores = [h.get("score", 0) for h in hyps]
    if scores and scores != sorted(scores, reverse=True):
        warn("hypotheses 未按分数降序排列，看板排序以 score 为准")
        data["hypotheses"] = sorted(hyps, key=lambda x: -x.get("score", 0))
        for i, h in enumerate(data["hypotheses"]):
            h["rank"] = i + 1

    tl = data.get("timeline") or []
    for i, e in enumerate(tl):
        for k in ("t", "t_label", "phase", "title"):
            if e.get(k) is None:
                err("timeline[%d] 缺字段 %s" % (i, k))
    if tl and [e.get("t") for e in tl] != sorted(e["t"] for e in tl):
        warn("timeline 未按时间升序，已重排")
        data["timeline"] = sorted(tl, key=lambda x: x.get("t") or 0)

    flags = data.get("flags") or []
    if not flags:
        err("flags 为空，至少写明一条分析局限或待核验项")
    if not patterns:
        if not any("未检出" in str(f) for f in flags):
            err("patterns 为空时，flags 必须写明「未检出显著异常模式」")

    for s in (data.get("sources") or []):
        check_url(s.get("url"), "sources." + str(s.get("name")))
    if not data.get("sources"):
        warn("sources 为空，看板方法论参考区将为空")


def json_for_embed(obj):
    return json.dumps(obj, ensure_ascii=False, indent=None).replace("</", "<\\/")


def main():
    ap = argparse.ArgumentParser(description="日志根因定位 data.json -> report.html")
    ap.add_argument("run_dir", help="运行目录（含 data.json）")
    ap.add_argument("--out", default="report.html")
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    data_path = os.path.join(run_dir, "data.json")
    if not os.path.isfile(data_path):
        print("[FATAL] 未找到 data.json: %s" % data_path)
        sys.exit(2)
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("[FATAL] data.json 解析失败: %s" % e)
        sys.exit(2)

    validate(data)

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tpl_path = os.path.join(skill_dir, "assets", "board_template.html")
    if not os.path.isfile(tpl_path):
        print("[FATAL] 未找到模板: %s" % tpl_path)
        sys.exit(2)
    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    meta = data.get("meta") or {}
    repl = {
        "__TITLE__": meta.get("title") or "日志分析与故障根因定位",
        "__SUBTITLE__": meta.get("subtitle") or "",
        "__SCOPE__": meta.get("scope") or "",
        "__SPAN__": meta.get("span_label") or "",
        "__WINDOW__": meta.get("window") or "",
        "__GENERATED__": meta.get("generated_at") or "",
    }
    html = tpl
    for k, v in repl.items():
        html = html.replace(k, str(v))
    html = html.replace("__DATA_JSON__", json_for_embed(data))

    out_path = os.path.join(run_dir, args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    n_high = sum(1 for p in (data.get("patterns") or []) if p.get("sev") == 3)
    print("[OK] 看板已生成: %s" % out_path)
    print("     时间窗 %d | 异常模式 %d 类（高严重度 %d） | 根因假设 %d 条 | 时间线 %d 个事件"
          % (len(data.get("buckets") or []), len(data.get("patterns") or []), n_high,
             len(data.get("hypotheses") or []), len(data.get("timeline") or [])))
    if WARNINGS:
        print("\n[WARN] %d 条提示:" % len(WARNINGS))
        for w in WARNINGS:
            print("  - " + w)
    if ERRORS:
        print("\n[ERROR] %d 项校验未通过:" % len(ERRORS))
        for e in ERRORS:
            print("  - " + e)
        print("\n已生成 HTML，但数据存在硬性错误，请先修复 data.json 后重新构建。")
        sys.exit(1)
    print("\n[PASS] 全部校验通过，可交付。")


if __name__ == "__main__":
    main()
