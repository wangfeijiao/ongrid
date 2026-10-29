#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_cron_status.py — 采集 WorkBuddy 定时任务(automation)运行状态，输出 JSON。

数据来源（均为本机路径，按优先级）：
  1. ~/.workbuddy/workbuddy.db  （SQLite）
       - automations         表：任务定义（name / schedule_type / rrule / status / next_run_at）
       - automation_runs     表：每次执行记录；runs_json 里含 success(bool) / startedAt / finishedAt / output
  2. ~/.workbuddy/logs/automation.log （调度层日志，补漏跑/连续失败时间线）

关键事实（写这个脚本前已实测确认）：
  - automation_runs.status 恒为 'ACCEPTED'、result_success 恒为 1 —— 它们只标记
    「会话被接受/创建」，不能用来判断任务是否真正成功。
  - 真正的成败在 automation_runs.runs_json 里，它是一个 JSON 数组，每条含
    success / startedAt / finishedAt / output 字段。
  - 调度层失败（如「连接器未连接」）会写入 automation.log，形如：
        run failed for <id>: [UNKNOWN] <原因>
        run finished: ... success=false ...

用法：
  python collect_cron_status.py [--db PATH] [--log PATH] [--out PATH] [--tail N]

默认输出到 stdout（JSON），可 --out 写文件。--tail 控制「最近输出」截取的字符数。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def _ms_to_iso(ms):
    """毫秒时间戳 -> ISO8601 本地时间字符串；None -> None。"""
    if not ms:
        return None
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return None


def _utc_ms_from_isolog(line):
    """从 automation.log 一行提取 UTC 时间戳（毫秒）。开头形如 2026-10-13T11:51:15.058Z。"""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z", line)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def load_automations(db_path):
    """读取 automations 表，返回 {automation_id: dict}。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, schedule_type, status, rrule, scheduled_at, "
        "next_run_at, last_run_at, created_at, updated_at, deleted_at "
        "FROM automations"
    ).fetchall()
    conn.close()

    out = {}
    for r in rows:
        r = dict(r)
        if r.get("deleted_at"):
            continue  # 软删除的任务不展示
        aid = r["id"]
        out[aid] = {
            "id": aid,
            "name": r.get("name") or "(未命名)",
            "schedule_type": r.get("schedule_type") or "",
            "status": r.get("status") or "",
            "rrule": r.get("rrule") or "",
            "scheduled_at": r.get("scheduled_at") or "",
            "next_run_at": _ms_to_iso(r.get("next_run_at")),
            "last_run_at": _ms_to_iso(r.get("last_run_at")),
            "created_at": _ms_to_iso(r.get("created_at")),
        }
    return out


def load_runs(db_path):
    """读取 automation_runs 表，解析 runs_json，返回 {automation_id: [run,...]}。

    每条 run: { success, started_at, finished_at, output }
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT automation_id, runs_json, thread_title, metadata_json, created_at "
        "FROM automation_runs ORDER BY created_at ASC"
    ).fetchall()
    conn.close()

    runs_by_aid = {}
    for r in rows:
        r = dict(r)
        aid = r["automation_id"]
        runs_by_aid.setdefault(aid, [])
        raw = r.get("runs_json")
        parsed = []
        if raw:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = []
        if not isinstance(parsed, list):
            parsed = []
        if not parsed:
            # 兜底：runs_json 为空时，用 thread_title + metadata_json 构造一条
            meta = {}
            try:
                meta = json.loads(r.get("metadata_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            parsed = [{
                "success": r.get("result_success") == 1,
                "startedAt": r.get("created_at"),
                "finishedAt": None,
                "output": r.get("thread_title") or "",
            }]
        for item in parsed:
            runs_by_aid[aid].append({
                "success": bool(item.get("success", False)),
                "started_at": _ms_to_iso(item.get("startedAt")),
                "finished_at": _ms_to_iso(item.get("finishedAt")),
                "output": item.get("output") or "",
            })
    return runs_by_aid


