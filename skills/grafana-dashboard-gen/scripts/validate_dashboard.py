#!/usr/bin/env python3
"""校验 Grafana Dashboard JSON 结构完整性"""

import json
import sys
from pathlib import Path


def validate_dashboard(filepath: str) -> list[str]:
    """校验 Dashboard JSON，返回错误列表（空列表表示通过）"""
    errors = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [f"JSON 解析失败: {e}"]

    # 检查顶层结构
    if "dashboard" not in data:
        errors.append("缺少顶层 'dashboard' 对象")
        return errors

    db = data["dashboard"]

    # 必填字段检查
    required_fields = ["title", "panels"]
    for field in required_fields:
        if field not in db:
            errors.append(f"Dashboard 缺少必填字段: '{field}'")

    if "title" in db and not db["title"]:
        errors.append("Dashboard 标题为空")

    # 检查 panels
    if "panels" in db:
        panel_ids = []
        for i, panel in enumerate(db["panels"]):
            # Panel id 唯一性
            if "id" in panel:
                if panel["id"] in panel_ids:
                    errors.append(f"Panel #{i} ('{panel.get('title', '?')}') 的 id={panel['id']} 与前面重复")
                panel_ids.append(panel["id"])
            else:
                errors.append(f"Panel #{i} ('{panel.get('title', '?')}') 缺少 id 字段")

            # Panel type 检查
            if "type" not in panel:
                errors.append(f"Panel #{i} ('{panel.get('title', '?')}') 缺少 type 字段")

            # gridPos 检查
            if "gridPos" in panel:
                gp = panel["gridPos"]
                for key in ["h", "w", "x", "y"]:
                    if key not in gp:
                        errors.append(f"Panel '#{i}' ('{panel.get('title', '?')}') gridPos 缺少 '{key}'")
                if "w" in gp and (gp["w"] < 1 or gp["w"] > 24):
                    errors.append(f"Panel #{i} ('{panel.get('title', '?')}') gridPos.w={gp['w']} 超出范围 (1-24)")
                if "x" in gp and (gp["x"] < 0 or gp["x"] + gp.get("w", 0) > 24):
                    errors.append(f"Panel #{i} ('{panel.get('title', '?')}') gridPos.x={gp['x']} + w={gp.get('w',0)} 超出网格宽度 24")
            elif panel.get("type") != "row":
                errors.append(f"Panel #{i} ('{panel.get('title', '?')}') 缺少 gridPos 字段")

            # Row 面板检查
            if panel.get("type") == "row" and "collapsed" in panel and panel["collapsed"]:
                if "panels" in panel:
                    for j, sub_panel in enumerate(panel["panels"]):
                        if "id" in sub_panel and sub_panel["id"] in panel_ids:
                            errors.append(f"Row '{panel.get('title', '?')}' 内部 Panel #{j} id={sub_panel['id']} 重复")
                        if "id" in sub_panel:
                            panel_ids.append(sub_panel["id"])

            # 非 Row 面板应有 targets 或 datasource
            if panel.get("type") not in ("row", "text") and "targets" not in panel and "datasource" not in panel:
                errors.append(f"Panel #{i} ('{panel.get('title', '?')}') 缺少 targets 或 datasource")

    # 检查 templating
    if "templating" in db and "list" in db["templating"]:
        var_names = []
        for var in db["templating"]["list"]:
            if "name" not in var:
                errors.append(f"变量缺少 name 字段: {var}")
            elif var["name"] in var_names:
                errors.append(f"变量名 '{var['name']}' 重复")
            else:
                var_names.append(var["name"])

    return errors


def main():
    if len(sys.argv) < 2:
        print("用法: python validate_dashboard.py <dashboard.json> [<dashboard2.json> ...]")
        sys.exit(1)

    all_passed = True
    for filepath in sys.argv[1:]:
        path = Path(filepath)
        if not path.exists():
            print(f"❌ 文件不存在: {filepath}")
            all_passed = False
            continue

        errors = validate_dashboard(filepath)
        if errors:
            print(f"❌ {filepath} 校验失败:")
            for err in errors:
                print(f"   - {err}")
            all_passed = False
        else:
            panels_count = 0
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            panels_count = len(data.get("dashboard", {}).get("panels", []))
            vars_count = len(data.get("dashboard", {}).get("templating", {}).get("list", []))
            print(f"✅ {filepath} 校验通过 ({panels_count} 个面板, {vars_count} 个变量)")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
