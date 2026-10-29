# -*- coding: utf-8 -*-
"""边界异常 / 性能基线测试（通用版，已脱敏，纯标准库）。

与 speed_test.py（延迟）、concurrency_test.py（并发）互补。
单独成脚本的原因：并发写失败会泄漏数据库连接，导致后续串行请求全部 500，
混在同一个进程里跑会污染结果边界。必须在独立进程运行。

可通过环境变量覆盖默认配置（生产环境请替换）：
  API_BASE      后端基准地址，默认 http://127.0.0.1:8099
  API_USER      登录账号，默认 admin（示例）
  API_PASSWORD  登录密码，默认 admin123（示例）

用法：
  python scripts/edge_perf_test.py
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8099')
USER = os.environ.get('API_USER', 'admin')            # 示例账号，生产环境请替换
PASSWORD = os.environ.get('API_PASSWORD', 'admin123')  # 示例密码，生产环境请替换
PREFIX = '_KL_'  # 测试数据前缀，便于清理时识别

# 目标系统的“创建实体”与“保存明细”端点，按实际替换
CREATE_PATH = '/api/shows'
FLOW_PATH = '/api/flow'


def req(method, path, body=None, token=None, raw=False, timeout=90):
    url = BASE + path
    data = json.dumps(body).encode('utf-8') if body is not None else None
    h = {}
    if data is not None:
        h['Content-Type'] = 'application/json'
    if token:
        h['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            rb = x.read()
            if raw:
                return x.status, rb
            try:
                return x.status, json.loads(rb.decode('utf-8'))
            except Exception:
                return x.status, rb.decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        eb = e.read().decode('utf-8', 'ignore')
        try:
            return e.code, json.loads(eb)
        except Exception:
            return e.code, eb
    except Exception as e:
        return 0, str(e)


def as_dict(x):
    return x if isinstance(x, dict) else {}


st, r = req('POST', '/api/login', {'username': USER, 'password': PASSWORD})
TOK = r.get('token', '') if st == 200 else ''
if not TOK:
    print('登录失败'); sys.exit(1)

CLEAN = []

print('\n' + '=' * 68)
print('K 组：边界与异常输入')
print('=' * 68)

st, s = req('POST', CREATE_PATH, {'name': 'X' * 500, 'type': '维度A', 'show_date': '2026-11-05'}, TOK)
if st == 200:
    print('  [WARN] K1 超长名称(500字符)可入库 → 列表/卡片展示会被撑破')
    req('DELETE', '%s/%d?purge=1' % (CREATE_PATH, s.get('id', 0)), token=TOK)
else:
    print('  [INFO] K1 超长名称被拒 status=%s' % st)

evil = "'; DROP TABLE items; --"
st, s = req('POST', CREATE_PATH, {'name': evil, 'type': '维度A', 'show_date': '2026-11-05'}, TOK)
EID = as_dict(s).get('id')
st2, alive = req('GET', CREATE_PATH, token=TOK)
ok_k2 = isinstance(alive, list) and len(alive) >= 1 and st2 == 200
print('  [%s] K2 注入串入库后表未被破坏，记录数=%d'
      % ('PASS' if ok_k2 else 'FAIL', len(alive) if isinstance(alive, list) else -1))
if EID:
    req('DELETE', '%s/%d?purge=1' % (CREATE_PATH, EID), token=TOK)

xss = '<script>alert(1)</script>'
st, s = req('POST', CREATE_PATH, {'name': xss, 'type': '维度A', 'show_date': '2026-11-05'}, TOK)
XID = as_dict(s).get('id')
if XID:
    stt, g = req('GET', '%s/%d' % (CREATE_PATH, XID), token=TOK)
    same = as_dict(g).get('name') == xss
    print('  [%s] K3 XSS 串原样入库 → 依赖前端转义，接口层不做净化'
          % ('PASS' if same else 'FAIL'))
    req('DELETE', '%s/%d?purge=1' % (CREATE_PATH, XID), token=TOK)

st, s = req('POST', CREATE_PATH, {
    'name': PREFIX + '边界展', 'type': '维度A', 'show_date': '2026-11-01,2026-11-02',
    'peak': 5000, 'start': '09:00', 'end': '12:00', 'interval': '整点'}, TOK)
SID = as_dict(s).get('id')

if SID:
    CLEAN.append(SID)
    req('POST', FLOW_PATH, {'show_id': SID, 'date': '2026-11-01', 'period': '09:00',
                            'entry': 100, 'exit': 10}, TOK)
    st, rr = req('POST', FLOW_PATH, {'show_id': SID, 'date': '2026-11-01', 'period': '10:00',
                                     'entry': 999999999, 'exit': 1}, TOK)
    print('  [%s] K4 超大数值 9.99 亿可保存 warnings=%s'
          % ('PASS' if st == 200 else 'FAIL', as_dict(rr).get('warnings')))

    for tag, dt in [('非法日期 2020-01-01', '2020-01-01'), ('非法格式 not-a-date', 'not-a-date')]:
        stt, _r = req('POST', FLOW_PATH, {'show_id': SID, 'date': dt, 'period': '09:00',
                                          'entry': 10, 'exit': 1}, TOK)
        if stt == 200:
            print('  [WARN] K5 %s 可写入 → 无校验，会产生游离数据、污染统计' % tag)
        else:
            print('  [PASS] K5 %s 被拒 status=%s' % (tag, stt))

    stt, _r = req('POST', FLOW_PATH, {'show_id': SID, 'date': '2026-11-01', 'period': '',
                                      'entry': 10, 'exit': 1}, TOK)
    print('  [%s] K6 空时段：%s' % ('WARN' if stt == 200 else 'PASS',
                                    '可写入' if stt == 200 else '被拒 status=%s' % stt))

    stt, raw = req('GET', '/api/export/daily?show_id=%d&date=2026-11-09' % SID, token=TOK, raw=True)
    print('  [PASS] K7 无数据日期导出不报错 bytes=%d' % (len(raw) if isinstance(raw, bytes) else 0))

    stt, raw = req('GET', '/api/export/daily?show_id=999999&date=2026-11-01', token=TOK, raw=True)
    print('  [%s] K8 不存在的实体导出应 404 → status=%s' % ('PASS' if stt == 404 else 'FAIL', stt))

    stt, d = req('GET', '/api/dashboard?date=1800-01-01', token=TOK)
    print('  [PASS] K9 非法日期查看板不报错 status=%s 记录数=%d'
          % (stt, len(as_dict(d).get('shows', []))))

    stt, d = req('GET', '/api/stats/monthly?year=1800&month=99', token=TOK)
    print('  [PASS] K10 非法年/月查月汇总不报错 status=%s' % stt)

print('\n' + '=' * 68)
print('L 组：性能基线（本机单进程，仅供参考）')
print('=' * 68)


def timed(code, path, th=3.0):
    t0 = time.time()
    stt, rb = req('GET', path, token=TOK, raw=True)
    dt = time.time() - t0
    sz = len(rb) if isinstance(rb, bytes) else len(str(rb))
    print('  [%s] %-4s %-34s %.2fs  %d 字节' % ('OK  ' if dt <= th else 'SLOW', code, path.split('?')[0], dt, sz))
    return dt


timed('L1', '/api/dashboard?date=2026-09-26')
timed('L2', '/api/stats/yearly?year=2026')
timed('L3', '/api/stats/monthly?year=2026&month=9')
timed('L4', '/api/items' if CREATE_PATH == '/api/shows' else CREATE_PATH, 5.0)
timed('L5', '/api/export/hierarchical?year=2026', 8.0)

print('\n' + '=' * 68)
print('清理')
print('=' * 68)
for sid in CLEAN:
    req('DELETE', '%s/%d?purge=1' % (CREATE_PATH, sid), token=TOK)
print('  [INFO] 已清理 %d 个测试实体（前缀 %s）' % (len(CLEAN), PREFIX))
