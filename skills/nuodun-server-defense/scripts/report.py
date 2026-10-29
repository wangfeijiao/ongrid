#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report.py — 生成自包含的 HTML 体检报告。

约束：
  * 单文件、零外部依赖（不引 CDN），断网、内网、离线交付都能打开。
  * 浅色主题、可打印、可长时间阅读；关键是"能照着修"，所以证据/动作/验收/回滚
    四件事在每一条结论里都要齐。
  * 未覆盖清单必须显著呈现 —— 只说"发现了什么"不说"没查到什么"，报告就是误导。
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from common import (
    CATEGORIES,
    OUTPUT_DIR,
    SEV_COLOR,
    SEV_LABEL,
    ensure_output_dir,
    now_stamp,
)

CSS = """
:root{
  --bg:#f6f7f9; --panel:#ffffff; --line:#e5e7eb; --text:#111827; --muted:#6b7280;
  --critical:#b91c1c; --high:#dc2626; --medium:#d97706; --low:#2563eb; --info:#64748b;
  --ok:#059669; --accent:#0f766e;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.10);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
  "Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.3px}
h2{font-size:19px;margin:34px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--line)}
h3{font-size:16px;margin:22px 0 10px}
.sub{color:var(--muted);font-size:13px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;box-shadow:var(--shadow);margin-bottom:16px}
.grid{display:grid;gap:14px}
.g-hero{grid-template-columns:220px 1fr}
.g-sev{grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}
.g-two{grid-template-columns:1fr 1fr}
@media(max-width:760px){.g-hero,.g-two{grid-template-columns:1fr}}
.score{display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:20px;box-shadow:var(--shadow)}
.score .num{font-size:56px;font-weight:700;line-height:1;letter-spacing:-2px}
.score .grade{margin-top:8px;font-size:13px;color:var(--muted);letter-spacing:2px}
.score .bar{width:100%;height:8px;border-radius:4px;background:#eef0f3;margin-top:14px;overflow:hidden}
.score .bar > i{display:block;height:100%;border-radius:4px}
.kv{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;font-size:14px}
.kv dt{color:var(--muted)}.kv dd{margin:0;word-break:break-all}
.sev{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.sev .dot{width:10px;height:10px;border-radius:50%;flex:none}
.sev .n{font-size:22px;font-weight:700;line-height:1}
.sev .t{font-size:12px;color:var(--muted)}
.f{padding:14px 16px;border-right:none;border-top:none;border-bottom:none;
  background:var(--panel);border:1px solid var(--line);border-left-width:4px;
  border-radius:10px;box-shadow:var(--shadow);margin-bottom:10px}
.f .hd{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.f .t{font-weight:600;font-size:15px}
.tag{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
  color:var(--muted);background:#fafbfc;white-space:nowrap}
.tag.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.ev{margin:10px 0 0;padding:10px 12px;background:#f8fafc;border:1px dashed var(--line);
  border-radius:8px;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:pre-wrap;word-break:break-all;color:#334155;max-height:220px;overflow:auto}
.sec{margin-top:10px;font-size:14px}
.sec b{display:block;color:var(--muted);font-size:12px;font-weight:600;letter-spacing:.4px;
  text-transform:uppercase;margin-bottom:3px}
.fix{white-space:pre-wrap;background:#f0fdf4;border-left:3px solid var(--ok);
  padding:8px 10px;border-radius:6px;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
.imp{background:#fafaf9;border-left:3px solid #a8a29e;padding:8px 10px;border-radius:6px}
.phase{border-radius:12px;padding:16px 18px;margin-bottom:14px;border:1px solid var(--line);
  background:var(--panel);box-shadow:var(--shadow)}
.phase .ph{display:flex;align-items:center;gap:10px;font-weight:600;font-size:16px;margin-bottom:4px}
.phase .pd{color:var(--muted);font-size:13px;margin-bottom:12px}
.pitem{border-top:1px dashed var(--line);padding:12px 0}
.pitem:first-of-type{border-top:none}
.pitem .pt{font-weight:600;margin-bottom:6px}
.pitem .row{display:grid;grid-template-columns:64px 1fr;gap:4px 10px;font-size:13.5px}
.pitem .row span:first-child{color:var(--muted)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 16px}
.filters button{border:1px solid var(--line);background:var(--panel);border-radius:999px;
  padding:5px 13px;font-size:13px;cursor:pointer;color:var(--text)}
.filters button.on{background:var(--text);color:#fff;border-color:var(--text)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;background:#fafbfc}
details{margin-top:8px}
summary{cursor:pointer;color:var(--accent);font-size:13.5px;user-select:none}
.banner{border-radius:12px;padding:14px 16px;margin-bottom:16px;font-size:14px;
  border:1px solid;box-shadow:var(--shadow)}
.banner.warn{background:#fffbeb;border-color:#fcd34d;color:#78350f}
.banner.info{background:#eff6ff;border-color:#bfdbfe;color:#1e3a8a}
.banner.ok{background:#ecfdf5;border-color:#a7f3d0;color:#064e3b}
.banner.danger{background:#fef2f2;border-color:#fecaca;color:#7f1d1d}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.foot{color:var(--muted);font-size:12.5px;text-align:center;margin-top:40px;line-height:1.9}
.pill{display:inline-block;padding:1px 7px;border-radius:6px;font-size:11.5px;
  background:#f1f5f9;color:#475569;margin-right:6px}
@media print{body{background:#fff}.card,.f,.phase,.sev{box-shadow:none}
  .filters{display:none}details{display:block}details>summary{display:none}
  details>div{display:block!important}}
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


# --------------------------------------------------------------------------

def _score_color(score: int) -> str:
    if score >= 90:
        return "#059669"
    if score >= 80:
        return "#0d9488"
    if score >= 70:
        return "#d97706"
    if score >= 60:
        return "#ea580c"
    return "#b91c1c"


def render_finding(f: dict) -> str:
    sev = f.get("severity", "INFO")
    color = SEV_COLOR.get(sev, "#64748b")
    ev = "\n".join(str(x) for x in f.get("evidence", []))
    parts = [
        f'<div class="f" style="border-left-color:{color}" data-sev="{sev}" '
        f'data-cat="{f.get("category","")}">',
        '<div class="hd">',
        f'<span class="tag" style="color:{color};border-color:{color}">'
        f'{_esc(SEV_LABEL.get(sev, sev))}</span>',
        f'<span class="t">{_esc(f.get("title"))}</span>',
        f'<span class="tag id">{_esc(f.get("id"))}</span>',
        f'<span class="tag">{_esc(CATEGORIES.get(f.get("category",""), ""))}</span>',
    ]
    if f.get("auto_fixable"):
        parts.append('<span class="tag" style="color:#059669;border-color:#a7f3d0">'
                     '可自动修复</span>')
    parts.append("</div>")

    if ev:
        parts.append(f'<div class="ev">{_esc(ev)}</div>')
    if f.get("impact"):
        parts.append(f'<div class="sec"><b>影响</b><div class="imp">{_esc(f["impact"])}</div></div>')
    if f.get("fix"):
        parts.append(f'<div class="sec"><b>修复</b><div class="fix">{_esc(f["fix"])}</div></div>')
    if f.get("ref"):
        parts.append(f'<div class="sub" style="margin-top:8px">参考：'
                     f'<span class="mono">{_esc(f["ref"])}</span></div>')
    parts.append("</div>")
    return "".join(parts)


def render_plan(plan: dict) -> str:
    if not plan["phases"]:
        return '<div class="banner ok">没有需要处置的问题。</div>'
    out = []
    for ph in plan["phases"]:
        out.append('<div class="phase">')
        out.append(f'<div class="ph"><span>{ph["icon"]}</span>'
                   f'<span>{_esc(ph["code"])} · {_esc(ph["name"])}</span>'
                   f'<span class="tag">{ph["count"]} 项</span></div>')
        out.append(f'<div class="pd">{_esc(ph["desc"])}</div>')
        for it in ph["items"]:
            out.append('<div class="pitem">')
            auto = ' <span class="pill" style="color:#059669">可自动修复</span>' \
                if it["auto_fixable"] else ""
            out.append(f'<div class="pt">{_esc(it["title"])}{auto}</div>')
            out.append('<div class="row">')
            if it.get("evidence"):
                out.append("<span>证据</span><span class=\"mono\">" +
                           _esc(" / ".join(str(x) for x in it["evidence"])[:400]) + "</span>")
            out.append(f'<span>动作</span><span class="mono">{_esc(it["action"])}</span>')
            out.append(f'<span>验收</span><span>{_esc(it["verify"])}</span>')
            out.append(f'<span>回滚</span><span>{_esc(it["rollback"])}</span>')
            out.append(f'<span>生效</span><span>{_esc(it["restart"])}</span>')
            out.append("</div></div>")
        out.append("</div>")
    return "".join(out)


def render_facts(report: dict) -> str:
    out = []
    for r in report.get("results", []):
        facts = r.get("facts") or {}
        notes = r.get("notes") or []
        rows = []
        for k, v in facts.items():
            if isinstance(v, (list, dict)):
                if not v:
                    continue
                txt = json.dumps(v, ensure_ascii=False)[:1400]
            else:
                txt = str(v)
            rows.append(f"<tr><td class='mono'>{_esc(k)}</td><td class='mono'>{_esc(txt)}</td></tr>")
        if not rows and not notes:
            continue
        status = "" if r.get("ok", True) else " <span class='tag' style='color:#d97706'>采集降级</span>"
        title = f"{_esc(r['name'])}（{len(rows)} 项事实）" if rows else _esc(r["name"])
        out.append(f"<details><summary>{title}{status}</summary>")
        if r.get("errors"):
            out.append("<div class='banner warn'>采集异常：" +
                       _esc("；".join(r["errors"])) + "</div>")
        for msg in notes:
            out.append("<div class='banner'>提示：" + _esc(str(msg)) + "</div>")
        if rows:
            out.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
        out.append("</details>")
    return "".join(out)


# --------------------------------------------------------------------------

def render_html(report: dict, plan: dict | None = None, plan_only: bool = False) -> str:
    plan = plan or build_plan_dict(report)
    host = report.get("host", {})
    counts = report.get("counts", {})
    score = report.get("score", 0)
    grade = report.get("grade", "-")
    sc = _score_color(score)

    # 结论横幅
    if counts.get("CRITICAL", 0):
        banner = ('<div class="banner danger"><b>存在严重问题，建议立即处置。</b> '
                  f'其中 {counts["CRITICAL"]} 项属于入侵迹象或可直接被利用的缺陷 —— '
                  '先止损（隔离/断网/改口令），再按下方 P0 清单逐条处理。</div>')
    elif counts.get("HIGH", 0):
        banner = (f'<div class="banner warn"><b>发现 {counts["HIGH"]} 项高危问题。</b> '
                  '建议 24 小时内处理完 P1 清单，处理前先做一次备份并保留回滚脚本。</div>')
    elif counts.get("MEDIUM", 0):
        banner = (f'<div class="banner info">存在 {counts["MEDIUM"]} 项中危问题，'
                  '建议本周内安排处理；P3 项可择机优化。</div>')
    else:
        banner = '<div class="banner ok">未发现中危以上问题，系统处于良好状态。</div>'

    # 严重度卡片
    sev_cards = []
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
        n = counts.get(sev, 0)
        sev_cards.append(
            f'<div class="sev"><span class="dot" style="background:{SEV_COLOR[sev]}"></span>'
            f'<div><div class="n">{n}</div><div class="t">{SEV_LABEL[sev]}</div></div></div>')

    degraded = [r["name"] for r in report.get("results", []) if not r.get("ok", True)]
    degraded_html = ""
    if degraded:
        degraded_html = ('<div class="banner warn"><b>部分模块采集降级：</b>'
                         + _esc("、".join(degraded))
                         + '。这些模块的结论可能不完整，请结合未覆盖清单一起看。</div>')

    # 分类统计
    cat_rows = []
    by_cat: dict[str, int] = {}
    for f in report.get("findings", []):
        by_cat[f.get("category", "config")] = by_cat.get(f.get("category", "config"), 0) + 1
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        cat_rows.append(f"<tr><td>{_esc(CATEGORIES.get(cat, cat))}</td><td>{n}</td></tr>")

    findings_html = "".join(render_finding(f) for f in report.get("findings", [])) \
        or '<div class="banner ok">没有命中任何检查项。</div>'

    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = "服务器安全与性能体检报告"

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {_esc(host.get('hostname'))}</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>{title}</h1>
<div class="sub">诺盾网络 · 服务器安全维护、杀木马病毒 skill · 作者：泰安诺盾 仇苏毅<br>
主机 <b>{_esc(host.get('hostname'))}</b> ·
模式 <span class="mono">{_esc(report.get('mode'))}</span> ·
报告生成 {gen_time} · 耗时 {report.get('duration_s', 0)}s</div>

<div style="height:16px"></div>
{banner}
{degraded_html}

<div class="grid g-hero">
  <div class="score">
    <div class="num" style="color:{sc}">{score}</div>
    <div class="grade">健康分 / 100 &nbsp; 等级 {grade}</div>
    <div class="bar"><i style="width:{score}%;background:{sc}"></i></div>
  </div>
  <div class="card">
    <dl class="kv">
      <dt>操作者</dt><dd>{_esc(host.get('operator'))}</dd>
      <dt>管理员权限</dt><dd>{'是' if host.get('is_admin') else '否（部分检查受限）'}</dd>
      <dt>平台</dt><dd class="mono">{_esc(host.get('platform'))}</dd>
      <dt>Python</dt><dd class="mono">{_esc(host.get('python'))}</dd>
      <dt>开始时间</dt><dd>{_esc(report.get('started'))}</dd>
      <dt>结束时间</dt><dd>{_esc(report.get('finished'))}</dd>
    </dl>
  </div>
</div>

<h2>问题分布</h2>
<div class="grid g-sev">{''.join(sev_cards)}</div>

<h2>处置计划</h2>
{render_plan(plan)}

<h2>问题明细</h2>
<div class="filters">
  <button class="on" data-f="all">全部</button>
  <button data-f="CRITICAL">严重</button>
  <button data-f="HIGH">高危</button>
  <button data-f="MEDIUM">中危</button>
  <button data-f="LOW">低危</button>
  <button data-f="INFO">提示</button>
  <button data-f="perf">性能</button>
  <button data-f="sec">安全</button>
  <button data-f="malware">恶意代码</button>
  <button data-f="crash">稳定性</button>
</div>
{findings_html}

<h2>采集明细（事实与原始证据）</h2>
<div class="sub" style="margin-bottom:10px">下表是本次体检实际读到的原始数据，
每条结论都能在这里找到出处；缺失的项请对照下方「未覆盖清单」。</div>
{render_facts(report)}

<h2>分类统计</h2>
<div class="card"><table><thead><tr><th>类别</th><th>问题数</th></tr></thead>
<tbody>{''.join(cat_rows) or '<tr><td colspan="2">无</td></tr>'}</tbody></table></div>

<div class="foot">
本报告由 服务器安全维护、杀木马病毒skill 生成 · 所有判定均基于本机实际读取的数据，未向外部回传任何信息<br>
报告中的每条结论都是<b>线索</b>，请结合业务实际人工复核后再执行变更；执行前务必备份并保留回滚脚本。<br>
生成时间 {gen_time} · {'仅展示处置计划' if plan_only else '完整报告'}
</div>
</div>
<script>
(function(){{
  var btns=document.querySelectorAll('.filters button');
  var items=document.querySelectorAll('.f');
  btns.forEach(function(b){{
    b.addEventListener('click',function(){{
      btns.forEach(function(x){{x.classList.remove('on')}});
      b.classList.add('on');
      var f=b.getAttribute('data-f');
      items.forEach(function(it){{
        var ok = f==='all' || it.getAttribute('data-sev')===f || it.getAttribute('data-cat')===f;
        it.style.display = ok ? '' : 'none';
      }});
    }});
  }});
}})();
</script>
</body></html>"""
    return body


