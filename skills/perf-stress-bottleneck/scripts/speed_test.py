# -*- coding: utf-8 -*-
"""API 端到端延迟实测（通用版，已脱敏，纯标准库）。

模拟客户端真实调用链：登录拿 token → 各业务端点采样 N 次取 min/avg/max。
运行前需后端已在 API_BASE 监听。

可通过环境变量覆盖默认配置（生产环境请替换）：
  API_BASE      后端基准地址，默认 http://127.0.0.1:8099
  API_USER      登录账号，默认 admin（示例）
  API_PASSWORD  登录密码，默认 admin123（示例）

用法：
  python scripts/speed_test.py
"""
import os
import time
import json
import statistics
import urllib.request
import urllib.error
import urllib.parse

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8099')
USER = os.environ.get('API_USER', 'admin')          # 示例账号，生产环境请替换
PASSWORD = os.environ.get('API_PASSWORD', 'admin123')  # 示例密码，生产环境请替换

# 待测端点：按目标系统实际路由替换（method, path, params）
ENDPOINTS = [
    ("GET", "/api/dashboard", {"date": "2026-09-29", "dim": "汇总"}),
    ("GET", "/api/stats/monthly", {"year": 2026, "month": 9, "dim": "汇总"}),
    ("GET", "/api/stats/yearly", {"year": 2026, "dim": "汇总"}),
    ("GET", "/api/users", None),
    ("GET", "/api/config", None),
    ("POST", "/api/flow", {"show_id": 1, "date": "2026-09-29", "period": "09:00-10:00",
                           "entry": 100, "exit": 90, "extra": 0}),
]

trust_env = False  # 绕过 HTTP(S)_PROXY，避免把 localhost 发往代理


def _open(method, path, params=None, body=None, headers=None):
    url = BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    h = dict(headers or {})
    if data is not None:
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    if not trust_env:
        req.add_header('Cache-Control', 'no-store')
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


def bench(label, method, path, params=None, body=None, n=5, headers=None):
    xs, code = [], None
    for _ in range(n):
        t = time.perf_counter()
        try:
            code, _ = _open(method, path, params, body, headers)
            xs.append((time.perf_counter() - t) * 1000)
        except Exception as e:
            code = 'ERR:%s' % e
            break
    if xs:
        print('%-22s %-30s code=%-4s min=%6.1fms avg=%6.1fms max=%6.1fms' % (
            label, path, code, min(xs), statistics.mean(xs), max(xs)))
    else:
        print('%-22s %-30s code=%s  (请求失败)' % (label, path, code))


print('=== 登录 ===')
t = time.perf_counter()
st, _ = _open('POST', '/api/login', body={'username': USER, 'password': PASSWORD})
tok = None
if st == 200:
    # 部分系统登录返回 token 字段，按需适配
    try:
        tok = json.loads(_open('POST', '/api/login', body={'username': USER, 'password': PASSWORD})[1]).get('token')
    except Exception:
        tok = 'dummy'
print('login code=%s  %.1fms' % (st, (time.perf_counter() - t) * 1000))
hdr = {'Authorization': 'Bearer ' + (tok or '')}

print('\n=== 业务端点延迟（各采样 5 次） ===')
for i, (m, p, params) in enumerate(ENDPOINTS):
    bench('端点%d' % (i + 1), m, p, params, body=(params if m == 'POST' else None), headers=hdr)
