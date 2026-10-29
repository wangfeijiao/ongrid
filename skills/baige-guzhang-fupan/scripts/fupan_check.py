#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""故障复盘 JSON 硬校验器。

用法（cmd）：
    python scripts\\fupan_check.py fupan.json
    python scripts\\fupan_check.py --example-good   生成示例（校验通过）
    python scripts\\fupan_check.py --example-bad    生成示例（校验失败）

fupan.json 结构：
{
  "title": "xxx 服务故障复盘",
  "incident_start": "2026-10-04 14:20:00",
  "incident_end": "2026-10-04 15:05:00",
  "severity": "P2",
  "timeline": [
    {"time": "2026-10-04 14:20:00", "event": "发布系统上线 v2.3.1", "source": "发布记录"},
    {"time": "2026-10-04 14:35:00", "event": "错误率告警触发", "source": "监控（未核实阈值）"}
  ],
  "why5": ["现象：...", "为什么1：...", "为什么2：...", "为什么3：...", "为什么4：...", "为什么5：..."],
  "contributing_factors": ["因素1：...", "因素2：..."],
  "action_items": [
    {"item": "发布系统增加配置格式校验", "owner": "运维值班",
     "due": "2026-10-15", "done_check": "恶意值测试用例通过", "class": "防复发"}
  ]
}

硬校验规则（任一条失败则 exit 1）：
1. 时间线必须按时间递增排列；时间格式必须可解析
2. 每条改进项必须有非空负责人 owner 与截止时间 due（due 必须是合法日期）
3. why5 链条至少 4 层；促成因素 contributing_factors 至少 2 条
4. 根因/因素文本不许出现甩锅词：人为失误、操作失误、粗心、大意、疏忽、责任人xxx的错
   （甩锅词检测覆盖 why5 + contributing_factors 全文）