def build_plan_dict(report: dict) -> dict:
    """把 report dict 还原成 plan。简单重建，避免循环依赖。"""
    from common import Finding
    from plan import build_plan

    class _R:
        pass
    r = _R()
    r.findings = []
    for fd in report.get("findings", []):
        f = Finding(id=fd["id"], title=fd["title"], severity=fd["severity"],
                    category=fd.get("category", "config"),
                    evidence=fd.get("evidence", []), impact=fd.get("impact", ""),
                    fix=fd.get("fix", ""), ref=fd.get("ref", ""),
                    auto_fixable=fd.get("auto_fixable", False))
        r.findings.append(f)
    return build_plan(r)


def write_report(report: dict, path: str | None = None) -> str:
    ensure_output_dir()
    p = Path(path) if path else (OUTPUT_DIR / f"报告-{report.get('host',{}).get('hostname','host')}"
                                             f"-{now_stamp()}.html")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(report), encoding="utf-8")
    return str(p)


def cmd_report(args) -> int:
    from common import C, load_json

    src = Path(args.input)
    if not src.is_file():
        print(C.red(f"  找不到报告数据：{args.input}"))
        return 1
    data = load_json(src)
    if "findings" not in data:
        print(C.red("  输入文件不是体检结果 JSON"))
        return 1
    out = write_report(data, args.out)
    print(C.green(f"  HTML 报告已生成：{out}"))
    if not args.no_open:
        try:
            import webbrowser  # noqa: PLC0415

            webbrowser.open(Path(out).as_uri())
        except Exception:  # noqa: BLE001
            pass
    return 0


def add_report_parser(sub) -> None:
    p = sub.add_parser("report", help="由体检 JSON 生成 HTML 报告")
    p.add_argument("input", help="audit 产出的 JSON 路径")
    p.add_argument("--out", default=None)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_report)
