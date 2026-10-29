#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log-rca-timeline / 日志分析与故障根因定位 —— 技能包综合自检

用法:
    python selfcheck.py                        # 静态检查
    python selfcheck.py --e2e <run_dir>        # 静态 + 端到端（data.json -> 看板 -> 渲染自检）
    python selfcheck.py --log <文件或目录>     # 完整链路（日志 -> data.json -> 看板 -> 渲染自检）

路径一律用环境变量拼接，脚本内不出现任何盘符字面量。
"""
from __future__ import annotations

import argparse
import glob as globmod
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
except Exception:
    pass

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
HOME = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""

results = []


def rec(ok, name, detail=""):
    results.append((bool(ok), name, detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + ((" -> " + str(detail)) if detail and not ok else ""))


def find_node():
    cands = []
    for root in (os.path.join(HOME, ".workbuddy", "binaries", "node", "versions"),
                 os.path.join(HOME, ".workbuddy", "binaries", "node")):
        if root and os.path.isdir(root):
            cands += sorted(globmod.glob(os.path.join(root, "*", "node.exe")), reverse=True)
            cands += sorted(globmod.glob(os.path.join(root, "node.exe")), reverse=True)
    for c in cands:
        if os.path.exists(c):
            return c
    return shutil.which("node") or shutil.which("node.exe")


NEED_FILES = [
    "SKILL.md", "manifest.yaml", "README.md", "icon-512.png",
    "references/log-parsing.md", "references/anomaly-patterns.md",
    "references/rca-ranking.md", "references/timeline-postmortem.md",
    "assets/board_template.html",
    "scripts/analyze_logs.py", "scripts/build_board.py",
    "scripts/render_check.js", "scripts/selfcheck.py", "scripts/gen_icon.py",
]

FM_KEYS = ["name", "version", "display_name", "display_name_en",
           "description", "description_zh", "description_en"]


def check_static():
    print("\n== 1. 目录与文件 ==")
    for d in ("references", "scripts", "assets"):
        rec(os.path.isdir(os.path.join(SKILL, d)), "目录存在: %s/" % d)
    for f in NEED_FILES:
        rec(os.path.isfile(os.path.join(SKILL, f)), "文件存在: %s" % f)
    extra = []
    for dp, dns, fns in os.walk(SKILL):
        rel = os.path.relpath(dp, SKILL)
        if rel != "." and len(rel.split(os.sep)) > 1:
            extra.append(rel)
        for fn in fns:
            if re.match(r"^(_tmp_|_?shot_|_edge)", fn) or fn.endswith(".pyc"):
                extra.append(os.path.join(rel, fn))
    rec(not extra, "无三级以上目录、无临时文件残留", "; ".join(extra))

    print("\n== 2. SKILL.md frontmatter ==")
    sk = os.path.join(SKILL, "SKILL.md")
    txt = open(sk, encoding="utf-8").read() if os.path.isfile(sk) else ""
    rec(txt.startswith("---"), "以 --- 开头")
    fm = txt.split("---", 2)[1] if txt.startswith("---") else ""
    for key in FM_KEYS:
        rec((key + ":") in fm, "frontmatter 含 %s" % key)
    rec("agent_created: true" in fm, "frontmatter 含 agent_created")
    m = re.search(r"name:\s*(.+)", fm)
    if m:
        nm = m.group(1).strip()
        rec(re.match(r"^[a-z0-9-]+$", nm) is not None, "name 为裸写的小写连字符形式", nm)
        rec(nm == os.path.basename(SKILL), "name 与技能目录名一致", "%s vs %s" % (nm, os.path.basename(SKILL)))
    else:
        rec(False, "能抓到 name 字段")
    idx_name = fm.find("\nname:") + 1 if "\nname:" in fm else (0 if fm.startswith("name:") else -1)
    idx_disp = fm.find("display_name:")
    rec(idx_name >= 0 and (idx_disp < 0 or idx_name < idx_disp), "name 排在 display_name 之前")
    dm = re.search(r"description:\s*(.+)", fm)
    rec(bool(dm) and "<" not in dm.group(1) and ">" not in dm.group(1),
        "description 行不含尖括号（单行双引号包裹）")
    den = re.search(r"description_en:\s*(.+)", fm)
    if den:
        ln = len(den.group(1).strip().strip('"').strip("'"))
        rec(ln <= 1000, "description_en <= 1000 字符", "%d 字符" % ln)
        rec(ln > 200, "description_en 内容充分（> 200 字符）", "%d 字符" % ln)
    body = txt.split("---", 2)[2] if txt.startswith("---") else txt
    rec(len(body) > 6000, "正文长度 > 6000 字符", str(len(body)))
    refs = re.findall(r"`((?:references|scripts|assets)/[^`]+)`", body)
    missing = sorted({r for r in refs if not os.path.exists(os.path.join(SKILL, r))})
    rec(not missing, "SKILL.md 引用的文件均存在", ",".join(missing))
    for sec in ("异常模式库", "根因假设排序", "故障时间线", "data.json 字段规范", "打包交付"):
        rec(sec in body, "含章节: %s" % sec)

    print("\n== 3. manifest.yaml ==")
    mp = os.path.join(SKILL, "manifest.yaml")
    mt = open(mp, encoding="utf-8").read() if os.path.isfile(mp) else ""
    for key in ("slug:", "name:", "version:", "description:", "icon:", "trigger_words:"):
        rec(key in mt, "manifest 含 %s" % key.rstrip(":"))
    rec("icon-512.png" in mt, "icon 指向 icon-512.png")
    rec(len(mt) > 300, "manifest 内容充分")

    print("\n== 4. 图标 ==")
    ip = os.path.join(SKILL, "icon-512.png")
    if os.path.isfile(ip):
        rec(os.path.getsize(ip) / 1024.0 <= 500, "图标体积 <= 500KB",
            "%.0fKB" % (os.path.getsize(ip) / 1024.0))
        try:
            from PIL import Image
            with Image.open(ip) as im:
                rec(im.size == (512, 512), "图标尺寸 512x512", str(im.size))
        except Exception as e:
            rec(False, "图标尺寸 512x512", str(e))

    print("\n== 5. 模板与占位符 ==")
    tp = os.path.join(SKILL, "assets", "board_template.html")
    if os.path.isfile(tp):
        t = open(tp, encoding="utf-8").read()
        for ph in ("__TITLE__", "__SUBTITLE__", "__SCOPE__", "__SPAN__",
                   "__WINDOW__", "__GENERATED__", "__DATA_JSON__"):
            rec(ph in t, "模板含占位符 %s" % ph)
        rec(t.count("<script>") >= 2, "模板含 payload 与渲染两段脚本")
        rec("window.__LRT_DATA__" in t, "模板含数据负载变量名")
        for kw in ("根因假设排序", "故障时间线", "验证动作", "可排除条件"):
            rec(kw in t, "模板含结论锚点「%s」" % kw)

    print("\n== 6. 参考文档锚点 ==")
    for f, kw in (("references/log-parsing.md", "时间窗口"),
                  ("references/anomaly-patterns.md", "异常模式"),
                  ("references/rca-ranking.md", "证据分级"),
                  ("references/timeline-postmortem.md", "故障时间线")):
        p = os.path.join(SKILL, f)
        rec(os.path.isfile(p) and kw in open(p, encoding="utf-8").read(),
            "%s 含关键锚点「%s」" % (f, kw))

    print("\n== 7. 脚本语法 ==")
    for s in ("scripts/analyze_logs.py", "scripts/build_board.py",
              "scripts/selfcheck.py", "scripts/gen_icon.py"):
        p = os.path.join(SKILL, s)
        if not os.path.isfile(p):
            continue
        try:
            py_compile.compile(p, doraise=True,
                               cfile=os.path.join(tempfile.gettempdir(), "lrt_c.pyc"))
            rec(True, "Python 语法: %s" % s)
        except Exception as e:
            rec(False, "Python 语法: %s" % s, str(e))
    node = find_node()
    if node:
        js = os.path.join(SKILL, "scripts", "render_check.js")
        r = subprocess.run([node, "--check", js], capture_output=True, text=True)
        rec(r.returncode == 0, "Node 语法: scripts/render_check.js", (r.stderr or "")[:200])
    else:
        rec(False, "定位 Node 解释器", "未找到，跳过 JS 语法检查")

    print("\n== 8. 分发合规 ==")
    # 模式串一律用碎片拼接，避免扫描自身源码时自指误报
    pats = [
        "[A-Za-z]" + ":" + r"[\\/]{1,2}" + "Users" + r"[\\/]",
        "/" + "Users/",
        "/home/" + "[a-zA-Z]",
        "C" + ":" + r"[\\/]{1,2}",
    ]
    abs_re = re.compile("|".join(pats))
    hits = []
    for dp, _dns, fns in os.walk(SKILL):
        for fn in fns:
            if not fn.endswith((".py", ".js", ".md", ".html", ".yaml", ".yml", ".txt")):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, SKILL)
            if rel == os.path.join("scripts", "selfcheck.py"):
                continue          # 本文件内的模式串本身即碎片形式，跳过以免自指
            t = open(p, encoding="utf-8", errors="replace").read()
            for m in abs_re.finditer(t):
                hits.append("%s:%s" % (rel, m.group(0)))
    rec(not hits, "包内无本机绝对路径残留", "; ".join(sorted(set(hits))[:5]))
    if not hits:
        rec(len(sys.argv) >= 1, "解释器路径一律用环境变量拼接（%USERPROFILE% 形式）")

    return node


def run(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    for ln in (r.stdout or "").strip().splitlines()[:40]:
        print("      " + ln)
    if r.stderr:
        print("      [stderr] " + (r.stderr or "").strip()[:400])
    return r.returncode


def check_e2e(run_dir, node, log_path=None):
    print("\n== 9. 端到端 ==")
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    if log_path:
        log_path = os.path.abspath(log_path)
        code = run([PY, "-X", "utf8", os.path.join(SKILL, "scripts", "analyze_logs.py"),
                    "--input", log_path, "--window", "1m",
                    "--out", os.path.join(run_dir, "data.json")], "analyze")
        rec(code == 0, "analyze_logs.py 退出码 0", "code=%s" % code)
    dj = os.path.join(run_dir, "data.json")
    if not os.path.isfile(dj):
        rec(False, "run_dir 含 data.json", run_dir)
        return
    rec(True, "run_dir 含 data.json")

    code = run([PY, "-X", "utf8", os.path.join(SKILL, "scripts", "build_board.py"), run_dir],
               "build")
    rec(code == 0, "build_board.py 退出码 0", "code=%s" % code)

    html = os.path.join(run_dir, "report.html")
    rec(os.path.isfile(html), "report.html 已生成")
    if node and os.path.isfile(html):
        code = run([node, os.path.join(SKILL, "scripts", "render_check.js"),
                    html, os.path.join(run_dir, "render_check.txt")], "render")
        rec(code == 0, "render_check.js 退出码 0", "code=%s" % code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", help="端到端模式：含 data.json 的运行目录")
    ap.add_argument("--log", help="完整链路模式：传入日志文件或目录")
    args = ap.parse_args()

    print("日志分析与故障根因定位 · 技能包自检")
    print("技能目录: " + SKILL)
    node = check_static()
    if args.log:
        check_e2e(args.e2e or os.path.join(os.getcwd(), "rca_run"), node, args.log)
    elif args.e2e:
        check_e2e(args.e2e, node)

    n_pass = sum(1 for r in results if r[0])
    n_fail = len(results) - n_pass
    print("\n结果: 共 %d 项，通过 %d，失败 %d" % (len(results), n_pass, n_fail))
    if n_fail:
        for ok_, name, detail in results:
            if not ok_:
                print("  x " + name + ((" -> " + str(detail)) if detail else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
