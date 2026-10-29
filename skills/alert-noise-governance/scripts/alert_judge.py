#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""告警判据工具箱 —— 把「误报十二型」里最容易写错的三类判据做成可直接调用的纯函数。

纯标准库、无外部依赖、可单测。四类能力：

1. window  —— 窗口感知判定：先解析调度表达式，判断"此刻是否应在运行窗口内"（治型 7）
2. classify —— 错误分类不混淆：超时 / 限流 / 负载 / 权限 / 数据 / 磁盘 ……（治型 9）
3. dedupe  —— 去重指纹规范化：类别 + 具体项 + 形态；且只在成功路径写位（治型 11）
4. recheck —— 窗口期容忍复检：首次失败先复检，成功即静默（治型 1、2）

用法：
    python3 alert_judge.py --selftest
    python3 alert_judge.py window   --cron "*/20 9-21 * * *" --at "2026-09-15T03:10"
    python3 alert_judge.py classify --msg "connection timed out after 30s"
    python3 alert_judge.py dedupe   --item "disk" --form "usage=97%" --stage success
    python3 alert_judge.py ladder   --attempt 7
"""
import argparse
import datetime as dt
import hashlib
import json
import sys

# --------------------------------------------------------------------------
# 1. 窗口感知：crontab 表达式匹配（治型 7「窗口/排班语义不符」）
# --------------------------------------------------------------------------
DOW = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def _expand(field, lo, hi, aliases=None):
    """展开单段 crontab 字段 → set(int)；非法语法返回 None。"""
    if aliases:
        field = field.lower()
        for i, name in enumerate(aliases):
            field = field.replace(name, str(i))
    out = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            if not s.isdigit() or int(s) == 0:
                return None
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part.lstrip("-"):
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return None
            a, b = int(a), int(b)
        else:
            if not part.isdigit():
                return None
            a = b = int(part)
        if a < lo or b > hi or a > b:
            return None
        out.update(range(a, b + 1, step))
    return out or None


def cron_matches(expr, when):
    """when 为 datetime；表达式 5 段：分 时 日 月 周。(是否匹配, 错误说明)"""
    parts = expr.split()
    if len(parts) != 5:
        return None, "表达式必须是 5 段（分 时 日 月 周）"
    mi, ho, dom, mo, dow = parts
    sets = [
        _expand(mi, 0, 59),
        _expand(ho, 0, 23),
        _expand(dom, 1, 31),
        _expand(mo, 1, 12),
        _expand(dow, 0, 6, aliases=DOW),
    ]
    if any(s is None for s in sets):
        return None, "字段语法无法解析：%s" % expr
    mins, hours, doms, months, dows = sets
    wd = when.weekday()                # Mon=0
    cron_dow = (wd + 1) % 7            # cron: Sun=0
    ok = (when.minute in mins and when.hour in hours
          and when.day in doms and when.month in months and cron_dow in dows)
    return bool(ok), None


def window_aware(expr, when):
    """返回 (verdict, exit_code)。窗口外要静默；窗口内超期才允许告警。

    verdict ∈ {in_window, out_of_window}；out_of_window ⇒ 调用方必须静默。
    """
    ok, err = cron_matches(expr, when)
    if ok is None:
        return "invalid_expression: %s" % err, 2
    return ("in_window" if ok else "out_of_window"), (0 if ok else 3)


# --------------------------------------------------------------------------
# 2. 错误分类：不混淆不同根因（治型 9「错误信息误导」）
# --------------------------------------------------------------------------
ERROR_RULES = [
    ("quota",      ["rate limit", "rate-limit", "ratelimit", "too many requests", "429", "quota exceeded", "限流", "quota"]),
    ("network",    ["timed out", "timeout", "connection refused", "connection reset", "unreachable",
                    "name resolution", "getaddrinfo", "dns", "no route to host", "tls", "ssl",
                    "proxy", "econnrefused", "超时", "连接被拒"]),
    ("load",       ["queue", "overload", "busy", "saturated", "backpressure", "排队", "过载", "单槽"]),
    ("auth",       ["401", "403", "unauthorized", "forbidden", "permission denied", "token", "credential", "鉴权", "无权"]),
    ("disk",       ["no space left", "disk full", "enospc", "space exhausted", "磁盘", "空间不足"]),
    ("data",       ["integrity", "corrupt", "malformed", "parse error", "schema", "checksum", "损坏", "解析失败"]),
]
LAYER_HINT = {
    "quota":   "配额/限流层 —— 换后端、退避重试或申请额度；不要当成网络故障。",
    "network": "网络层 —— 查代理/网关地址/DNS/端口可达性；不要把超时一律改写成『限流』。",
    "load":    "负载层 —— 查并发与单槽占用、放宽超时预算；不要当成服务故障。",
    "auth":    "凭据/权限层 —— 查令牌有效期与作用域；不要当成网络故障。",
    "disk":    "存储层 —— 查容量与 inode；不要当成应用崩溃。",
    "data":    "数据层 —— 查来源与写入完整性；不要当成环境问题。",
    "unknown": "未能归类 —— 保留原始报错原文，禁止臆断归类后继续排查。",
}


def classify_error(msg):
    """把报错文本归到具体层 → (kind, hint)。归类必须唯一、可解释。"""
    low = (msg or "").lower()
    for kind, keys in ERROR_RULES:
        if any(k in low for k in keys):
            return kind, LAYER_HINT[kind]
    return "unknown", LAYER_HINT["unknown"]


# --------------------------------------------------------------------------
# 3. 去重指纹：含"哪一项 + 什么形态"；且只在成功路径写位（治型 11）
# --------------------------------------------------------------------------
def fingerprint(category, item, form):
    """稳定、可读的指纹（含形态，避免假警与真退化共用一键）。"""
    def norm(s):
        return " ".join(str(s or "").strip().lower().split())
    return "|".join([norm(category), norm(item), norm(form)])


def record_attempt(store, key, stage, note="", now=None):
    """stage='success' 才写去重位；'failure' 只累加尝试次数（失败不消耗去重位）。

    store 为 dict（示例用；生产替换为持久化）。返回 (store, action)。
    """
    rec = store.setdefault(key, {"attempts": 0, "notified": False, "note": "", "at": None})
    rec["attempts"] += 1
    if stage == "success":
        rec["notified"] = True          # 只在成功路径写去重位
        rec["note"] = note
        rec["at"] = now
        return store, "dedupe-armed"
    return store, "attempt-only"


# --------------------------------------------------------------------------
# 4. 窗口期容忍复检 + 自愈节奏阶梯（治型 1、2）
# --------------------------------------------------------------------------
SELF_HEAL_LADDER = [1, 2, 5, 10, 15]        # 分钟，第 5 轮仍失败才升级上报


def ladder_minutes(attempt):
    """第 attempt 轮自愈的间隔（分钟）；超出阶梯返回最后一档。"""
    if attempt <= 0:
        return SELF_HEAL_LADDER[0]
    return SELF_HEAL_LADDER[min(attempt, len(SELF_HEAL_LADDER)) - 1]


def tolerant_check(probe, recheck_budget=1, sleeper=None):
    """首次失败先复检（窗口期容忍）；复检通过 ⇒ 静默。

    probe() -> (ok: bool, detail: str)，由调用方注入（便于测试与替换）。
    返回 dict：verdict ∈ {ok, healed_silent, failed}
    """
    sleep = sleeper or (lambda _s: None)
    tries = []
    for i in range(recheck_budget + 1):
        ok, detail = probe()
        tries.append({"round": i + 1, "ok": bool(ok), "detail": detail})
        if ok:
            verdict = "ok" if i == 0 else "healed_silent"
            return {"verdict": verdict, "tries": tries, "silent": verdict != "failed"}
        if i < recheck_budget:
            sleep(30)
    return {"verdict": "failed", "tries": tries, "silent": False}


def naive_compare(pre_state, post_state):
    """反面演示（型 3 TOCTOU）：拿"刷新前快照"与"刷新后的结果"比严格相等。

    只要期间有任何正常推进，就恒判失败 —— 这是误报的经典写法，禁止照抄。
    """
    return "stale" if pre_state != post_state else "ok"


def recompute_verdict(current_state, baseline, drift, threshold=3):
    """正面写法（型 3 修法）：按现役状态重算，与"该不该刷新"同口径。"""
    lag = drift if drift is not None else 0
    return "ok" if lag < threshold else "stale", {"current": current_state, "baseline": baseline, "lag": lag}


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------
def selftest():
    cases = []

    def ck(name, got, want):
        cases.append((name, got == want, got, want))

    # 1 窗口感知
    ck("window 夜间不在窗口", cron_matches("*/20 9-21 * * *", dt.datetime(2026, 9, 15, 3, 10))[0], False)
    ck("window 窗口内命中", cron_matches("*/20 9-21 * * *", dt.datetime(2026, 9, 15, 9, 20))[0], True)
    ck("window 窗口内非整点", cron_matches("*/20 9-21 * * *", dt.datetime(2026, 9, 15, 9, 21))[0], False)
    ck("window 每小时", cron_matches("0 * * * *", dt.datetime(2026, 9, 15, 13, 0))[0], True)
    ck("window 月级排班(每月1日)", cron_matches("0 3 1 * *", dt.datetime(2026, 9, 15, 3, 0))[0], False)
    ck("window 非法表达式", cron_matches("*/20 9-21", dt.datetime(2026, 9, 15))[0], None)
    v, rc = window_aware("*/20 9-21 * * *", dt.datetime(2026, 9, 15, 3, 10))
    ck("window 窗口外必须静默(rc=3)", (v, rc), ("out_of_window", 3))

    # 2 错误分类
    ck("classify 超时→网络层", classify_error("connection timed out after 30s")[0], "network")
    ck("classify 429→配额层", classify_error("HTTP 429 Too Many Requests")[0], "quota")
    ck("classify 超时≠限流", classify_error("connection timed out after 30s")[0] != "quota", True)
    ck("classify 排队→负载层", classify_error("request queued, server busy")[0], "load")
    ck("classify 403→权限层", classify_error("403 Forbidden: token expired")[0], "auth")
    ck("classify 磁盘→存储层", classify_error("No space left on device")[0], "disk")
    ck("classify 未知不臆断", classify_error("weird internal state 42")[0], "unknown")

    # 3 去重指纹
    k1 = fingerprint("disk", "root", "usage=97%")
    k2 = fingerprint("disk", "root", "usage=99%")
    k3 = fingerprint("disk", "root", "usage=97%")
    ck("dedupe 同形态同键", k1 == k3, True)
    ck("dedupe 形态不同→不同键", k1 != k2, True)
    store = {}
    record_attempt(store, k1, "failure", now="t1")
    ck("dedupe 失败不消耗去重位", store[k1]["notified"], False)
    record_attempt(store, k1, "failure", now="t2")
    ck("dedupe 失败只累加尝试", store[k1]["attempts"], 2)
    record_attempt(store, k1, "success", note="已投递", now="t3")
    ck("dedupe 成功才置位", store[k1]["notified"], True)

    # 4 窗口期容忍复检
    seq = [False, True]
    r = tolerant_check(lambda: (seq.pop(0), "probe"), recheck_budget=1)
    ck("recheck 首次失败复检通过→静默", r["verdict"], "healed_silent")
    ck("recheck 静默不打扰", r["silent"], True)
    r2 = tolerant_check(lambda: (False, "still down"), recheck_budget=1)
    ck("recheck 复检仍失败→上报", r2["verdict"], "failed")
    r3 = tolerant_check(lambda: (True, "healthy"), recheck_budget=1)
    ck("recheck 首次即成功→ok", r3["verdict"], "ok")
    ck("ladder 阶梯", [ladder_minutes(i) for i in (1, 2, 3, 4, 5, 9)], [1, 2, 5, 10, 15, 15])

    # 5 TOCTOU 正反对照
    ck("TOCTOU 反面写法必报", naive_compare("sha_old", "sha_new"), "stale")
    ck("TOCTOU 正面按现役重算→达标", recompute_verdict("new", "old", drift=0)[0], "ok")
    ck("TOCTOU 正面超阈值→真告警", recompute_verdict("new", "old", drift=9, threshold=3)[0], "stale")

    passed = sum(1 for _, ok, _, _ in cases if ok)
    for name, ok, got, want in cases:
        if not ok:
            print("FAIL %-32s got=%r want=%r" % (name, got, want))
    print("selftest: %d/%d PASS" % (passed, len(cases)))
    return 0 if passed == len(cases) else 1


# --------------------------------------------------------------------------
def _parse_at(s):
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit("无法解析时间：%s（示例 2026-09-15T03:10）" % s)


def main(argv=None):
    p = argparse.ArgumentParser(description="告警判据工具箱")
    p.add_argument("--selftest", action="store_true", help="运行自测")
    sub = p.add_subparsers(dest="cmd")

    w = sub.add_parser("window", help="窗口感知判定")
    w.add_argument("--cron", required=True)
    w.add_argument("--at", required=True)

    c = sub.add_parser("classify", help="错误分类（不混淆根因）")
    c.add_argument("--msg", required=True)

    d = sub.add_parser("dedupe", help="去重指纹")
    d.add_argument("--item", required=True)
    d.add_argument("--form", default="")
    d.add_argument("--category", default="alert")
    d.add_argument("--stage", default="success", choices=["success", "failure"])

    l = sub.add_parser("ladder", help="自愈节奏阶梯")
    l.add_argument("--attempt", type=int, required=True)

    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd == "window":
        v, rc = window_aware(args.cron, _parse_at(args.at))
        print(json.dumps({"verdict": v, "rc": rc,
                          "action": "静默（窗口外，不算停跳）" if rc else "窗口内，允许按超期判据告警"},
                         ensure_ascii=False))
        return rc
    if args.cmd == "classify":
        kind, hint = classify_error(args.msg)
        print(json.dumps({"kind": kind, "hint": hint}, ensure_ascii=False))
        return 0
    if args.cmd == "dedupe":
        store = {}
        key = fingerprint(args.category, args.item, args.form)
        store, action = record_attempt(store, key, args.stage)
        print(json.dumps({"fingerprint": key, "action": action,
                          "dedupe_armed": store[key]["notified"]}, ensure_ascii=False))
        return 0
    if args.cmd == "ladder":
        print(json.dumps({"attempt": args.attempt, "wait_minutes": ladder_minutes(args.attempt)},
                         ensure_ascii=False))
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
