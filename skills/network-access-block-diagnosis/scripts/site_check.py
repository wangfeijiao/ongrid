#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
站点访问故障分层诊断脚本
========================
判断某个网站打不开，到底是「浏览器问题」「本机网络/代理问题」
还是「服务端 WAF 拦截/限流」，并给出可执行的下一步动作。

设计要点（都是踩坑换来的，勿轻易改动）：
  1. 强制直连（ProxyHandler({})）—— AI 工具会话常注入本地代理，
     不绕开会把「代理故障」误判成「目标站故障」。
  2. 超时收敛到 10s 以内 —— 部分域名不可达会卡满超时，拖垮整个检测。
  3. 登录类接口必须用 POST 探测 —— GET 会得到 405/422，被误读成故障。
  4. 同时打印出口公网 IP —— 这是判断「换网络是否生效」的唯一可信指标，
     不能凭用户主观描述（实战教训：用户以为连了热点，其实没有）。
  5. 浏览器实测用 playwright-core + 已有 chromium 缓存，无需额外安装。

用法：
  python site_check.py                                  # 默认探测 deepseek
  python site_check.py --site github.com                # 任意站点
  python site_check.py --site x.com --urls / --urls /api/v0/users/login
  python site_check.py --site x.com --browser           # 加浏览器实测（截图）
  python site_check.py --site x.com --json              # 输出 JSON（供自动化消费）
  python site_check.py --site x.com --log run.jsonl     # 追加历史记录
  python site_check.py --site x.com --confirm           # 恢复确认（连测3次）
  python site_check.py --site x.com --watch 5           # 持续监测，恢复即停