校验通过打印 OK 并 exit 0。
"""

import json
import re
import sys
from datetime import datetime

BLAME_WORDS = ["人为失误", "操作失误", "粗心", "大意", "疏忽", "的错", "背锅", "甩锅"]

TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
]


def parse_time(s):
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def parse_date(s):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def check(data):
    errors = []
    if not isinstance(data, dict):
        return ["顶层必须是 JSON 对象"]

    # 1. 时间线递增
    timeline = data.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        errors.append("timeline 必须是非空数组")
    else:
        prev = None
        for i, entry in enumerate(timeline):
            t = entry.get("time") if isinstance(entry, dict) else None
            dt = parse_time(t)
            if dt is None:
                errors.append(f"timeline[{i}] 时间无法解析: {t!r}")
                continue
            if prev is not None and dt < prev:
                errors.append(
                    f"timeline[{i}] 时间倒序: {t} 早于上一条"
                )
            prev = dt
            if not entry.get("event"):
                errors.append(f"timeline[{i}] 缺少 event 描述")
            if not entry.get("source"):
                errors.append(f"timeline[{i}] 缺少 source 证据来源")

    # 2. 改进项四要素
    items = data.get("action_items")
    if not isinstance(items, list) or not items:
        errors.append("action_items 必须是非空数组")
    elif len(items) > 8:
        errors.append(f"改进项 {len(items)} 条超过 8 条上限，请聚焦最有杠杆的")
    else:
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errors.append(f"action_items[{i}] 必须是对象")
                continue
            if not (it.get("owner") or "").strip():
                errors.append(f"action_items[{i}] 缺少 owner 负责人")
            due = (it.get("due") or "").strip()
            if not due:
                errors.append(f"action_items[{i}] 缺少 due 截止时间")
            elif parse_date(due) is None:
                errors.append(f"action_items[{i}] due 日期格式非法: {due!r}")
            if not (it.get("done_check") or "").strip():
                errors.append(f"action_items[{i}] 缺少 done_check 验收标准")
            cls = (it.get("class") or "").strip()
            if cls and cls not in ("防复发", "早发现", "快止血"):
                errors.append(f"action_items[{i}] class 必须是 防复发/早发现/快止血 之一")

    # 3. 5Why 链条与促成因素数量
    why5 = data.get("why5")
    if not isinstance(why5, list) or len(why5) < 4:
        errors.append("why5 链条至少 4 层（含现象）")
    factors = data.get("contributing_factors")
    if not isinstance(factors, list) or len(factors) < 2:
        errors.append("contributing_factors 促成因素至少 2 条")

    # 4. 甩锅词检测
    blame_text = ""
    for key in ("why5", "contributing_factors"):
        val = data.get(key)
        if isinstance(val, list):
            blame_text += "\n".join(str(x) for x in val)
    for w in BLAME_WORDS:
        if w in blame_text:
            errors.append(f"根因文本含甩锅词「{w}」：请追问系统与流程原因，不归咎个人")

    return errors


def example_good():
    return {
        "title": "订单服务故障复盘",
        "incident_start": "2026-10-04 14:20:00",
        "incident_end": "2026-10-04 15:05:00",
        "severity": "P2",
        "timeline": [
            {"time": "2026-10-04 14:20:00", "event": "发布系统上线 v2.3.1", "source": "发布记录"},
            {"time": "2026-10-04 14:35:00", "event": "错误率告警触发", "source": "监控（未核实阈值）"},
            {"time": "2026-10-04 14:42:00", "event": "值班工程师开始回滚", "source": "值班记录"},
            {"time": "2026-10-04 15:05:00", "event": "错误率恢复正常，故障结束", "source": "监控"},
        ],
        "why5": [
            "现象：订单服务错误率突增至 40%，持续 45 分钟",
            "为什么1：v2.3.1 引入的配置字段格式错误，导致解析失败",
            "为什么2：发布流水线对该字段没有格式校验",
            "为什么3：配置 schema 变更时未同步更新校验规则",
            "为什么4：缺少配置变更的自动化回归门禁",
        ],
        "contributing_factors": [
            "因素1：配置校验规则与 schema 演进脱节，无自动化同步机制",
            "因素2：错误配置 30 秒内全量生效，无灰度/分批发布兜底",
            "因素3：告警阈值基于历史均值，未覆盖突增场景，发现滞后 15 分钟",
        ],
        "action_items": [
            {"item": "发布系统对配置字段增加格式校验", "owner": "运维值班",
             "due": "2026-10-15", "done_check": "恶意值测试用例通过", "class": "防复发"},
            {"item": "配置发布改为分批灰度", "owner": "架构组",
             "due": "2026-10-25", "done_check": "灰度发布流程文档评审通过", "class": "快止血"},
            {"item": "错误率告警阈值按 P99 重设", "owner": "SRE 值班",
             "due": "2026-10-20", "done_check": "灰度验证通过", "class": "早发现"},
        ],
    }


def example_bad():
    bad = example_good()
    bad["timeline"][2], bad["timeline"][1] = bad["timeline"][1], bad["timeline"][2]  # 制造倒序
    bad["action_items"][0]["owner"] = ""          # 制造缺负责人
    bad["action_items"][1]["due"] = ""             # 制造缺截止时间
    bad["why5"][1] = "为什么1：运维人员操作失误敲错了配置"  # 制造甩锅词
    bad["contributing_factors"] = ["因素1：人为失误"]  # 制造数量不足+甩锅词
    return bad


def main(argv):
    if "--example-good" in argv:
        print(json.dumps(example_good(), ensure_ascii=False, indent=2))
        return 0
    if "--example-bad" in argv:
        print(json.dumps(example_bad(), ensure_ascii=False, indent=2))
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"文件不存在: {argv[1]}")
        return 1
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}")
        return 1
    errors = check(data)
    if errors:
        print(f"校验失败（{len(errors)} 项）:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK：时间线递增、改进项四要素齐全、无甩锅词，可以渲染报告。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
