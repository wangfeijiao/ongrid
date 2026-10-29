#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地漏洞靶场 —— 仅用于测试 AutoSRCScan 扫描器的检测能力。
包含：SQL注入(报错/盲注)、反射XSS、CSRF缺失、水平越权、.git泄露、.env泄露、敏感信息泄露。
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

USERS = {
    1: {"id": 1, "name": "张三", "email": "zhangsan@example.com", "phone": "13800138000"},
    2: {"id": 2, "name": "李四", "email": "lisi@example.com", "phone": "13911112222"},
}

GIT_CONFIG = "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n"
ENV_FILE = "DB_PASSWORD=SuperSecret123\nJWT_SECRET=jwt_secret_abc\nSTRIPE_KEY=sk_live_xxx\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}

        if u.path == "/":
            self._send(200, INDEX)
        elif u.path == "/search":
            kw = q.get("q", "")
            if "'" in kw or '"' in kw:
                self._send(200, f"<html><body><h1>查询失败</h1><p>sqlite3.OperationalError: unrecognized token: \"'\"</p></body></html>")
            elif "and '1'='1'" in kw:
                self._send(200, f"<html><body><h1>搜索: {kw}</h1><p>结果1 结果2 结果3 结果4 结果5</p></body></html>")
            elif "and '1'='2'" in kw:
                self._send(200, "<html><body><h1>搜索</h1><p>无结果</p></body></html>")
            else:
                # 转义回显（此处安全）
                safe = kw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                self._send(200, f"<html><body><h1>搜索: {safe}</h1><p>共0条</p></body></html>")
        elif u.path == "/comment":
            self._send(200, COMMENT_PAGE)
        elif u.path == "/transfer":
            self._send(200, TRANSFER_PAGE)   # POST表单，无CSRF token
        elif u.path == "/profile":
            try:
                uid = int(q.get("id", "0"))
            except ValueError:
                uid = 0
            user = USERS.get(uid)
            if user:
                self._send(200, f"<html><body><h1>用户中心</h1><p>姓名: {user['name']}</p>"
                                f"<p>邮箱: {user['email']}</p><p>手机: {user['phone']}</p></body></html>")
            else:
                self._send(404, "<html><body>用户不存在</body></html>")
        elif u.path == "/.git/config":
            self._send(200, GIT_CONFIG)
        elif u.path == "/.env":
            self._send(200, ENV_FILE, ctype="text/plain; charset=utf-8")
        elif u.path == "/api/users":
            data = [{"name": "张三", "phone": "13800138000", "idcard": "11010119900307867X"},
                    {"name": "李四", "phone": "13911112222", "idcard": "110101199203084432"}]
            self._send(200, json.dumps(data), ctype="application/json; charset=utf-8")
        elif u.path == "/static/":
            self._send(200, "<html><head><title>Index of /static/</title></head><body>"
                            "<h1>Index of /static/</h1><ul><li><a href='app.jar'>app.jar</a></li>"
                            "<li><a href='config.bak'>config.bak</a></li></ul></body></html>")
        elif u.path == "/logout":
            self._send(200, "<html><body>再见</body></html>")
        else:
            self._send(404, "<html><body>404 Not Found</body></html>")

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        if u.path == "/comment":
            # 未转义直接回显 → 反射XSS
            self._send(200, f"<html><body><h1>评论区</h1><div class='c'>{form.get('content','')}</div></body></html>")
        elif u.path == "/transfer":
            self._send(200, f"<html><body>已向 {form.get('to','')} 转账 {form.get('amount','')} 元</body></html>")
        else:
            self._send(404, "<html><body>404</body></html>")


INDEX = """<html><head><title>演示商城</title></head><body>
<h1>演示商城首页</h1>
<a href="/search?q=手机">商品搜索</a>
<a href="/comment">用户评论</a>
<a href="/transfer">转账</a>
<a href="/profile?id=1">我的资料</a>
<a href="/api/users">用户接口</a>
<a href="/static/">静态资源</a>
<a href="/logout">退出</a>
<form action="/search" method="GET"><input name="q" value=""><input type="submit" value="搜索"></form>
</body></html>"""

COMMENT_PAGE = """<html><body><h1>评论区</h1>
<form action="/comment" method="POST">
<textarea name="content">说点什么</textarea>
<input type="submit" value="发表">
</form></body></html>"""

TRANSFER_PAGE = """<html><body><h1>转账</h1>
<form action="/transfer" method="POST">
收款人: <input name="to" value="">
金额: <input name="amount" value="100">
<input type="submit" value="确认转账">
</form></body></html>"""


if __name__ == "__main__":
    port = 8777
    print(f"漏洞靶场启动: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
