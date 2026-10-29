#!/usr/bin/env node
/**
 * log-rca-timeline / 日志分析与故障根因定位 —— 看板交付前自检
 *
 * 用法: node render_check.js <report.html> [输出报告路径]
 * 退出码 0 = 通过；1 = 存在 ERROR
 *
 * 静态检查之外，用最小 DOM 桩真实执行内联渲染脚本，并主动触发
 * 筛选 / 搜索 / 序列切换三条交互路径，防止「静态全绿、页面空白」。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = process.argv[2];
const outFile = process.argv[3] || null;
const lines = [];
let hasError = false;

function log(s) { lines.push(s); }
function ok(name) { log('  [PASS] ' + name); }
function fail(name, detail) { hasError = true; log('  [FAIL] ' + name + (detail ? ' -> ' + detail : '')); }
function warnl(name, detail) { log('  [WARN] ' + name + (detail ? ' -> ' + detail : '')); }

if (!file) {
  console.error('用法: node render_check.js <report.html> [out.txt]');
  process.exit(2);
}

let html;
try {
  html = fs.readFileSync(file, 'utf8');
} catch (e) {
  console.error('[FATAL] 读取失败: ' + e.message);
  process.exit(2);
}

log('日志分析与故障根因定位 · 看板自检');
log('文件: ' + path.resolve(file) + '  ' + (html.length / 1024).toFixed(0) + 'KB');

// ---------- 1. 占位符 ----------
log('\n== 1. 占位符替换 ==');
const ph = [...new Set((html.match(/__[A-Z][A-Z_]*__/g) || []))].filter(x => x !== '__LRT_DATA__');
ph.length === 0 ? ok('无占位符残留') : fail('存在未替换占位符', ph.join(','));

// ---------- 2. 文档结构 ----------
log('\n== 2. 文档结构 ==');
['<!DOCTYPE html>', '<html lang="zh-CN">', '</html>'].forEach(t => {
  html.includes(t) ? ok('包含 ' + t) : fail('缺失 ' + t);
});
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
scripts.length >= 2 ? ok('含 payload 与渲染两段脚本 (' + scripts.length + ')')
                    : fail('脚本段数不足', String(scripts.length));
const bodyOnly = html.replace(/<script[\s\S]*?<\/script>/g, '');

// ---------- 3. 合规与结论锚点 ----------
log('\n== 3. 结论性质锚点 ==');
[
  ['假设排序声明', '根因假设排序'],
  ['时间窗聚合', '时间窗聚合序列'],
  ['故障时间线', '故障时间线'],
  ['待核验项', '待核验项与分析局限'],
  ['验证动作', '验证动作'],
  ['可排除条件', '可排除条件'],
  ['方法论参考', '方法论参考'],
  ['复盘要点', '复盘与处置要点'],
].forEach(([name, kw]) => {
  html.includes(kw) ? ok('含「' + name + '」') : fail('缺失「' + name + '」', kw);
});

// ---------- 4. payload ----------
log('\n== 4. 数据负载 ==');
let data = null;
const pm = html.match(/window\.__LRT_DATA__\s*=\s*(\{[\s\S]*?\});\s*/);
if (!pm) {
  fail('未找到 window.__LRT_DATA__ 数据负载');
} else {
  ok('数据负载存在');
  try {
    data = JSON.parse(pm[1].replace(/<\\\//g, '</'));
    const bk = data.buckets || [], pt = data.patterns || [], hy = data.hypotheses || [];
    log('     时间窗 ' + bk.length + ' | 异常模式 ' + pt.length + ' | 假设 ' + hy.length
        + ' | 时间线 ' + ((data.timeline || []).length));
    bk.length > 0 ? ok('buckets 非空') : fail('buckets 为空');
    (data.flags || []).length > 0 ? ok('flags 非空') : fail('flags 为空');
    (data.timeline || []).length > 0 ? ok('timeline 非空') : fail('timeline 为空');
    let noUnit = 0, badSev = 0;
    pt.forEach(p => {
      if (p.unit === undefined) noUnit++;
      if (['高', '中', '低'].indexOf(p.severity) < 0) badSev++;
    });
    noUnit === 0 ? ok('全部模式含 unit') : fail('存在缺 unit 的模式', String(noUnit));
    badSev === 0 ? ok('严重度枚举合法') : fail('严重度枚举越界', String(badSev));
    let badScore = 0;
    hy.forEach(h => { if (!(typeof h.score === 'number' && h.score >= 0 && h.score <= 100)) badScore++; });
    badScore === 0 ? ok('假设分数均在 0-100') : fail('假设分数越界', String(badScore));
  } catch (e) {
    fail('数据负载 JSON 解析失败', e.message);
  }
}

// ---------- 5. 最小 DOM 桩执行 ----------
log('\n== 5. 渲染执行（最小 DOM 桩） ==');
if (!data || scripts.length < 2) {
  fail('缺少数据或脚本，跳过渲染执行');
} else {
  const nodes = {};
  const docHandlers = {};
  function mkEl(id) {
    if (nodes[id]) return nodes[id];
    const cls = new Set();
    nodes[id] = {
      id,
      innerHTML: '',
      textContent: '',
      dataset: {},
      style: {},
      classList: {
        add: c => cls.add(c), remove: c => cls.delete(c),
        contains: c => cls.has(c),
        toggle: (c, on) => { if (on === undefined) { cls.has(c) ? cls.delete(c) : cls.add(c); } else { on ? cls.add(c) : cls.delete(c); } },
      },
      _handlers: {},
      addEventListener: function (ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); },
      querySelectorAll: () => [],
      appendChild: () => {},
      _cls: cls,
    };
    return nodes[id];
  }
  const document = {
    getElementById: id => mkEl(id),
    addEventListener: (ev, fn) => { (docHandlers[ev] = docHandlers[ev] || []).push(fn); },
    querySelectorAll: () => [],
    createElement: () => mkEl('_tmp_' + Math.random()),
  };
  const window = {};
  const sandbox = { window, document, console: { log: () => {}, warn: () => {}, error: () => {} }, Math, JSON, Object, Array, String, Number, Date, isNaN, parseInt, parseFloat };
  vm.createContext(sandbox);
  try {
    vm.runInContext(scripts[0], sandbox, { timeout: 10000 });
    vm.runInContext(scripts[1], sandbox, { timeout: 15000 });
    ok('内联脚本执行无异常');
  } catch (e) {
    fail('内联脚本执行抛异常', e.message);
  }

  const bad = /undefined|NaN|\[object Object\]/;
  // 无异常 / 无假设时，patGrid 与 hypList 允许为空（由空状态卡片接管）
  const mayBeEmpty = {
    patGrid: (data.patterns || []).length === 0,
    hypList: (data.hypotheses || []).length === 0,
  };
  ['stats', 'summary', 'chartbox', 'patGrid', 'hypList', 'timeline', 'flags', 'srcList'].forEach(id => {
    const el = nodes[id];
    if (!el) { fail('未渲染容器 #' + id); return; }
    const txt = el.innerHTML || el.textContent || '';
    if (!txt || txt.length < 10) {
      if (mayBeEmpty[id]) { ok('容器 #' + id + ' 为空（数据本身为空，符合预期）'); return; }
      fail('容器 #' + id + ' 渲染为空', String(txt.length));
      return;
    }
    if (bad.test(txt)) fail('容器 #' + id + ' 含 undefined/NaN', txt.slice(0, 120));
    else ok('容器 #' + id + ' 已渲染且无 undefined/NaN');
  });
  const chart = (nodes['chartbox'] || {}).innerHTML || '';
  chart.indexOf('<svg') >= 0 ? ok('聚合图已输出 SVG') : fail('聚合图缺少 SVG');
  (chart.match(/<rect/g) || []).length > 3 ? ok('聚合图含数据柱')
                                           : warnl('聚合图柱子偏少', String((chart.match(/<rect/g) || []).length));

  // ---------- 6. 交互路径 ----------
  log('\n== 6. 交互路径 ==');
  function fireClick(dataset) {
    const chip = { dataset, closest: () => chip, classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false } };
    (docHandlers['click'] || []).forEach(fn => fn({ target: chip }));
  }
  try {
    fireClick({ sev: '高' });
    ok('严重度筛选执行无异常');
    const afterSev = (nodes['patGrid'] || {}).innerHTML || '';
    if (bad.test(afterSev)) fail('严重度筛选后出现 undefined/NaN', afterSev.slice(0, 120));
    else ok('严重度筛选结果正常');
    fireClick({ sev: '' });
    const types = [...new Set((data.patterns || []).map(p => p.type))];
    if (types.length) {
      fireClick({ type: types[0] });
      ok('类型筛选执行无异常');
      fireClick({ type: '' });
    }
    fireClick({ sort: 'time' });
    ok('排序切换执行无异常');
    ['errors', 'error_rate', 'p95', 'total'].forEach(s => {
      fireClick({ series: s });
      const c = (nodes['chartbox'] || {}).innerHTML || '';
      if (c.indexOf('<svg') < 0) fail('序列 ' + s + ' 切换后图表丢失');
    });
    ok('四种序列切换均正常');
    const q = nodes['q'];
    if (q && (q._handlers['input'] || []).length) {
      q._handlers['input'].forEach(fn => fn({ target: { value: 'error' } }));
      ok('搜索框输入执行无异常');
      q._handlers['input'].forEach(fn => fn({ target: { value: '' } }));
    } else {
      warnl('搜索框未绑定 input 事件');
    }
  } catch (e) {
    fail('交互路径抛异常', e.message);
  }
}

// ---------- 7. 外链 ----------
log('\n== 7. 外链检查 ==');
const hrefs = [...new Set((html.match(/https?:\/\/[^"'\\\s)<>,]+/g) || []))];
log('     链接 ' + hrefs.length + ' 个（含数据负载内）');
const nonHttps = hrefs.filter(u => u.indexOf('http://') === 0);
nonHttps.length === 0 ? ok('全部为 https 链接') : warnl('存在 http 链接', nonHttps.slice(0, 3).join(','));

log('\n' + (hasError ? '结果: 存在 ERROR，请先修复后再交付。' : '结果: 全部通过，可交付。'));
const text = lines.join('\n');
console.log(text);
if (outFile) {
  try { fs.writeFileSync(outFile, text, 'utf8'); } catch (e) { /* ignore */ }
}
process.exit(hasError ? 1 : 0);
