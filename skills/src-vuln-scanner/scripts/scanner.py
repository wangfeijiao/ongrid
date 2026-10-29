#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoSRCScan —— 授权范围内的自动化漏洞检测工具

⚠️ 合规声明：本工具仅可用于已获得书面授权的安全测试场景（如企业SRC项目、
授权渗透测试、自有资产的漏洞自检）。禁止用于未授权目标，使用前必须在
scope 文件中确认授权（authorization_confirmed: true）。

功能：
  1. 授权范围校验（白名单强制，未授权目标直接拒绝）
  2. 轻量爬取（链接/表单发现）
  3. 漏洞检测：SQL注入 / 反射型XSS / CSRF / 越权访问(IDOR) / 敏感信息泄露
  4. 结构化报告（Markdown + JSON：漏洞名称、风险等级、复现步骤、影响范围、修复建议）
  5. 批量任务管理（tasks.json）
  6. 全量操作审计日志（logs/）

用法：
  python scanner.py scan  --scope scope.json --target http://example.com
  python scanner.py batch --tasks tasks.json
  python scanner.py tasks --tasks tasks.json        # 查看任务状态
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from html.parser import HTMLParser
from urllib import request as urlreq, parse as urlparse, error as urlerror

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_VERSION = "1.1.0"
# 部分站点(如WAF)会拦截非浏览器UA，授权测试场景下使用浏览器UA为常规做法
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 AutoSRCScan/1.1 (authorized-security-testing)"
TIMEOUT = 20
MAX_PAGES = 20          # 单目标最大爬取页面数
MAX_PARAMS_PER_PAGE = 8

SEVERITY_ORDER = {"严重": 4, "高危": 3, "中危": 2, "低危": 1, "信息": 0}

# ---------------------------------------------------------------------------
# 日志（审计）
# ---------------------------------------------------------------------------

