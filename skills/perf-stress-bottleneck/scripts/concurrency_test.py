# -*- coding: utf-8 -*-
"""并发写入专项测试（通用版，已脱敏，纯标准库）。

验证多个客户端同时保存时：全部成功、无重复记录、失败后服务仍健康。
必须在独立进程运行（与边界测试分开）。

可通过环境变量覆盖默认配置（生产环境请替换）：
  API_BASE      后端基准地址，默认 http://127.0.0.1:8099
  API_USER      登录账号，默认 admin（示例）
  API_PASSWORD  登录密码，默认 admin123（示例）

用法：
  python scripts/concurrency_test.py
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8099')
USER = os.environ.get('API_USER', 'admin')             # 示例账号，生产环境请替换
PASSWORD = os.environ.get('API_PASSWORD', 'admin123')   # 示例密码，生产环境请替换
NAME = '_CONC_并发验证展'  # 测试实体名（带前缀便于清理）
CREATE_PATH = '/api/shows'
FLOW_PATH = '/api/flow'


def req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    h = {}
    if data is not None:
        h['Content-Type'] = 'application/json'
    if token:
        h['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            raw = x.read().decode('utf-8')
            try:
                return x.status, json.loads(raw)
            except Exception:
                return x.status, raw
    except urllib.error.HTTPError as e:
        eb = e.read().decode('utf-8', 'ignore')
        try:
            return e.code, json.loads(eb)
        except Exception:
            return e.code, eb
    except Exception as e:
        return 0, str(e)


def D(x):
    return x if isinstance(x, dict) else {}


st, r = req('POST', '/api/login', {'username': USER, 'password': PASSWORD})
TOK = D(r).get('token', '')
assert TOK, '登录失败'

print('=' * 62)
print('并发写入专项测试')
print('=' * 62)

st, s = req('POST', CREATE_PATH, {
    'name': NAME, 'type': '维度A', 'show_date': '2026-11-01,2026-11-02',
    'peak': 50000, 'start': '09:00', 'end': '18:00', 'interval': '整点'}, TOK)
SID = D(s).get('id')
print('测试实体 id=%s' % SID)

D8 = '2026-11-01'
periods = ['%02d:00' % h for h in range(9, 19)]

# --- 场景1：10 人同时录不同时段 ---
def save_distinct(i):
    return req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': periods[i],
                                  'entry': 100 + i, 'exit': 10 * i}, TOK)[0]


t0 = time.time()
with ThreadPoolExecutor(max_workers=10) as ex:
    r1 = list(ex.map(save_distinct, range(10)))
dt1 = time.time() - t0
n1 = sum(1 for x in r1 if x == 200)
print('%-38s %2d/10  %.2fs  %s' % ('场景1 10 并发录不同时段', n1, dt1, sorted(set(r1))))

# --- 场景2：8 人同时改同一时段 ---
def save_same(i):
    return req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': '09:00',
                                  'entry': 500, 'exit': 50}, TOK)[0]


t0 = time.time()
with ThreadPoolExecutor(max_workers=8) as ex:
    r2 = list(ex.map(save_same, range(8)))
dt2 = time.time() - t0
n2 = sum(1 for x in r2 if x == 200)
print('%-38s %2d/8   %.2fs  %s' % ('场景2  8 并发改同一时段', n2, dt2, sorted(set(r2))))

# --- 场景3：混合读+写并发 ---
def mixed(i):
    if i % 2:
        return req('GET', '/api/dashboard?date=%s' % D8, token=TOK)[0]
    return req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': periods[i],
                                  'entry': 200 + i, 'exit': 5 * i}, TOK)[0]


t0 = time.time()
with ThreadPoolExecutor(max_workers=10) as ex:
    r3 = list(ex.map(mixed, range(10)))
dt3 = time.time() - t0
n3 = sum(1 for x in r3 if x == 200)
print('%-38s %2d/10  %.2fs' % ('场景3 读写混合并发', n3, dt3))

# --- 结果完整性 ---
st, rows = req('GET', '/api/flow/daily?show_id=%d&date=%s' % (SID, D8), token=TOK)
rows = rows if isinstance(rows, list) else []
dup_rows = len(rows) - len({x['period'] for x in rows if isinstance(x, dict)})
print('%-38s 实际 %d 条 / 时段 %d 个，重复 %d 条' % ('记录完整性', len(rows), len(periods), dup_rows))

# --- 幂等 / 离线补传 ---
a = req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': '10:00', 'entry': 777, 'exit': 66}, TOK)
b = req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': '10:00', 'entry': 777, 'exit': 66}, TOK)
print('%-38s %s' % ('重复提交幂等', '一致' if D(a[1]).get('inside') == D(b[1]).get('inside') else '不一致'))

for _ in range(3):
    req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': '11:00', 'entry': 321, 'exit': 22}, TOK)
st, rows = req('GET', '/api/flow/daily?show_id=%d&date=%s' % (SID, D8), token=TOK)
hit = [x for x in rows if isinstance(x, dict) and x.get('period') == '11:00']
print('%-38s %d 条' % ('离线补传重放 3 次后记录数', len(hit)))

# --- 服务自愈能力（关键：一个写失败不能拖垮整个服务）---
h = req('GET', '/api/health')[0]
w = req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': '12:00', 'entry': 10, 'exit': 1}, TOK)[0]
print('%-38s health=%s 后续写=%s' % ('并发后服务仍可用', h, w))

# --- 新增输入校验 ---
v1 = req('POST', FLOW_PATH, {'show_id': SID, 'date': 'not-a-date', 'period': '09:00',
                             'entry': 1, 'exit': 1}, TOK)[0]
v2 = req('POST', FLOW_PATH, {'show_id': SID, 'date': D8, 'period': '',
                             'entry': 1, 'exit': 1}, TOK)[0]
print('%-38s 非法日期=%s 空时段=%s (应 400)' % ('输入校验', v1, v2))

# --- 清理 ---
req('DELETE', '%s/%d?purge=1' % (CREATE_PATH, SID), token=TOK)
print('\n结论：%s' % ('并发写入全部通过' if (n1 == 10 and n2 == 8 and n3 == 10 and dup_rows == 0 and len(hit) == 1
         and h == 200 and w == 200 and v1 == 400 and v2 == 400) else '仍有并发失败，见上表'))