"""

import argparse
import gzip
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ---- 状态码知识库 -----------------------------------------------------------
# (判定, 说明, 所属层, 建议动作)
# 层：browser / local / egress / waf / server / app
CODE_MAP = {
    200: ("正常", "", "app", ""),
    204: ("正常(无内容)", "接口连通", "app", ""),
    301: ("跳转", "站点存活，访问地址需调整", "app", "改用 Location 指向的地址"),
    302: ("跳转", "站点存活，访问地址需调整", "app", "改用 Location 指向的地址"),
    307: ("跳转", "站点存活，访问地址需调整", "app", "改用 Location 指向的地址"),
    308: ("跳转", "站点存活，访问地址需调整", "app", "改用 Location 指向的地址"),
    400: ("请求错误", "参数格式不对", "app", "检查请求参数"),
    401: ("未认证(401)", "通路正常，缺登录态/Token 失效", "app",
          "重新登录或刷新 Token；与网络无关，别去查网络"),
    403: ("被拒绝(403)", "WAF 地域/IP 封禁，或权限不足", "waf",
          "换合法网络；确认是否需要登录态"),
    404: ("不存在(404)", "站点通，是路径写错了", "app", "核对访问路径"),
    405: ("可达(405)", "方法不允许——接口正常，是探测用了 GET", "app",
          "改用 POST 探测；这不是故障"),
    407: ("需代理认证", "本机代理要求认证", "browser",
          "检查浏览器/代理客户端的认证配置"),
    408: ("请求超时", "服务端等不到完整请求", "local", "检查本机上行链路"),
    409: ("冲突", "业务状态冲突", "app", "查看业务返回体"),
    422: ("可达(422)", "参数校验失败——请求已达业务后端，通路正常", "app",
          "这是通路正常的强证据；按业务错误处理"),
    429: ("被限流(429)", "WAF 速率限制：出口 IP 被标记", "egress",
          "换网络后核对出口IP是否真的变了；或静置数小时；勿反复刷新"),
    451: ("因法律不可用(451)", "按法律要求屏蔽（地域封锁）", "waf",
          "所在地区被限制访问，换网络通常无效"),
    500: ("服务端错误", "后端异常", "server", "等服务端恢复"),
    502: ("网关异常(502)", "优先怀疑本机代理故障，其次服务端", "browser",
          "先关掉本机代理/换直连再试；服务端问题则等待"),
    503: ("服务不可用", "服务端临时过载或维护", "server", "等待数分钟重试"),
    504: ("网关超时", "上游响应超时", "server", "等待重试"),
    521: ("源站不可达(521)", "Cloudflare 连不上源站", "server", "服务端侧故障，等待"),
    525: ("SSL握手失败(525)", "Cloudflare 与源站 TLS 协商失败", "server", "服务端侧证书问题，等待"),
    0: ("无法连接", "网络不通／被本机防火墙拦截／DNS 解析失败", "local",
        "先查本机网络、DNS、防火墙"),
}


def _read_body(resp, limit=8192):
    """读取响应体，自动处理 gzip，避免中文乱码"""
    try:
        raw = resp.read(limit * 4)
    except Exception:
        return ""
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    for cs in ("utf-8", "gbk", "latin-1"):
        try:
            return raw[:limit].decode(cs)
        except Exception:
            continue
    return ""


def http_probe(url, timeout=10, method="GET"):
    """直连探测，返回 (状态码, 耗时, 响应体片段, 响应头)。状态码 0 表示连不上。"""
    data = b"{}" if method == "POST" else None
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if method == "POST":
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, time.time() - t0, _read_body(r), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            body = _read_body(e)
        except Exception:
            body = ""
        return e.code, time.time() - t0, body, dict(e.headers or {})
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return 0, time.time() - t0, str(e), {}


def get_public_ip():
    """取出口公网 IP 与归属地 —— 判断换网是否生效的关键"""
    for url in ("https://myip.ipip.net", "https://api.ipify.org"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with OPENER.open(req, timeout=8) as r:
                return _read_body(r, 256).strip()
        except Exception:
            continue
    return "获取失败"


def get_default_route():
    """取当前默认路由的本机 IP 与网关 —— 确认流量实际走哪条线"""
    try:
        out = subprocess.run(["route", "print", "0.0.0.0"],
                             capture_output=True, timeout=10).stdout
        text = ""
        for cs in ("gbk", "utf-8"):
            try:
                text = out.decode(cs)
                break
            except Exception:
                continue
        for line in text.splitlines():
            p = line.split()
            if len(p) >= 4 and p[0] == "0.0.0.0" and p[1] == "0.0.0.0":
                return f"本机 {p[3]} → 网关 {p[2]}"
    except Exception:
        pass
    return "读取失败"


def extract_waf_id(body):
    """从常见 WAF 拦截页里抠出追踪 ID（华为云 / Cloudflare / Akamai 等）"""
    for marker in ("Event ID:", "Ray ID:", "Reference #", "Request ID:"):
        i = body.find(marker)
        if i != -1:
            seg = body[i + len(marker):].strip()
            return marker + " " + seg.split("<")[0].split("\n")[0].strip()[:64]
    return ""


def classify(code, body):
    """返回 (判定, 说明, 归属层, 建议动作)"""
    if code in CODE_MAP:
        return CODE_MAP[code]
    low = body.lower()
    if "request blocked" in low or "access denied" in low or "forbidden" in low:
        return (f"被拦截({code})", "WAF 拦截页", "waf", "换合法网络或等待解除")
    if 500 <= code < 600:
        return (f"服务端异常({code})", "服务端或中间网关故障", "server", "等待重试")
    return (f"HTTP {code}", "", "app", "")


def normalize_path(p):
    """修正 Git Bash (MSYS) 对以 / 开头参数的路径转换。
    在 Git Bash 下 '/api/x' 会被转成本地盘符路径，需还原。"""
    if p.startswith("/") and len(p) > 1:
        return p
    m = re.match(r"^[A-Za-z]:/.*?/(api/.*|.*\.(json|html|php))$", p)
    if m:
        return "/" + m.group(1)
    return p


# ---- 浏览器实测（第①层验证）-------------------------------------------------
# 用 playwright-core 驱动本机已缓存的 chromium。
# 关键：显式传 executablePath，绕开 playwright 版本号与缓存版本号不一致的问题
# （实测踩坑：playwright-core 期待 chromium-1243，本机只有 1228，会报 Executable doesn't exist）。
PW_SNIPPET = r"""
const { chromium } = require('playwright-core');
(async () => {
  const url = process.argv[2], out = process.argv[3], exe = process.argv[4];
  let browser;
  try {
    const opts = { headless: true };
    if (exe) opts.executablePath = exe;
    browser = await chromium.launch(opts);
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(1500);
    const title = await page.title();
    const text = (await page.innerText('body').catch(() => '')).slice(0, 3000);
    await page.screenshot({ path: out, fullPage: false });
    console.log(JSON.stringify({
      ok: true, status: resp ? resp.status() : 0, title, text
    }));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: String(e).slice(0, 300) }));
  } finally {
    if (browser) await browser.close();
  }
})();
"""


def find_node():
    """定位 node 可执行文件。
    优先级：环境变量 SITE_CHECK_NODE > PATH 上的 node > 常见托管目录里自动扫描最高版本。
    不写死版本号——托管目录下的版本目录名会随后续安装变动。"""
    env_node = os.environ.get("SITE_CHECK_NODE")
    if env_node and os.path.exists(env_node):
        return env_node

    cand_root = os.path.expandvars(r"%USERPROFILE%\.workbuddy\binaries\node\versions")
    if os.path.isdir(cand_root):
        found = []
        for name in os.listdir(cand_root):
            p = os.path.join(cand_root, name, "node.exe")
            if os.path.exists(p):
                found.append((name, p))
        if found:
            # 版本号倒序，取最新
            def vkey(nm):
                return [int(x) for x in re.findall(r"\d+", nm)] or [0]
            found.sort(key=lambda c: vkey(c[0]), reverse=True)
            return found[0][1]

    return "node"   # 交给 PATH 解析


def find_chromium():
    """在本机 ms-playwright 缓存里找一个可用的 chromium 可执行文件。
    不依赖版本号：扫描目录取最高的可用版本，优先 full chrome（headless 更稳）。
    可用环境变量 SITE_CHECK_CHROME 直接指定路径。"""
    env_chrome = os.environ.get("SITE_CHECK_CHROME")
    if env_chrome and os.path.exists(env_chrome):
        return env_chrome

    base = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
    if not os.path.isdir(base):
        return None
    candidates = []
    for name in sorted(os.listdir(base)):
        # full chromium 优先，其次 headless shell
        if name.startswith("chromium-"):
            p = os.path.join(base, name, "chrome-win64", "chrome.exe")
            if os.path.exists(p):
                candidates.append((0, name, p))
        elif name.startswith("chromium_headless_shell-"):
            p = os.path.join(base, name, "chrome-headless-shell-win64",
                             "chrome-headless-shell.exe")
            if os.path.exists(p):
                candidates.append((1, name, p))
    if not candidates:
        return None
    # 先按类型（full 优先）再按版本号倒序
    def ver(n):
        m = re.search(r"-(\d+)", n)
        return int(m.group(1)) if m else 0
    candidates.sort(key=lambda c: (c[0], -ver(c[1])))
    return candidates[0][2]


def browser_probe(url, out_png):
    """用 playwright-core 真实渲染页面并截图，判定是否为 WAF 拦截页"""
    node = find_node()
    if not node:
        return {"ok": False, "error": "未找到 node 运行时"}

    exe = find_chromium()
    if not exe:
        return {"ok": False,
                "error": "未找到可用的 chromium（ms-playwright 缓存为空）"}

    ws = os.path.expandvars(r"%USERPROFILE%\.workbuddy\binaries\node\workspace")
    if not os.path.isdir(os.path.join(ws, "node_modules")):
        ws = os.environ.get("SITE_CHECK_NODE_MODULES", ws)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pw_probe.js")
    try:
        with open(script, "w", encoding="utf-8") as f:
            f.write(PW_SNIPPET)
    except Exception as e:
        return {"ok": False, "error": f"无法写入探测脚本: {e}"}

    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(ws, "node_modules")
    try:
        r = subprocess.run([node, script, url, out_png, exe],
                           capture_output=True, timeout=90, env=env)
        out = (r.stdout or b"").decode("utf-8", "ignore").strip().splitlines()
        for line in reversed(out):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        err = (r.stderr or b"").decode("utf-8", "ignore")[:300]
        return {"ok": False, "error": err or "无输出"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "浏览器探测超时(90s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        try:
            os.remove(script)
        except Exception:
            pass


def judge_browser_result(res):
    """把浏览器实测结果翻译成『是否被 WAF 拦截』的判定。
    注意：只认『拦截页特征』，不能拿『验证码』这类宽泛词判——
    登录页正文里就有"发送验证码"按钮，会误报（实战踩坑）。"""
    if not res.get("ok"):
        return "浏览器实测失败", f"原因：{res.get('error', '未知')}", "unknown"
    title = (res.get("title") or "").strip()
    tl = title.lower()
    text = (res.get("text") or "").lower()
    status = res.get("status", 0)

    # WAF 拦截页特征：这些词出现在【标题】或页面主体大段文本中才可信。
    # 只看标题最稳——拦截页标题高度特征化，正常业务页不会用这些做标题。
    waf_titles = ("request blocked", "rate limit", "access denied",
                  "error 1020", "attention required", "just a moment",
                  "forbidden", "请求被拒绝", "访问被拒绝", "请求被拦截")
    if any(k in tl for k in waf_titles):
        return "确认被 WAF 拦截", f"页面标题：{title}", "waf"

    # 人机验证挑战页：标题特征（Cloudflare/极验等挑战页的标题就是这些）
    challenge_titles = ("just a moment", "checking your browser",
                        "安全验证", "人机验证", "verify you are human")
    if any(k in tl for k in challenge_titles):
        return "被要求人机验证", f"页面标题：{title}", "waf"

    # 正文特征仅在【页面几乎无内容】时才可信（拦截页通常正文很短）；
    # 正常业务页正文长，含"blocked/denied"等词只是恰好有这个词
    if len(text.strip()) < 400:
        if any(k in text for k in ("request blocked", "rate limit reached",
                                   "access denied")):
            return "疑似被 WAF 拦截", f"页面标题：{title}", "waf"

    if status in (200, 0) or (status and 200 <= status < 400):
        return "页面正常渲染", f"页面标题：{title}", "ok"
    return f"页面返回 {status}", f"页面标题：{title}", "other"


# ---- 目标地址 ---------------------------------------------------------------
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def base_url(site, scheme="auto"):
    """把 --site 解析成 base URL。

    回环地址默认走 http：本机服务（开发服务器、预览服务、控制面板）
    几乎都是明文 HTTP，硬拼 https 会握手失败，把「服务健康」误判成
    「连不上」——那正是本技能要消灭的误判。
    显式传 --scheme http/https 时以传参为准。
    """
    if scheme in ("http", "https"):
        return f"{scheme}://{site}"
    host = site
    if host.startswith("["):                      # IPv6 字面量，如 [::1]:8080
        host = host.split("]")[0].lstrip("[")
    elif ":" in host:                             # host:port
        host = host.rsplit(":", 1)[0]
    host = host.lower()
    if host in LOOPBACK_HOSTS or host.startswith("127."):
        return f"http://{site}"
    return f"https://{site}"


# ---- 单轮检测 ---------------------------------------------------------------
def run_once(site, paths, use_browser=False, shots_dir=None, quiet=False,
             scheme="auto"):
    base = base_url(site, scheme)
    """返回 (是否正常, 结构化结果)"""
    now = datetime.now()
    result = {
        "time": now.isoformat(timespec="seconds"),
        "site": site,
        "public_ip": get_public_ip(),
        "default_route": get_default_route(),
        "probes": [],
        "browser": None,
        "conclusion": "",
        "layer": "",
        "healthy": False,
    }

    if not quiet:
        print("=" * 66)
        print(f"  站点访问诊断   {now:%Y-%m-%d %H:%M:%S}")
        print("=" * 66)
        print(f"  出口公网 IP : {result['public_ip']}")
        print(f"  默认路由    : {result['default_route']}")
        print(f"  目标站点    : {site}")
        print("-" * 66)

    main_code = None
    for path in paths:
        path = normalize_path(path)
        url = f"{base}{path}"
        method = "POST" if ("login" in path or "auth" in path) else "GET"
        code, cost, body, _hdr = http_probe(url, method=method)
        label, note, layer, action = classify(code, body)
        waf_id = extract_waf_id(body) if code in (403, 429) else ""
        rec = {"path": path, "method": method, "code": code,
               "seconds": round(cost, 3), "verdict": label,
               "note": note, "layer": layer, "action": action,
               "waf_id": waf_id}
        result["probes"].append(rec)

        if not quiet:
            print(f"  {path:<32} {label:<16} {cost:>6.2f}s")
            if note:
                print(f"    ↳ {note}")
            if action:
                print(f"    ↳ 建议：{action}")
            if waf_id:
                print(f"    ↳ WAF 追踪ID: {waf_id}")
        if path == "/" or main_code is None:
            main_code = code

    # 第①层：浏览器实测（可选）
    if use_browser:
        png = os.path.join(shots_dir or ".",
                           f"{site.replace('.', '_')}_{now:%H%M%S}.png")
        if not quiet:
            print("-" * 66)
            print("  浏览器实测中（真实渲染 + 截图）……")
        res = browser_probe(f"{base}/", png)
        verdict, detail, blayer = judge_browser_result(res)
        result["browser"] = {"verdict": verdict, "detail": detail,
                             "layer": blayer, "screenshot": png if res.get("ok") else None}
        if not quiet:
            print(f"  浏览器判定  : {verdict}")
            if detail:
                print(f"    ↳ {detail}")
            if res.get("ok"):
                print(f"    ↳ 截图：{png}")

    # 结论
    if main_code == 200:
        verdict, layer = "站点正常可访问", "ok"
    elif main_code in (429, 403, 451):
        verdict = "被 WAF 拦截 → 非浏览器问题、非本机问题"
        layer = "waf"
    elif main_code == 0:
        verdict, layer = "连不上 → 先排查本机网络/DNS/防火墙", "local"
    elif main_code in (401,):
        verdict, layer = "缺登录态 → 通路正常，去重新登录", "app"
    else:
        verdict, layer = f"返回 {main_code}，按上方建议处理", "other"
    result["conclusion"] = verdict
    result["layer"] = layer
    result["healthy"] = (main_code == 200)

    if not quiet:
        print("-" * 66)
        print(f"  结论：{verdict}")
        if layer == "waf":
            print("  下一步：")
            print("    1) 换网络，然后核对上方『出口公网 IP』是否真的变了")
            print("       —— IP 没变说明换网没生效（实战最常见的假象）")
            print("    2) 用 WAF 追踪ID向服务方申诉")
            print("    3) 静置数小时，勿反复刷新（会加重标记）")
        print("=" * 66)

    return result["healthy"], result


def confirm_recovery(site, paths, times=3, gap=5, scheme="auto"):
    """恢复确认：连测 N 次，全部正常才算真正恢复（避免限流窗口边界误判）"""
    base = base_url(site, scheme)
    print(f"\n恢复确认：连测 {times} 次，间隔 {gap} 秒……")
    ok_count = 0
    for i in range(times):
        code, cost, _b, _h = http_probe(f"{base}/")
        mark = "✓" if code == 200 else f"✗ ({code})"
        print(f"  第 {i+1}/{times} 次：{mark}   耗时 {cost:.2f}s")
        if code == 200:
            ok_count += 1
        if i < times - 1:
            time.sleep(gap)
    stable = ok_count == times
    print(f"  结果：{ok_count}/{times} 次正常 → " +
          ("已稳定恢复" if stable else "仍不稳定，建议继续观察"))
    return stable


def append_log(log_path, payload):
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"  日志写入失败：{e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="站点访问故障分层诊断")
    ap.add_argument("--site", default="chat.deepseek.com", help="目标域名")
    ap.add_argument("--scheme", choices=["auto", "http", "https"], default="auto",
                    help="目标协议；auto 时回环地址走 http，其余走 https")
    ap.add_argument("--urls", action="append", default=None,
                    help="要探测的路径，可重复；默认 /")
    ap.add_argument("--browser", action="store_true",
                    help="启用浏览器实测（真实渲染 + 截图）")
    ap.add_argument("--shots", default=None, help="截图保存目录，默认当前目录")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--log", default=None, metavar="文件",
                    help="把本轮结果追加到 JSONL 日志文件")
    ap.add_argument("--confirm", action="store_true",
                    help="恢复确认模式：连测 3 次全通过才判定恢复")
    ap.add_argument("--watch", nargs="?", const=5, type=int, metavar="分钟",
                    help="持续监测，正常后自动停止")
    args = ap.parse_args()

    paths = args.urls or ["/"]

    # 单轮模式
    if args.watch is None:
        healthy, res = run_once(args.site, paths,
                                use_browser=args.browser, shots_dir=args.shots,
                                scheme=args.scheme)
        if args.confirm:
            res["recovery_stable"] = confirm_recovery(args.site, paths,
                                                      scheme=args.scheme)
        if args.log:
            append_log(args.log, res)
            print(f"  已追加日志：{args.log}")
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if healthy else 2)

    # 监测模式
    interval = max(1, args.watch) * 60
    print(f"持续监测 {args.site}，每 {args.watch} 分钟一次，恢复正常后自动停止。Ctrl+C 退出。\n")
    n = 0
    while True:
        n += 1
        print(f"[第 {n} 轮]")
        healthy, res = run_once(args.site, paths,
                                use_browser=args.browser,
                                shots_dir=args.shots, quiet=args.json,
                                scheme=args.scheme)
        if args.log:
            append_log(args.log, res)
        if healthy:
            if args.confirm:
                stable = confirm_recovery(args.site, paths, scheme=args.scheme)
                res["recovery_stable"] = stable
                if args.log:
                    append_log(args.log, res)
                if not stable:
                    print(f"\n>>> 单次通过但不够稳定，{args.watch} 分钟后继续。")
                    time.sleep(interval)
                    continue
            print("\n>>> 已恢复，监测结束。")
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            break
        print(f"\n>>> 仍未恢复，{args.watch} 分钟后重测……")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n已手动退出。")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。")