def build_logger(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logfile = os.path.join(
        log_dir, "audit-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".log"
    )
    logger = logging.getLogger("autosrc")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.info(f"审计日志已开启: {logfile}")
    return logger


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------

class HttpResponse:
    def __init__(self, status, body, headers, url):
        self.status, self.body, self.headers, self.url = status, body, headers, url


def http_fetch(url, method="GET", data=None, logger=None, timeout=TIMEOUT):
    """发起 HTTP 请求并返回 HttpResponse；异常时返回 None。"""
    try:
        req = urlreq.Request(url, method=method)
        req.add_header("User-Agent", UA)
        body = None
        if data is not None:
            body = urlparse.urlencode(data).encode("utf-8")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urlreq.urlopen(req, data=body, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            if logger:
                logger.debug(f"HTTP {method} {url} -> {resp.status} ({len(raw)}B)")
            return HttpResponse(resp.status, text, dict(resp.headers), resp.url)
    except urlerror.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        if logger:
            logger.debug(f"HTTP {method} {url} -> {e.code} (HTTPError)")
        return HttpResponse(e.code, raw.decode("utf-8", errors="replace"), {}, url)
    except Exception as e:
        if logger:
            logger.debug(f"HTTP {method} {url} -> EXC {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    vuln_id: str
    name: str                 # 漏洞名称
    vuln_type: str            # 类型：SQLi/XSS/CSRF/IDOR/INFO_LEAK
    severity: str             # 严重/高危/中危/低危/信息
    url: str
    method: str = "GET"
    param: str = ""
    evidence: str = ""        # 证据（截断的响应片段）
    repro_steps: list = field(default_factory=list)   # 复现步骤
    impact: str = ""          # 影响范围分析
    fix: str = ""             # 修复建议
    discovered_at: str = ""

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# 爬虫：提取链接与表单
# ---------------------------------------------------------------------------

class PageParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base = base_url
        self.links = []
        self.forms = []          # [{action, method, fields:[{name,type,value}]}]
        self._cur_form = None

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "a" and ad.get("href"):
            self.links.append(urlparse.urljoin(self.base, ad["href"]))
        elif tag == "form":
            self._cur_form = {
                "action": urlparse.urljoin(self.base, ad.get("action") or self.base),
                "method": (ad.get("method") or "GET").upper(),
                "fields": [],
            }
        elif tag in ("input", "textarea", "select") and self._cur_form is not None:
            self._cur_form["fields"].append({
                "name": ad.get("name") or "",
                "type": (ad.get("type") or "text").lower(),
                "value": ad.get("value") or "",
            })

    def handle_endtag(self, tag):
        if tag == "form" and self._cur_form is not None:
            self.forms.append(self._cur_form)
            self._cur_form = None


def crawl(root_url, logger, max_pages=MAX_PAGES):
    """轻量爬取：抓取首页及同站链接（深度1），返回 (pages, forms)。"""
    host = urlparse.urlsplit(root_url).netloc
    seen, pages, forms = set(), [], []
    queue = [root_url]
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        normalized = url.split("#")[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        parsed = urlparse.urlsplit(normalized)
        if parsed.netloc != host or parsed.scheme not in ("http", "https"):
            continue
        resp = http_fetch(normalized, logger=logger)
        if resp is None or resp.status != 200 or "text/html" not in resp.headers.get("Content-Type", "html"):
            # 无 Content-Type 的本地服务默认按 HTML 处理
            if resp is None or resp.status != 200:
                continue
        pages.append((normalized, resp.body))
        try:
            p = PageParser(normalized)
            p.feed(resp.body)
        except Exception:
            continue
        forms.extend(p.forms)
        for lk in p.links:
            lk2 = lk.split("#")[0]
            if urlparse.urlsplit(lk2).netloc == host and lk2 not in seen:
                queue.append(lk2)
    logger.info(f"爬取完成：{len(pages)} 个页面，{len(forms)} 个表单")
    return pages, forms


# ---------------------------------------------------------------------------
# 检测规则
# ---------------------------------------------------------------------------

SQL_ERROR_PATTERNS = [
    r"SQL syntax.*?(MySQL|MariaDB)", r"Warning.*?\bmysql?_", r"mysqli?_[a-z_]+\(",
    r"sqlite3?\.(OperationalError|ProgrammingError|DatabaseError)",
    r"psycopg2\.", r"SQLSTATE\[\w+\]", r"ORA-\d{5}", r"Unclosed quotation mark",
    r"Microsoft OLE DB Provider for SQL Server", r"ODBC.*?SQL Server",
    r"You have an error in your SQL syntax", r"unrecognized token",
    r"quoted string not properly terminated",
]
SQL_ERROR_RE = re.compile("|".join(SQL_ERROR_PATTERNS), re.I)

XSS_MARKER_PAYLOAD = 'vscan7x<svg/onload=alert(1)>'

IDOR_PARAM_RE = re.compile(r"^(id|uid|userid|user_id|account_id|uid|docid|doc_id|order_id|oid)$", re.I)

SENSITIVE_PATHS = [
    "/.git/config", "/.env", "/.svn/entries", "/.DS_Store",
    "/backup.zip", "/backup.sql", "/db.sql", "/dump.sql",
    "/WEB-INF/web.xml", "/phpinfo.php", "/server-status",
    "/.git/HEAD", "/config.json.bak", "/application.yml",
]

INFO_LEAK_PATTERNS = [
    (re.compile(r"\b1[3-9]\d{9}\b"), "手机号泄露", "中危"),
    (re.compile(r"\b\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"), "身份证号泄露", "中危"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "私钥泄露", "严重"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "云平台访问密钥(AK/SK)泄露", "严重"),
    (re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"), "内网IP泄露", "低危"),
    (re.compile(r"(jdbc:[a-z]+://|root:.*?@tcp\()"), "数据库连接串泄露", "高危"),
]
SENSITIVE_ACTION_RE = re.compile(r"(pay|transfer|passwd|password|change|delete|admin|withdraw|order)", re.I)


def truncate(text, n=200):
    t = text.strip().replace("\n", " ")
    return t[:n] + ("..." if len(t) > n else "")


class Detector:
    """检测器基类。"""

    def __init__(self, logger):
        self.log = logger
        self.findings = []

    def add(self, **kw):
        f = Finding(
            vuln_id="VULN-" + datetime.now().strftime("%H%M%S") + f"-{len(self.findings)+1:03d}",
            discovered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **kw,
        )
        self.findings.append(f)
        self.log.info(f"[发现] [{f.severity}] {f.name} @ {f.url} (参数: {f.param or '-'})")


class SQLiDetector(Detector):
    """基于报错回显与布尔差异的 SQL 注入探测。"""

    def test_param(self, url, method, param, baseline_params):
        # 1) 报错型：注入单引号
        mutated = dict(baseline_params)
        mutated[param] = str(baseline_params.get(param, "")) + "'\"("
        resp = self._submit(url, method, mutated)
        if resp and SQL_ERROR_RE.search(resp.body):
            self.add(
                name="SQL注入（报错回显）",
                vuln_type="SQLi",
                severity="高危",
                url=url, method=method, param=param,
                evidence=truncate(SQL_ERROR_RE.search(resp.body).group(0)),
                repro_steps=[
                    f"访问/提交 {url}（{method}，参数 {param}）",
                    f"将参数 {param} 的值修改为 {mutated[param]!r} 提交",
                    "响应中出现数据库报错信息，说明输入未经过滤直接进入SQL语句",
                ],
                impact="攻击者可构造恶意SQL语句，读取、篡改或删除数据库内容，"
                       "严重时可获取管理员权限或进一步渗透内网。",
                fix="对所有外部输入使用参数化查询/预编译语句；使用ORM；"
                    "对输入做白名单校验；关闭生产环境错误回显；遵循最小权限原则配置数据库账号。",
            )
            return True
        # 2) 布尔型：'and'1'='1 与 'and'1'='2 的响应差异
        base_resp = self._submit(url, method, baseline_params)
        if not base_resp:
            return False
        p_true, p_false = dict(baseline_params), dict(baseline_params)
        v = str(baseline_params.get(param, ""))
        p_true[param] = v + "' and '1'='1' -- "
        p_false[param] = v + "' and '1'='2' -- "
        r_true, r_false = self._submit(url, method, p_true), self._submit(url, method, p_false)
        if r_true and r_false and r_true.status == 200 and r_false.status == 200:
            len_t, len_f = len(r_true.body), len(r_false.body)
            if len_t == len(base_resp.body) and abs(len_t - len_f) > 15:
                self.add(
                    name="SQL注入（布尔盲注）",
                    vuln_type="SQLi",
                    severity="高危",
                    url=url, method=method, param=param,
                    evidence=f"注入 '1'='1 响应长度 {len_t}，注入 '1'='2 响应长度 {len_f}，基准 {len(base_resp.body)}",
                    repro_steps=[
                        f"访问 {url}（{method}，参数 {param}）",
                        f"分别提交 {p_true[param]!r} 与 {p_false[param]!r}",
                        "两次响应内容长度出现稳定差异，说明注入条件改变了查询逻辑",
                    ],
                    impact="攻击者可通过布尔盲注逐位提取数据库任意数据，包括用户敏感信息与管理员凭据。",
                    fix="使用参数化查询，禁止SQL拼接；部署WAF规则拦截注入特征；对参数类型做强校验。",
                )
                return True
        return False

    def _submit(self, url, method, params):
        if method.upper() == "POST":
            return http_fetch(url, method="POST", data=params, logger=self.log)
        qs = urlparse.urlencode(params)
        return http_fetch(url.split("?")[0] + "?" + qs, logger=self.log)


class XSSDetector(Detector):
    """反射型 XSS：提交标记 payload，检查是否未经转义直接回显。"""

    def test_param(self, url, method, param, baseline_params):
        mutated = dict(baseline_params)
        mutated[param] = XSS_MARKER_PAYLOAD
        if method.upper() == "POST":
            resp = http_fetch(url, method="POST", data=mutated, logger=self.log)
        else:
            resp = http_fetch(url.split("?")[0] + "?" + urlparse.urlencode(mutated), logger=self.log)
        if resp and XSS_MARKER_PAYLOAD in resp.body:
            self.add(
                name="反射型跨站脚本（XSS）",
                vuln_type="XSS",
                severity="中危",
                url=url, method=method, param=param,
                evidence=f"payload {XSS_MARKER_PAYLOAD!r} 未经HTML转义直接回显在响应中",
                repro_steps=[
                    f"访问/提交 {url}（{method}，参数 {param}）",
                    f"将参数值设置为 {XSS_MARKER_PAYLOAD!r}",
                    "响应页面中该payload被原样输出，可被替换为任意脚本代码并在浏览器执行",
                ],
                impact="攻击者可构造恶意链接诱使受害者点击，在其浏览器中执行任意脚本，"
                       "窃取会话Cookie、伪造操作、挂马钓鱼。",
                fix="所有输出点按上下文进行HTML实体转义；输入侧做白名单过滤；"
                    "设置Content-Security-Policy响应头；Cookie加HttpOnly/Secure属性。",
            )
            return True
        return False


class CSRFDetector(Detector):
    """CSRF：检查敏感操作的 POST 表单是否携带 token。"""

    TOKEN_FIELD_RE = re.compile(r"(csrf|token|authenticity|_token)", re.I)

    def test_form(self, form, page_url):
        if form["method"] != "POST":
            return False
        has_token = any(
            f["type"] == "hidden" and self.TOKEN_FIELD_RE.search(f["name"])
            for f in form["fields"]
        )
        action = form["action"].split("?")[0]
        # 登录/搜索类表单风险低，跳过
        if re.search(r"(login|search|register|signup)", action, re.I):
            return False
        if not has_token:
            sensitive = bool(SENSITIVE_ACTION_RE.search(action))
            self.add(
                name="跨站请求伪造（CSRF）",
                vuln_type="CSRF",
                severity="中危" if sensitive else "低危",
                url=action, method="POST",
                evidence=f"POST 表单（来源 {page_url}）未包含任何 CSRF token 隐藏字段",
                repro_steps=[
                    f"登录站点后打开表单页 {page_url}",
                    f"在攻击者页面构造自动提交到 {action} 的跨站表单",
                    "诱使已登录用户访问该页面，请求将以用户身份被强制执行",
                ],
                impact="攻击者可在受害者不知情的情况下以其身份执行修改密码、转账、下单等敏感操作。",
                fix="为所有状态变更请求添加随机CSRF token并服务端校验；"
                    "敏感操作要求二次验证；校验Origin/Referer；SameSite Cookie属性。",
            )
            return True
        return False


class IDORDetector(Detector):
    """越权访问（水平）：对ID类参数做邻近值替换并比较响应。"""

    def test_param(self, url, param, params):
        m = re.search(r"\d+", str(params.get(param, "")))
        if not m:
            return False
        base_resp = http_fetch(url.split("?")[0] + "?" + urlparse.urlencode(params), logger=self.log)
        if not base_resp or base_resp.status != 200:
            return False
        cur = int(m.group(0))
        for nxt in (cur + 1, max(cur - 1, 0)):
            if nxt == cur:
                continue
            mutated = dict(params)
            mutated[param] = str(params.get(param, "")).replace(m.group(0), str(nxt), 1)
            r = http_fetch(url.split("?")[0] + "?" + urlparse.urlencode(mutated), logger=self.log)
            if r and r.status == 200:
                # 结构相似（长度接近）且内容不同，说明返回了另一对象的数据
                if r.body != base_resp.body and abs(len(r.body) - len(base_resp.body)) < 0.3 * max(len(r.body), 1):
                    self.add(
                        name="越权访问（水平越权/IDOR）",
                        vuln_type="IDOR",
                        severity="高危",
                        url=url, param=param,
                        evidence=f"参数 {param}={m.group(0)} 与 {param}={nxt} 均返回200且页面结构一致、数据不同（无身份校验）",
                        repro_steps=[
                            f"以普通用户身份访问 {url}",
                            f"将参数 {param} 的值从 {m.group(0)} 修改为 {nxt}",
                            "页面正常返回了其他用户/对象的数据，说明服务端未校验数据归属",
                        ],
                        impact="攻击者可遍历ID参数批量获取其他用户的敏感数据，造成大规模信息泄露。",
                        fix="服务端对每次数据访问校验资源归属（基于会话身份而非客户端参数）；"
                            "使用不可预测的全局唯一ID；对异常高频访问触发风控。",
                    )
                    return True
        return False


class HeaderDetector(Detector):
    """安全响应头缺失检测（点击劫持/MIME嗅探/传输安全防护缺失）。"""

    HEADER_CHECKS = [
        ("Content-Security-Policy", "缺少CSP内容安全策略响应头", "低危",
         "页面未声明内容安全策略，一旦出现XSS漏洞，攻击者可自由加载外部恶意脚本。",
         "为站点配置 Content-Security-Policy 响应头，限制脚本/样式/框架来源。"),
        ("X-Frame-Options", "缺少X-Frame-Options响应头（点击劫持防护缺失）", "低危",
         "页面可被任意站点内嵌为iframe，可构造点击劫持攻击诱骗用户误操作。",
         "配置 X-Frame-Options: SAMEORIGIN（或使用CSP frame-ancestors）。"),
        ("X-Content-Type-Options", "缺少X-Content-Type-Options响应头", "信息",
         "浏览器可能对响应做MIME嗅探，把非脚本内容当脚本执行，放大XSS等漏洞危害。",
         "配置 X-Content-Type-Options: nosniff。"),
    ]
    HSTS_CHECK = ("Strict-Transport-Security", "缺少HSTS响应头（HTTPS站点）", "低危",
                  "用户首次访问或误输http时可能遭中间人降级攻击。",
                  "为全站HTTPS配置 Strict-Transport-Security: max-age=31536000; includeSubDomains。")

    def test(self, url):
        resp = http_fetch(url, logger=self.log)
        if not resp or resp.status != 200:
            return
        lower = {k.lower(): v for k, v in resp.headers.items()}
        checks = list(self.HEADER_CHECKS)
        if urlparse.urlsplit(url).scheme == "https":
            checks.append(self.HSTS_CHECK)
        for name, title, severity, impact, fix in checks:
            if name.lower() not in lower:
                self.add(
                    name=title,
                    vuln_type="CONFIG",
                    severity=severity,
                    url=url,
                    evidence=f"响应头中未返回 {name}",
                    repro_steps=[
                        f"访问 {url}",
                        f"查看响应头，未发现 {name} 字段",
                    ],
                    impact=impact,
                    fix=fix,
                )


class DirListDetector(Detector):
    """目录列出（Directory Listing / Index of）检测。"""

    DIRLIST_RE = re.compile(r"<title>Index of /|>Directory listing for /", re.I)

    def probe_paths(self, root_url):
        for path in ("/static/", "/upload/", "/backup/", "/files/"):
            url = root_url.rstrip("/") + path
            resp = http_fetch(url, logger=self.log)
            if resp and resp.status == 200 and self.DIRLIST_RE.search(resp.body):
                self.add(
                    name="目录遍历暴露（Directory Listing）",
                    vuln_type="INFO_LEAK",
                    severity="中危",
                    url=url,
                    evidence="页面返回了服务器自动生成的文件索引（Index of /）",
                    repro_steps=[
                        f"直接访问 {url}",
                        "页面列出了该目录下的全部文件，可任意浏览/下载",
                    ],
                    impact="备份文件、源码压缩包、内部文档等可能被直接枚举下载，常成为入侵的突破口。",
                    fix="关闭Web服务器的目录列出功能（如Nginx autoindex off、Apache Options -Indexes）。",
                )


class InfoLeakDetector(Detector):
    """敏感路径探测 + 响应内容敏感信息匹配。"""

    GIT_CONFIG_RE = re.compile(r"\[core\]|repositoryformatversion")
    ENV_SECRET_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY)\s*=", re.I)

    def probe_paths(self, root_url):
        for path in SENSITIVE_PATHS:
            url = root_url.rstrip("/") + path
            resp = http_fetch(url, logger=self.log)
            if not resp or resp.status != 200:
                continue
            body = resp.body.strip()
            if not body or len(body) < 5:
                continue
            if path.startswith("/.git/") and self.GIT_CONFIG_RE.search(body):
                self._path_finding(url, path, "高危", "源码版本库(.git)可公开访问",
                    "响应包含 [core] 等 git 仓库配置内容",
                    ["直接访问 " + url, "返回了 git 仓库配置文件内容"],
                    "攻击者可利用工具还原完整源代码，获取数据库凭据、内部逻辑与密钥。",
                    "禁止将 .git 目录发布到Web根目录；部署前清理版本库目录；检查历史泄露并轮换其中的密钥。")
            elif path == "/.env" and self.ENV_SECRET_RE.search(body):
                self._path_finding(url, path, "严重", "环境配置文件(.env)泄露",
                    "响应包含 PASSWORD/SECRET/KEY 等敏感配置项",
                    ["直接访问 " + url, "返回了 .env 配置文件内容"],
                    "数据库密码、API密钥等凭据直接暴露，攻击者可用其直接入侵数据库或第三方服务。",
                    "将 .env 移出Web可访问目录；配置Web服务器拒绝访问点文件；立即轮换已泄露的全部密钥。")
            elif path in ("/backup.sql", "/db.sql", "/dump.sql") and ("CREATE TABLE" in body.upper() or "INSERT INTO" in body.upper()):
                self._path_finding(url, path, "严重", "数据库备份文件泄露",
                    "响应包含建表/插入语句，疑似数据库导出文件",
                    ["直接访问 " + url, "获得了数据库备份内容"],
                    "全部数据库内容（含用户敏感信息）可被任意下载。",
                    "删除Web目录下的备份文件；建立发布流程检查清单，禁止备份文件上线。")
            elif path == "/phpinfo.php" and "phpinfo()" in body:
                self._path_finding(url, path, "中危", "phpinfo 信息泄露",
                    "响应包含 phpinfo 输出",
                    ["直接访问 " + url, "页面暴露了服务器配置信息"],
                    "暴露绝对路径、扩展、环境变量等，为后续攻击提供情报。",
                    "生产环境删除 phpinfo 页面。")

    def _path_finding(self, url, path, severity, name, evidence, steps, impact, fix):
        self.add(name=name, vuln_type="INFO_LEAK", severity=severity, url=url,
                 evidence=evidence, repro_steps=steps, impact=impact, fix=fix)

    def scan_content(self, url, body):
        for regex, name, severity in INFO_LEAK_PATTERNS:
            m = regex.search(body)
            if m:
                sample = m.group(0)
                self.add(
                    name="敏感信息泄露（" + name + "）",
                    vuln_type="INFO_LEAK",
                    severity=severity,
                    url=url,
                    evidence=f"响应内容中匹配到: {sample}",
                    repro_steps=[
                        f"访问 {url}",
                        f"在返回内容中发现{name}: {sample}",
                    ],
                    impact="敏感个人信息或内部数据直接暴露在可公开访问的页面中，违反个人信息保护要求，可被批量采集。",
                    fix="页面脱敏展示；接口增加鉴权与频控；清理不应对外暴露的数据字段。",
                )


# ---------------------------------------------------------------------------
# 扫描调度
# ---------------------------------------------------------------------------

def extract_params(url):
    q = urlparse.urlsplit(url).query
    return dict(urlparse.parse_qsl(q)) if q else {}


def dedupe(findings):
    seen, out = set(), []
    for f in findings:
        key = (f.vuln_type, f.name, f.url.split("?")[0], f.param)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def scan_target(target, logger):
    """对单个授权目标执行完整扫描，返回 findings 列表。"""
    logger.info(f"========== 开始扫描: {target} ==========")
    pages, forms = crawl(target, logger)

    sqli, xss, csrf, idor, leak = (
        SQLiDetector(logger), XSSDetector(logger), CSRFDetector(logger),
        IDORDetector(logger), InfoLeakDetector(logger),
    )
    header, dirlist = HeaderDetector(logger), DirListDetector(logger)

    # 1. 敏感路径探测 + 首页内容敏感信息 + 安全响应头 + 目录列出
    leak.probe_paths(target)
    header.test(target)
    dirlist.probe_paths(target)

    # 2. 每个页面：内容敏感信息 + URL参数检测
    for url, body in pages:
        leak.scan_content(url, body)
        params = extract_params(url)
        if not params:
            continue
        for i, (k, v) in enumerate(list(params.items())):
            if i >= MAX_PARAMS_PER_PAGE:
                break
            sqli.test_param(url, "GET", k, params)
            xss.test_param(url, "GET", k, params)
            if IDOR_PARAM_RE.match(k):
                idor.test_param(url, k, params)

    # 3. 表单检测
    seen_forms = set()
    for form in forms:
        sig = (form["action"], form["method"], tuple(sorted(f["name"] for f in form["fields"])))
        if sig in seen_forms:
            continue
        seen_forms.add(sig)
        csrf.test_form(form, form["action"])
        fields = [f for f in form["fields"] if f["name"] and f["type"] not in ("hidden", "submit", "button", "csrf")]
        baseline = {f["name"]: (f["value"] or "test") for f in form["fields"]}
        for i, f in enumerate(fields):
            if i >= MAX_PARAMS_PER_PAGE:
                break
            sqli.test_param(form["action"], form["method"], f["name"], baseline)
            xss.test_param(form["action"], form["method"], f["name"], baseline)

    findings = dedupe(sqli.findings + xss.findings + csrf.findings + idor.findings
                      + leak.findings + header.findings + dirlist.findings)
    findings.sort(key=lambda f: -SEVERITY_ORDER.get(f.severity, 0))
    logger.info(f"========== 扫描完成: {target}，共发现 {len(findings)} 个问题 ==========")
    return findings


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

SEVERITY_BADGE = {"严重": "🔴", "高危": "🟠", "中危": "🟡", "低危": "🔵", "信息": "⚪"}


def write_report(findings, target, report_dir, task_name=""):
    os.makedirs(report_dir, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%H%M%S")
    # 统一重新编号，避免各检测器独立编号导致重复
    for i, f in enumerate(findings, 1):
        f.vuln_id = f"VULN-{stamp}-{i:03d}"
    stats = {}
    for f in findings:
        stats[f.severity] = stats.get(f.severity, 0) + 1

    # ---- JSON ----
    payload = {
        "report_meta": {
            "tool": "AutoSRCScan", "version": TOOL_VERSION,
            "generated_at": now, "target": target, "task_name": task_name,
            "finding_count": len(findings), "severity_stats": stats,
            "compliance": "本报告产生于授权安全测试，仅限授权范围内使用",
        },
        "findings": [f.to_dict() for f in findings],
    }
    json_path = os.path.join(report_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    # ---- Markdown ----
    md = []
    md.append(f"# 漏洞检测报告：{target}")
    md.append("")
    md.append(f"- 任务名称：{task_name or '-'}")
    md.append(f"- 生成时间：{now}")
    md.append(f"- 检测工具：AutoSRCScan v{TOOL_VERSION}")
    md.append(f"- 问题总数：**{len(findings)}**"
              + "".join(f"　{SEVERITY_BADGE.get(k,'')}{k}:{v}" for k, v in sorted(stats.items(), key=lambda x: -SEVERITY_ORDER.get(x[0], 0))))
    md.append("")
    md.append("> ⚠️ 本报告产生于**授权安全测试**，请在授权范围内使用与传播。")
    md.append("")
    md.append("## 风险概览")
    md.append("")
    md.append("| 编号 | 漏洞名称 | 风险等级 | 类型 | 位置 | 参数 |")
    md.append("| --- | --- | --- | --- | --- | --- |")
    for f in findings:
        md.append(f"| {f.vuln_id} | {f.name} | {SEVERITY_BADGE.get(f.severity,'')}{f.severity} | {f.vuln_type} | {f.url} | {f.param or '-'} |")
    md.append("")
    for i, f in enumerate(findings, 1):
        md.append(f"## {i}. {SEVERITY_BADGE.get(f.severity,'')} [{f.severity}] {f.name}")
        md.append("")
        md.append(f"- **漏洞编号**：{f.vuln_id}")
        md.append(f"- **漏洞类型**：{f.vuln_type}")
        md.append(f"- **风险等级**：{f.severity}")
        md.append(f"- **目标地址**：{f.url}（{f.method}，参数 `{f.param or '-'}`）")
        md.append(f"- **发现时间**：{f.discovered_at}")
        md.append("")
        md.append("**复现步骤**")
        for s in (f.repro_steps or []):
            md.append(f"1. {s}")
        md.append("")
        md.append(f"**证据**：`{f.evidence}`")
        md.append("")
        md.append(f"**影响范围分析**：{f.impact}")
        md.append("")
        md.append(f"**修复建议**：{f.fix}")
        md.append("")
        md.append("---")
        md.append("")
    md_path = os.path.join(report_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))

    # ---- HTML ----
    html_path = os.path.join(report_dir, "report.html")
    with open(html_path, "w", encoding="utf-8") as fp:
        fp.write(_render_html(payload))
    return md_path, json_path, html_path


SEV_COLOR = {"严重": "#c0392b", "高危": "#d35400", "中危": "#b7950b", "低危": "#2471a3", "信息": "#7f8c8d"}
SEV_BG = {"严重": "#fdecea", "高危": "#fdebd0", "中危": "#fdf3d0", "低危": "#eaf2f8", "信息": "#f2f3f4"}


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _render_html(payload):
    meta, findings = payload["report_meta"], payload["findings"]
    stats = meta.get("severity_stats", {})
    cards = "".join(
        f'<div class="card"><div class="num" style="color:{SEV_COLOR.get(k,"#333")}">{v}</div>'
        f'<div class="lbl">{SEVERITY_BADGE.get(k,"")}{_esc(k)}</div></div>'
        for k, v in sorted(stats.items(), key=lambda x: -SEVERITY_ORDER.get(x[0], 0))
    )
    rows = "".join(
        f'<tr><td>{_esc(f["vuln_id"])}</td><td>{_esc(f["name"])}</td>'
        f'<td><span class="pill" style="background:{SEV_BG.get(f["severity"]," #eee")};color:{SEV_COLOR.get(f["severity"],"#333")}">{_esc(f["severity"])}</span></td>'
        f'<td>{_esc(f["vuln_type"])}</td><td class="mono">{_esc(f["url"])}</td><td class="mono">{_esc(f["param"] or "-")}</td></tr>'
        for f in findings
    )
    details = "".join(
        f'''<div class="finding" style="border-left:4px solid {SEV_COLOR.get(f["severity"],"#999")}">
<h3>{SEVERITY_BADGE.get(f["severity"],"")} {_esc(f["name"])} <span class="pill" style="background:{SEV_BG.get(f["severity"],"#eee")};color:{SEV_COLOR.get(f["severity"],"#333")}">{_esc(f["severity"])}</span></h3>
<table class="kv"><tbody>
<tr><th>漏洞编号</th><td>{_esc(f["vuln_id"])}</td></tr>
<tr><th>类型 / 等级</th><td>{_esc(f["vuln_type"])} / {_esc(f["severity"])}</td></tr>
<tr><th>目标地址</th><td class="mono">{_esc(f["url"])}（{_esc(f["method"])}，参数 {_esc(f["param"] or "-")}）</td></tr>
<tr><th>发现时间</th><td>{_esc(f["discovered_at"])}</td></tr>
<tr><th>复现步骤</th><td><ol>{''.join(f"<li>{_esc(s)}</li>" for s in f["repro_steps"])}</ol></td></tr>
<tr><th>证据</th><td class="mono">{_esc(f["evidence"])}</td></tr>
<tr><th>影响范围分析</th><td>{_esc(f["impact"])}</td></tr>
<tr><th>修复建议</th><td>{_esc(f["fix"])}</td></tr>
</tbody></table></div>'''
        for f in findings
    )
    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>漏洞检测报告 - {_esc(meta["target"])}</title>
<style>
body{{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f5f6f8;color:#2c3e50}}
.wrap{{max-width:960px;margin:0 auto;padding:24px}}
header{{background:linear-gradient(135deg,#1a5276,#2874a6);color:#fff;border-radius:12px;padding:24px 28px}}
header h1{{margin:0 0 8px;font-size:22px}}
.meta{{font-size:13px;opacity:.92;line-height:1.8}}
.cards{{display:flex;gap:12px;margin:18px 0;flex-wrap:wrap}}
.card{{flex:1;min-width:100px;background:#fff;border-radius:10px;text-align:center;padding:14px 8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.num{{font-size:28px;font-weight:700}}
.lbl{{font-size:13px;color:#666;margin-top:4px}}
.notice{{background:#fef9e7;border:1px solid #f7dc6f;border-radius:8px;padding:10px 14px;font-size:13px;margin:12px 0}}
section{{background:#fff;border-radius:12px;padding:20px 24px;margin:16px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h2{{font-size:17px;border-bottom:2px solid #2874a6;padding-bottom:8px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #e5e8e8;padding:8px 10px;text-align:left;vertical-align:top}}
thead th{{background:#eaf2f8}}
.mono{{font-family:Consolas,monospace;font-size:12px;word-break:break-all}}
.pill{{display:inline-block;border-radius:12px;padding:1px 10px;font-size:12px;font-weight:600}}
.finding{{margin:18px 0;padding:14px 18px;background:#fbfcfc;border-radius:8px}}
.finding h3{{margin:0 0 10px;font-size:15px}}
.kv th{{width:110px;background:#f4f6f7;font-weight:600;white-space:nowrap}}
footer{{text-align:center;font-size:12px;color:#999;padding:16px}}
</style></head><body><div class="wrap">
<header><h1>漏洞检测报告</h1><div class="meta">
目标：{_esc(meta["target"])}<br>
任务：{_esc(meta["task_name"] or "-")}　|　生成时间：{_esc(meta["generated_at"])}　|　工具：AutoSRCScan v{_esc(meta["version"])}<br>
问题总数：<b>{meta["finding_count"]}</b></div></header>
<div class="notice">⚠️ 本报告产生于<strong>授权安全测试</strong>，请在授权范围内使用与传播。</div>
<div class="cards">{cards}</div>
<section><h2>风险概览</h2><table><thead><tr><th>编号</th><th>漏洞名称</th><th>风险等级</th><th>类型</th><th>位置</th><th>参数</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>漏洞详情</h2>{details}</section>
<footer>AutoSRCScan · 授权漏洞检测 · {meta["compliance"]}</footer>
</div></body></html>'''


# ---------------------------------------------------------------------------
# 授权范围校验
# ---------------------------------------------------------------------------

def normalize_origin(u):
    s = urlparse.urlsplit(u if "://" in u else "http://" + u)
    return s.netloc.lower()


def check_authorization(scope_conf, target, logger):
    if not scope_conf.get("authorization_confirmed"):
        logger.error("授权未确认：scope 文件中 authorization_confirmed 必须为 true（需持有书面授权）。")
        return False
    scope_list = scope_conf.get("scope") or []
    t_origin = normalize_origin(target)
    for s in scope_list:
        if normalize_origin(s) == t_origin:
            logger.info(f"授权校验通过：{target} ∈ {s}")
            return True
    logger.error(f"授权校验失败：{target} 不在授权范围 {scope_list} 内，已拒绝扫描。")
    return False


def load_json(path, what):
    if not os.path.exists(path):
        print(f"[错误] {what}文件不存在: {path}")
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


# ---------------------------------------------------------------------------
# 批量任务管理
# ---------------------------------------------------------------------------

def run_batch(tasks_path, base_dir, logger):
    conf = load_json(tasks_path, "任务")
    if not conf.get("authorization_confirmed"):
        logger.error("任务文件中 authorization_confirmed 不为 true，批量任务拒绝执行。")
        return
    tasks = conf.get("tasks", [])
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for t in tasks:
        if t.get("status") not in (None, "pending"):
            logger.info(f"任务 {t['id']}({t.get('name','')}) 状态为 {t['status']}，跳过。")
            continue
        logger.info(f">>> 批量任务 {t['id']}（{t.get('name','')}）开始")
        t["status"], t["started_at"] = "running", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_tasks(tasks_path, conf)
        total = []
        ok_targets = []
        for target in t.get("targets", []):
            if not check_authorization(conf, target, logger):
                t["status"] = "blocked(未授权目标)"
                continue
            try:
                findings = scan_target(target, logger)
            except Exception as e:
                logger.error(f"扫描 {target} 异常: {type(e).__name__}: {e}")
                findings = []
            total.extend(findings)
            ok_targets.append(target)
        rdir = os.path.join(base_dir, "reports", stamp + "_" + t["id"])
        if ok_targets:
            write_report(total, ", ".join(ok_targets), rdir, t.get("name", ""))
            t["report_dir"] = rdir
            t["finding_count"] = len(total)
        t["status"], t["finished_at"] = "done", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_tasks(tasks_path, conf)
        logger.info(f"<<< 任务 {t['id']} 完成：{len(total)} 个问题，报告: {rdir}")
    logger.info("全部批量任务执行完毕。")


def _save_tasks(tasks_path, conf):
    with open(tasks_path, "w", encoding="utf-8") as fp:
        json.dump(conf, fp, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="AutoSRCScan 授权漏洞扫描器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="单目标扫描")
    p_scan.add_argument("--scope", required=True, help="授权范围文件 scope.json")
    p_scan.add_argument("--target", required=True, help="目标URL，如 http://127.0.0.1:8777")
    p_scan.add_argument("--name", default="", help="任务名称")

    p_batch = sub.add_parser("batch", help="批量任务")
    p_batch.add_argument("--tasks", required=True, help="任务文件 tasks.json")

    p_list = sub.add_parser("tasks", help="查看任务状态")
    p_list.add_argument("--tasks", required=True)

    args = parser.parse_args()
    logger = build_logger(os.path.join(base_dir, "logs"))

    if args.cmd == "scan":
        scope = load_json(args.scope, "授权范围")
        if not check_authorization(scope, args.target, logger):
            sys.exit(3)
        findings = scan_target(args.target, logger)
        rdir = os.path.join(
            base_dir, "reports",
            datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + normalize_origin(args.target).replace(":", "_"),
        )
        md, js, html = write_report(findings, args.target, rdir, args.name)
        logger.info(f"报告已生成: {md} / {js} / {html}")
    elif args.cmd == "batch":
        run_batch(args.tasks, base_dir, logger)
    elif args.cmd == "tasks":
        conf = load_json(args.tasks, "任务")
        for t in conf.get("tasks", []):
            print(f"{t['id']}\t{t.get('status','pending')}\t{t.get('name','')}\t"
                  f"findings={t.get('finding_count','-')}\t{t.get('report_dir','-')}")


if __name__ == "__main__":
    main()
