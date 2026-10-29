#!/usr/bin/env python3
"""grafana_report.py — dev-018 grafana-queries：告警快照 / dashboard / 数据源健康（只读）

用法:
  python grafana_report.py --alerts --json                  # 在线：告警规则状态快照
  python grafana_report.py --dashboards [--query text] --json
  python grafana_report.py --dashboard <uid> --json         # 面板 JSON 摘要
  python grafana_report.py --datasources --json             # 数据源健康（凭据字段剔除）
  python grafana_report.py --health --json                  # /api/health
离线模式（解析已保存的 API 响应，供无 Grafana 环境测试）:
  python grafana_report.py --alerts --input alerts.json --json
      # input = GET /api/prometheus/grafana/api/v1/rules 响应（含实时 state）
  python grafana_report.py --provisioned --provisioned-input rules.json --json
      # = GET /api/v1/provisioned/alert-rules 响应（仅定义，无实时状态）
  python grafana_report.py --dashboards --dashboards-input search.json --json
  python grafana_report.py --datasources --datasources-input ds.json --json

环境: GRAFANA_URL + GRAFANA_TOKEN（viewer 即可）。退出码: 0 成功 | 1 可重试 | 2 需人工。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 20
# 数据源响应可能携带敏感字段，输出只保留以下白名单字段
DATASOURCE_FIELDS = ("name", "type", "uid", "access", "isDefault")


class UserError(Exception):
    def __init__(self, code, message, hints, exit_code):
        super().__init__(message)
        self.code, self.hints, self.exit_code = code, hints, exit_code


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(exc):
    emit({"ok": False,
          "error": {"code": exc.code, "message": str(exc), "hints": exc.hints}})
    return exc.exit_code


def load_json_file(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise UserError("INVALID_INPUT", f"{what} 文件不存在: {path}",
                        ["确认路径正确", "或去掉该参数走在线模式"], 2)
    except json.JSONDecodeError as exc:
        raise UserError("PARSE_ERROR", f"{what} 不是合法 JSON: {exc}",
                        ["该文件应是 Grafana API 的原始 JSON 响应"], 2)


def api_get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "grafana-queries-skill/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise UserError("AUTH_FAILED", "Grafana API 返回 401（token 无效或过期）",
                            ["在 Grafana 服务账号里重新生成 viewer token"], 1)
        if exc.code == 403:
            raise UserError("FORBIDDEN", "Grafana API 返回 403（权限不足）",
                            ["确认 token 角色 viewer 及以上、有目标 folder 的读权限"], 1)
        if exc.code == 429:
            raise UserError("RATE_LIMITED", "Grafana API 限流（429）",
                            ["等待后重试一次，不要循环重试"], 1)
        if exc.code == 404:
            raise UserError("NOT_FOUND", f"Grafana API 返回 404: {url.split('?')[0]}",
                            ["Grafana 版本较低可能没有该端点，改用 --provisioned 离线核对",
                             "确认 uid/slug 拼写"], 2)
        raise UserError("API_ERROR", f"Grafana API HTTP {exc.code}",
                        ["将错误码报告用户"], 1)
    except urllib.error.URLError as exc:
        raise UserError("NETWORK", f"无法访问 Grafana: {exc.reason}",
                        ["检查 GRAFANA_URL 与网络/VPN 后重试一次"], 1)
    except TimeoutError:
        raise UserError("TIMEOUT", f"Grafana 请求超时（{TIMEOUT}s）",
                        ["检查网络后重试一次"], 1)


# ---------- 告警快照 ----------

STATE_MAP = {"firing": "firing", "alerting": "firing", "pending": "pending",
             "normal": "normal", "inactive": "normal", "ok": "normal"}


def slim_rule(rule, group_name, folder):
    state = STATE_MAP.get(str(rule.get("state", "")).lower(), "other")
    return {
        "rule": rule.get("name", ""),
        "uid": rule.get("uid"),
        "group": group_name,
        "folder": folder,
        "state": state,
        "raw_state": rule.get("state"),
        "health": rule.get("health", ""),
        "last_evaluation": rule.get("lastEvaluation"),
        "labels": rule.get("labels") or {},
        "annotations": rule.get("annotations") or {},
    }


def alerts_snapshot(ruler_payload):
    """解析 /api/prometheus/grafana/api/v1/rules 响应（含实时 state）。"""
    groups = (ruler_payload.get("data") or {}).get("groups") if isinstance(ruler_payload, dict) else None
    if groups is None and isinstance(ruler_payload, dict) and "groups" in ruler_payload:
        groups = ruler_payload["groups"]
    if not isinstance(groups, list):
        raise UserError("PARSE_ERROR", "输入不含 data.groups（不是 ruler API 响应）",
                        ["告警实时状态用 GET /api/prometheus/grafana/api/v1/rules",
                         "仅定义列表用 --provisioned --provisioned-input"], 2)
    rules = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        for r in g.get("rules") or []:
            if isinstance(r, dict):
                rules.append(slim_rule(r, g.get("name", ""), g.get("file", "")))
    return assemble_alerts(rules, "ruler")


def provisioned_snapshot(payload):
    """解析 /api/v1/provisioned/alert-rules 响应（仅定义，无实时状态）。"""
    items = payload if isinstance(payload, list) else payload.get("rules")
    if not isinstance(items, list):
        raise UserError("PARSE_ERROR", "输入应为规则定义数组",
                        ["该文件应是 GET /api/v1/provisioned/alert-rules 响应"], 2)
    rules = [{"rule": r.get("name", ""), "uid": r.get("uid"), "group": r.get("ruleGroup", ""),
              "folder": r.get("folderUid", ""), "state": None, "raw_state": None,
              "health": None, "last_evaluation": None,
              "labels": r.get("labels") or {}, "annotations": r.get("annotations") or {},
              "pause": bool(r.get("ruleGroupPaused") or r.get("paused"))}
             for r in items if isinstance(r, dict)]
    out = assemble_alerts(rules, "provisioned")
    out["rules"] = sorted(rules, key=lambda r: (r["group"], r["rule"]))
    out["state_note"] = ("provisioned 响应只含规则定义，无实时 state；"
                         "实时状态用 GET /api/prometheus/grafana/api/v1/rules")
    return out


def assemble_alerts(rules, source):
    firing = sorted([r for r in rules if r["state"] == "firing"],
                    key=lambda r: (r["group"], r["rule"]))
    pending = sorted([r for r in rules if r["state"] == "pending"],
                     key=lambda r: (r["group"], r["rule"]))
    errors = sorted([r for r in rules if (r.get("health") or "") == "error"],
                    key=lambda r: (r["group"], r["rule"]))
    counts = {"total_rules": len(rules), "firing": len(firing), "pending": len(pending),
              "normal": sum(1 for r in rules if r["state"] == "normal"),
              "health_error": len(errors),
              "undefined_state": sum(1 for r in rules if r["state"] is None),
              "other_state": sum(1 for r in rules if r["state"] == "other")}
    return {"source": source, "counts": counts, "firing": firing,
            "pending": pending, "health_error_rules": errors}


# ---------- dashboard / 数据源 / health ----------

def dashboards_snapshot(payload):
    if not isinstance(payload, list):
        raise UserError("PARSE_ERROR", "输入应为 /api/search 响应数组", [], 2)
    items = [{"uid": d.get("uid"), "title": d.get("title", ""), "url": d.get("url", ""),
              "type": d.get("type", ""), "folder": d.get("folderTitle", "")}
             for d in payload if isinstance(d, dict)]
    return {"total": len(items), "dashboards": items}


def dashboard_panels(payload):
    dash = (payload or {}).get("dashboard") if isinstance(payload, dict) else None
    if not isinstance(dash, dict):
        raise UserError("PARSE_ERROR", "输入应为 /api/dashboards/uid/{uid} 响应",
                        ["该文件应含 dashboard.panels"], 2)
    panels = []
    for p in dash.get("panels") or []:
        if isinstance(p, dict):
            ds = p.get("datasource")
            panels.append({"id": p.get("id"), "title": p.get("title", ""),
                           "type": p.get("type", ""),
                           "datasource": ds.get("type") if isinstance(ds, dict) else ds})
    return {"uid": dash.get("uid"), "title": dash.get("title", ""),
            "panels_total": len(panels), "panels": panels}


def datasources_snapshot(payload):
    if not isinstance(payload, list):
        raise UserError("PARSE_ERROR", "输入应为 /api/datasources 响应数组", [], 2)
    items = [{k: d.get(k) for k in DATASOURCE_FIELDS}
             for d in payload if isinstance(d, dict)]
    return {"total": len(items), "datasources": items,
            "security_note": "已剔除 database/user/password/secureJsonData 等字段，"
                             "不要直接导出原始 /api/datasources 响应"}


def main():
    ap = argparse.ArgumentParser(
        description="Grafana 只读查询：告警快照/dashboard/数据源健康（help 即真相：https://grafana.com/docs/grafana/latest/developers/http_api/）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--alerts", action="store_true", help="告警规则实时状态快照（默认模式）")
    mode.add_argument("--provisioned", action="store_true", help="告警规则定义（无实时状态）")
    mode.add_argument("--dashboards", action="store_true", help="dashboard 列表（/api/search）")
    mode.add_argument("--dashboard", metavar="UID", help="单个 dashboard 的面板摘要")
    mode.add_argument("--datasources", action="store_true", help="数据源清单（凭据字段剔除）")
    mode.add_argument("--health", action="store_true", help="Grafana /api/health")
    ap.add_argument("--query", default="", help="dashboard 搜索词（配 --dashboards）")
    ap.add_argument("--url", help="Grafana 地址（默认取 GRAFANA_URL）")
    ap.add_argument("--input", help="离线：ruler API 响应（配 --alerts）")
    ap.add_argument("--provisioned-input", help="离线：provisioned alert-rules 响应（配 --provisioned）")
    ap.add_argument("--dashboards-input", help="离线：/api/search 响应（配 --dashboards）")
    ap.add_argument("--dashboard-input", help="离线：/api/dashboards/uid/{uid} 响应（配 --dashboard）")
    ap.add_argument("--datasources-input", help="离线：/api/datasources 响应（配 --datasources）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 信封")
    args = ap.parse_args()

    try:
        mode_used = (args.alerts or args.provisioned or args.dashboards or args.dashboard
                     or args.datasources or args.health or args.input or args.provisioned_input
                     or args.dashboards_input or args.dashboard_input or args.datasources_input)
        if not mode_used:
            args.alerts = True  # 默认模式：告警快照
        data, cmd = None, None
        if args.input or args.provisioned_input or args.dashboards_input \
                or args.dashboard_input or args.datasources_input:
            if args.input:
                data = alerts_snapshot(load_json_file(args.input, "input"))
            elif args.provisioned_input:
                data = provisioned_snapshot(load_json_file(args.provisioned_input, "provisioned-input"))
            elif args.dashboards_input:
                data = dashboards_snapshot(load_json_file(args.dashboards_input, "dashboards-input"))
            elif args.dashboard_input:
                data = dashboard_panels(load_json_file(args.dashboard_input, "dashboard-input"))
            else:
                data = datasources_snapshot(load_json_file(args.datasources_input, "datasources-input"))
        else:
            base = (args.url or os.environ.get("GRAFANA_URL", "")).rstrip("/")
            token = os.environ.get("GRAFANA_TOKEN", "")
            if not base or not token:
                raise UserError("CONFIG_MISSING",
                                "缺少 GRAFANA_URL 或 GRAFANA_TOKEN 环境变量",
                                ["bash: export GRAFANA_URL=https://grafana.example.com",
                                 "bash: export GRAFANA_TOKEN=<your-token>（viewer 即可）",
                                 "token 不要写进命令行参数或文件"], 2)
            if args.dashboard:
                url = f"{base}/api/dashboards/uid/{args.dashboard}"
                data = dashboard_panels(api_get(url, token))
            elif args.dashboards:
                url = f"{base}/api/search?{('query=' + args.query) if args.query else 'type=dash-db'}"
                data = dashboards_snapshot(api_get(url, token))
            elif args.datasources:
                url = f"{base}/api/datasources"
                data = datasources_snapshot(api_get(url, token))
            elif args.health:
                url = f"{base}/api/health"
                raw = api_get(url, token)
                data = {"version": raw.get("version"), "database": raw.get("database"),
                        "commit": raw.get("commit")}
            else:  # --alerts 默认
                url = f"{base}/api/prometheus/grafana/api/v1/rules"
                data = alerts_snapshot(api_get(url, token))
            cmd = f"GET {url}"

        if args.json:
            emit({"ok": True, "cmd": cmd, "data": data})
        else:
            c = data.get("counts")
            if c:
                print(f"rules: {c['total_rules']} ｜ firing={c['firing']} pending={c['pending']} "
                      f"normal={c['normal']} health_error={c['health_error']}")
                for r in data["firing"]:
                    print(f"  [FIRING] {r['group']}/{r['rule']} "
                          f"({(r['annotations'] or {}).get('summary', '')[:60]})")
            elif "dashboards" in data:
                print(f"dashboards: {data['total']}")
                for d in data["dashboards"]:
                    print(f"  {d['uid']}  {d['title']}")
            elif "datasources" in data:
                print(f"datasources: {data['total']}（凭据字段已剔除）")
                for d in data["datasources"]:
                    print(f"  {d.get('name')} [{d.get('type')}] default={d.get('isDefault')}")
            elif "panels" in data:
                print(f"dashboard {data['title']}: {data['panels_total']} panels")
            else:
                print(f"health: {data}")
        return 0
    except UserError as exc:
        return fail(exc)


if __name__ == "__main__":
    sys.exit(main())