def load_scheduler_events(log_path):
    """解析 automation.log，返回 {automation_id: [ {ts, ok, reason} ...]} 按时间升序。

    识别：
      run finished: id=<id> ... success=false  -> 失败
      run failed for <id>: [UNKNOWN] <reason>  -> 失败 + 原因
      run finished: id=<id> ... success=true   -> 成功
    """
    events = {}
    if not os.path.exists(log_path):
        return events
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ts = _utc_ms_from_isolog(line)
            if ts is None:
                continue

            # 失败原因行
            mf = re.search(r"run failed for ([0-9a-f-]{36}):\s*\[UNKNOWN\]\s*(.*)$", line)
            if mf:
                aid = mf.group(1)
                events.setdefault(aid, []).append({
                    "ts": ts, "ok": False, "reason": mf.group(2).strip()
                })
                continue

            # run finished 行（success 标记；不填 reason，真实原因来自 run failed for 行）
            mfin = re.search(r"run finished: id=([0-9a-f-]{36}).*success=(true|false)", line)
            if mfin:
                aid = mfin.group(1)
                ok = mfin.group(2) == "true"
                events.setdefault(aid, []).append({
                    "ts": ts, "ok": ok, "reason": ""
                })

    for aid in events:
        events[aid].sort(key=lambda e: e["ts"])
    return events


def compute_consecutive_failures(aid, runs, events):
    """计算「连续失败(漏跑)次数」，基于调度层 events 时间线。

    权威信号是 automation.log 里的调度结果：
      run finished ... success=false  /  run failed for <id>
    每次这样的记录 = 一次「调度失败/漏跑」。从最近一次往前数连续的失败次数。

    注意：runs_json 里的 success=true（会话成功产出）不能打断这个计数，
    因为「调度失败」和「会话成功」是不同维度——一次调度失败就是一次漏跑。
    """
    evs = events.get(aid, [])
    if not evs:
        return 0
    cnt = 0
    for e in reversed(evs):
        if e["ok"]:
            break
        cnt += 1
    return cnt


def collect(args):
    db_path = os.path.abspath(args.db)
    log_path = os.path.abspath(args.log)

    automations = load_automations(db_path)
    runs_by_aid = load_runs(db_path)
    events = load_scheduler_events(log_path)

    tasks = []
    for aid, meta in automations.items():
        runs = runs_by_aid.get(aid, [])

        # 最近一次执行（取 started_at 最大者）
        latest = None
        for r in runs:
            if r["started_at"] and (latest is None or r["started_at"] > latest["started_at"]):
                latest = r

        latest_output = (latest or {}).get("output", "")
        # 截取「最近输出」：保留开头前 N 行，方便一眼看结论
        tail_lines = latest_output.strip().splitlines()
        output_tail = "\n".join(tail_lines[: args.tail]) if tail_lines else ""

        # 最近一次调度结果（权威）：log 里该任务最近一条 run finished 的 success
        evs = events.get(aid, [])
        latest_scheduler = None
        for e in evs:
            latest_scheduler = e  # evs 已按时间升序，最后一条即最近
        if latest_scheduler is not None:
            latest_ok = latest_scheduler["ok"]
        else:
            # 无调度层记录时，退回 runs_json 最近一次 success
            latest_ok = (latest or {}).get("success")

        # 失败原因：最近一条失败事件的 reason
        fail_reason = ""
        for e in reversed(evs):
            if not e["ok"] and e.get("reason"):
                fail_reason = e["reason"]
                break

        consecutive = compute_consecutive_failures(aid, runs, events)

        tasks.append({
            "id": aid,
            "name": meta["name"],
            "schedule_type": meta["schedule_type"],
            "status": meta["status"],
            "rrule": meta["rrule"],
            "scheduled_at": meta["scheduled_at"],
            "next_run_at": meta["next_run_at"],
            "last_run_at": meta["last_run_at"],
            "total_runs": len(runs),
            "latest_started_at": (latest or {}).get("started_at"),
            "latest_finished_at": (latest or {}).get("finished_at"),
            "latest_success": latest_ok,
            "session_success": (latest or {}).get("success"),
            "fail_reason": fail_reason,
            "consecutive_failures": consecutive,
            "latest_output": output_tail,
        })

    # 按「连续失败次数」降序、再按名称排序：问题任务排前面
    tasks.sort(key=lambda t: (-t["consecutive_failures"], t["name"]))

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": db_path,
        "log_path": log_path,
        "task_count": len(tasks),
        "tasks": tasks,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="采集 WorkBuddy 定时任务运行状态")
    parser.add_argument("--db", default=os.path.join(HOME, ".workbuddy", "workbuddy.db"))
    parser.add_argument("--log", default=os.path.join(HOME, ".workbuddy", "logs", "automation.log"))
    parser.add_argument("--out", default=None, help="输出 JSON 文件路径（默认 stdout）")
    parser.add_argument("--tail", type=int, default=10, help="最近输出保留前 N 行（默认 10）")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(json.dumps({"error": f"数据库不存在: {args.db}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    result = collect(args)
    text = json.dumps(result, ensure_ascii=False, indent=2)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写入 {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
