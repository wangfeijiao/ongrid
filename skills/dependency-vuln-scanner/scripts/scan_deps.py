#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
依赖漏洞扫描器 —— 零依赖，仅用 Python 标准库。

扫描项目的第三方依赖，对照 OSV.dev（Google 免费漏洞库）查出已知 CVE 漏洞，
并给出「最新版本 + 修复命令」。

支持 7 种生态 + 锁文件（锁文件含传递依赖，能查出「依赖的依赖」的漏洞）：
  Python:  requirements.txt / poetry.lock / Pipfile.lock
  Node:    package.json / package-lock.json
  Go:      go.mod
  Java:    pom.xml
  Rust:    Cargo.toml / Cargo.lock
  Ruby:    Gemfile / Gemfile.lock
  PHP:     composer.json / composer.lock

用法:
  python3 scan_deps.py <依赖文件路径>
  python3 scan_deps.py <目录路径>             # 项目模式：递归发现依赖文件并汇总
  python3 scan_deps.py --json <路径>          # 以 JSON 输出（支持文件或目录）
  python3 scan_deps.py --sarif <路径>         # 以 SARIF 2.1.0 输出（支持文件或目录）
  --json 与 --sarif 互斥，不可同时使用。

版本解析:
  扫描直接声明文件（如 package.json、Cargo.toml）时，若同目录存在对应的锁文件
  （如 package-lock.json），会自动读取锁文件以获取实际安装版本，输出更准确的结果。

退出码:
  默认（人类可读）：扫描成功返回 0；无法扫描（文件不存在 / 无法识别格式）返回 1。
  --json / --sarif：即使出错也返回 0，并输出可解析的错误（JSON 含 error 字段 / SARIF 单条 scanner-error），便于上游统一处理。
  注意：是否发现漏洞不影响退出码（始终 0）——本工具只报告，不替调用方决定是否中断构建。
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ==================== 文件格式识别 ====================
# 文件名 -> (OSV 生态, 是否锁文件)
FILE_FORMATS = {
    'requirements.txt': ('PyPI', False),
    'poetry.lock': ('PyPI', True),
    'pipfile.lock': ('PyPI', True),
    'package.json': ('npm', False),
    'package-lock.json': ('npm', True),
    'go.mod': ('Go', False),
    'pom.xml': ('Maven', False),
    'cargo.toml': ('crates.io', False),
    'cargo.lock': ('crates.io', True),
    'gemfile': ('RubyGems', False),
    'gemfile.lock': ('RubyGems', True),
    'composer.json': ('Packagist', False),
    'composer.lock': ('Packagist', True),
}

# ==================== 直接声明文件 → 兄弟锁文件映射 ====================
SIBLING_LOCK_MAP = {
    'package.json': ['package-lock.json', 'npm-shrinkwrap.json'],
    'cargo.toml': ['cargo.lock'],
    'gemfile': ['gemfile.lock'],
    'composer.json': ['composer.lock'],
    'requirements.txt': ['poetry.lock', 'pipfile.lock'],
    # pom.xml, go.mod: 无锁文件
}

# ==================== 项目模式跳过的目录 ====================
SKIP_DIRS = {
    'node_modules', '.git', 'dist', 'build', 'out', 'target',
    '.venv', 'venv', 'env', '__pycache__', '.next', 'coverage', '.cache',
}


# ==================== 依赖解析 ====================
def parse_requirements(text):
    """requirements.txt → [(name, version)]"""
    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue
        line = line.split(';')[0].strip()
        m = re.match(r'([A-Za-z0-9_.-]+)\s*(==|>=|<=|~=|!=|>|<)?\s*([0-9][0-9A-Za-z.]*)?', line)
        if m:
            deps.append((m.group(1), m.group(3) or ''))
    return deps


def parse_package_json(text):
    """package.json → [(name, version)]"""
    data = json.loads(text)
    deps = []
    for section in ('dependencies', 'devDependencies'):
        for name, version in data.get(section, {}).items():
            v = str(version).lstrip('^~')
            if v and v != '*' and v != 'latest':
                deps.append((name, v))
    return deps


def parse_package_lock(text):
    """package-lock.json → [(name, version)]（含传递依赖）"""
    data = json.loads(text)
    deps = []
    for key, info in data.get('packages', {}).items():
        name = key.split('node_modules/')[-1]
        if name and info.get('version'):
            deps.append((name, info['version']))
    if not deps:
        for name, info in data.get('dependencies', {}).items():
            if info.get('version'):
                deps.append((name, info['version']))
    return deps


def parse_toml_lock(text):
    """poetry.lock / Cargo.lock → [(name, version)]（含传递依赖）"""
    deps = []
    for block in re.findall(r'\[\[package\]\](.*?)(?=\[\[|\Z)', text, re.DOTALL):
        name = re.search(r'name\s*=\s*"([^"]+)"', block)
        version = re.search(r'version\s*=\s*"([^"]+)"', block)
        if name and version:
            deps.append((name.group(1), version.group(1)))
    return deps


def parse_pipfile_lock(text):
    """Pipfile.lock → [(name, version)]（含传递依赖）"""
    data = json.loads(text)
    deps = []
    for section in ('default', 'develop'):
        for name, info in data.get(section, {}).items():
            v = str(info.get('version', '')).lstrip('==')
            if v:
                deps.append((name, v))
    return deps


def parse_go_mod(text):
    """go.mod → [(name, version)]，兼容单行 require 和 require 块"""
    deps = []
    for m in re.finditer(r'^\s*(?:require\s+)?([\w./-]+)\s+v([\w.\-+]+)', text, re.MULTILINE):
        name, version = m.group(1), m.group(2)
        if name not in ('module', 'go', 'require'):
            deps.append((name, version))
    return deps


def parse_pom_xml(text):
    """pom.xml → [(groupId:artifactId, version)]，解析 properties 占位符，跳过无版本/无法解析的依赖"""
    pm = re.search(r'<properties>(.*?)</properties>', text, re.DOTALL)
    props = {}
    if pm:
        for m in re.finditer(r'<([\w.-]+)>([^<]*)</\1>', pm.group(1)):
            props[m.group(1)] = m.group(2).strip()

    def resolve(v):
        v = (v or '').strip()
        for _ in range(5):
            m = re.search(r'\$\{([\w.-]+)\}', v)
            if not m:
                break
            key = m.group(1)
            if key not in props:
                return None
            v = v.replace(f'${{{key}}}', props[key])
        return None if '${' in v else v

    deps = []
    ns = '{http://maven.apache.org/POM/4.0.0}'
    root = ET.fromstring(text)
    for dep in root.iter(ns + 'dependency'):
        g = dep.find(ns + 'groupId')
        a = dep.find(ns + 'artifactId')
        v = dep.find(ns + 'version')
        if g is None or a is None:
            continue
        ver = resolve(v.text) if v is not None else None
        if ver:
            deps.append((f'{g.text}:{a.text}', ver))
    return deps


def parse_cargo_toml(text):
    """Cargo.toml → [(name, version)]，兼容简单格式 name="1.0" 和表格式 name={version="1.0"}"""
    deps = []
    for block in re.findall(r'\[[^\]]*dependencies\](.*?)(?=\n\[|\Z)', text, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(r'([\w.-]+)\s*=\s*"([\d.~^=<>]+)"', line)
            if m:
                deps.append((m.group(1), m.group(2).lstrip('~^=<>')))
                continue
            m = re.match(r'([\w.-]+)\s*=\s*\{[^}]*?version\s*=\s*"([\d.~^=<>]+)"', line)
            if m:
                deps.append((m.group(1), m.group(2).lstrip('~^=<>')))
    return deps


def parse_gemfile(text):
    """Gemfile → [(name, version)]"""
    deps = []
    for m in re.finditer(r"gem\s+['\"]([\w.-]+)['\"](?:\s*,\s*['\"]([\d.~>=\s]+)['\"])?", text):
        deps.append((m.group(1), (m.group(2) or '').strip()))
    return deps


def parse_gemfile_lock(text):
    """Gemfile.lock → [(name, version)]（含传递依赖）"""
    deps = []
    m = re.search(r'GEM(.*?)(?=\n[A-Z]{2,}|\Z)', text, re.DOTALL)
    if m:
        for mm in re.finditer(r'^\s{4}(\S+)\s+\(([\d.]+)\)', m.group(1), re.MULTILINE):
            deps.append((mm.group(1), mm.group(2)))
    return deps


def parse_composer_json(text):
    """composer.json → [(name, version)]"""
    data = json.loads(text)
    deps = []
    for name, version in data.get('require', {}).items():
        if name != 'php':
            v = str(version).lstrip('^~v')
            if v and v != '*':
                deps.append((name, v))
    return deps


def parse_composer_lock(text):
    """composer.lock → [(name, version)]（含传递依赖）"""
    data = json.loads(text)
    deps = []
    for pkg in data.get('packages', []):
        name = pkg.get('name', '')
        version = str(pkg.get('version', '')).lstrip('v')
        if name and version:
            deps.append((name, version))
    return deps


PARSERS = {
    'requirements.txt': parse_requirements,
    'poetry.lock': parse_toml_lock,
    'pipfile.lock': parse_pipfile_lock,
    'package.json': parse_package_json,
    'package-lock.json': parse_package_lock,
    'go.mod': parse_go_mod,
    'pom.xml': parse_pom_xml,
    'cargo.toml': parse_cargo_toml,
    'cargo.lock': parse_toml_lock,
    'gemfile': parse_gemfile,
    'gemfile.lock': parse_gemfile_lock,
    'composer.json': parse_composer_json,
    'composer.lock': parse_composer_lock,
}


def run_parser(fmt_name, text):
    """按规范格式名解析，解析失败返回空列表"""
    try:
        return PARSERS[fmt_name](text)
    except Exception:
        return []


def _build_lock_map(path):
    """查找兄弟锁文件并解析为 {name: version} 映射。
    返回 (dict, lock_basename) 或 (None, None)。"""
    p = Path(path)
    name_lower = p.name.lower()
    candidates = SIBLING_LOCK_MAP.get(name_lower, [])
    parent = p.parent
    try:
        dir_files = {f.lower(): f for f in os.listdir(parent)}
    except OSError:
        return None, None
    for lock_name in candidates:
        actual_name = dir_files.get(lock_name)
        if actual_name:
            lock_path = parent / actual_name
            try:
                text = lock_path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            if lock_name in PARSERS:
                lock_deps = run_parser(lock_name, text)
                if lock_deps:
                    return dict(lock_deps), actual_name
    return None, None


def detect_format(text):
    """非标准文件名 → 按内容特征识别，返回 (规范名, 生态, 是否锁文件) 或 None"""
    stripped = text.strip()
    low = stripped.lower()

    # JSON 文件（{ 开头）
    if stripped.startswith('{'):
        try:
            data = json.loads(stripped)
        except Exception:
            data = None
        if isinstance(data, dict):
            pkgs = data.get('packages')
            if isinstance(pkgs, dict) and any('node_modules/' in k for k in pkgs):
                return ('package-lock.json', 'npm', True)
            if isinstance(pkgs, list):
                return ('composer.lock', 'Packagist', True)
            if 'dependencies' in data or 'devDependencies' in data:
                return ('package.json', 'npm', False)
            if 'default' in data or 'develop' in data:
                return ('pipfile.lock', 'PyPI', True)
            if 'require' in data:
                return ('composer.json', 'Packagist', False)

    # TOML 锁文件（poetry.lock / Cargo.lock）：[[package]] + name = "xxx"
    if '[[package]]' in stripped and re.search(r'^\s*name\s*=\s*"', stripped, re.MULTILINE):
        if 'python-versions' in low or 'category' in low:
            return ('poetry.lock', 'PyPI', True)
        return ('cargo.lock', 'crates.io', True)

    # go.mod：行首 module 关键字
    if re.search(r'^\s*module\s+[\w./~-]+', stripped, re.MULTILINE):
        return ('go.mod', 'Go', False)

    # pom.xml
    if '<project' in low and ('maven' in low or '<dependency' in low):
        return ('pom.xml', 'Maven', False)

    # Cargo.toml
    if re.search(r'^\s*\[dependencies\]', stripped, re.MULTILINE):
        return ('cargo.toml', 'crates.io', False)

    # Gemfile / Gemfile.lock
    if re.search(r'^\s*gem\s+[\'"]', stripped, re.MULTILINE):
        if re.search(r'^\s{4}\S+\s+\([\d.]+\)', stripped, re.MULTILINE) or 'specs:' in low:
            return ('gemfile.lock', 'RubyGems', True)
        return ('gemfile', 'RubyGems', False)

    # requirements 风格（name==version / name>=version），兜底
    if re.search(r'^\s*[A-Za-z0-9_.-]+\s*(==|>=|<=|~=|!=|<|>)\s*\d', stripped, re.MULTILINE):
        return ('requirements.txt', 'PyPI', False)

    return None


def _range_specs(text, fmt_name):
    """判断清单是否为「范围/未锁定」版本（>=、^、~、* 等），而非精确锁定（==）。
    范围版本扫描基于最低版本，可能显著高估漏洞。"""
    if fmt_name == 'requirements.txt':
        for line in text.splitlines():
            s = line.split(';')[0].strip()
            if not s or s.startswith('#') or s.startswith('-'):
                continue
            if '==' in s:
                continue
            if re.match(r'[A-Za-z0-9_.\-]', s):
                return True
        return False
    if fmt_name == 'package.json':
        try:
            data = json.loads(text)
        except Exception:
            return False
        for sec in ('dependencies', 'devDependencies'):
            for v in data.get(sec, {}).values():
                v = str(v).strip()
                if v[:1] in '^~><*' or v in ('', '*', 'latest') or 'x' in v.lower() or '||' in v:
                    return True
        return False
    # Cargo.toml（默认 caret）、composer.json（^~）、Gemfile（~>）本质上都是范围
    if fmt_name in ('cargo.toml', 'composer.json', 'gemfile'):
        return True
    return False


def parse_deps(path):
    """识别格式并解析，返回 (deps, ecosystem, is_lock, fmt_name, range_specs)"""
    name = Path(path).name.lower()
    text = Path(path).read_text(encoding='utf-8', errors='replace')

    # 1. 精确文件名优先（标准名）
    if name in FILE_FORMATS:
        ecosystem, is_lock = FILE_FORMATS[name]
        return run_parser(name, text), ecosystem, is_lock, name, (not is_lock) and _range_specs(text, name)

    # 2. 非标准文件名 → 内容特征识别
    fmt = detect_format(text)
    if fmt is None:
        return [], 'unknown', False, 'unknown', False
    fmt_name, ecosystem, is_lock = fmt
    return run_parser(fmt_name, text), ecosystem, is_lock, fmt_name, (not is_lock) and _range_specs(text, fmt_name)


# ==================== OSV.dev 查询 ====================
def query_osv(ecosystem, name, version):
    if not version:
        return {'vulns': []}
    url = 'https://api.osv.dev/v1/query'
    payload = json.dumps({
        'package': {'name': name, 'ecosystem': ecosystem},
        'version': version,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {'vulns': []}
        return {'error': f'HTTP {e.code}'}
    except Exception as e:
        return {'error': str(e)}


OSV_BATCH_SIZE = 200


def _osv_querybatch(ecosystem, pairs):
    payload = {'queries': [{'package': {'name': n, 'ecosystem': ecosystem}, 'version': v}
                           for n, v in pairs]}
    req = urllib.request.Request('https://api.osv.dev/v1/querybatch',
                                 data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
        return json.loads(resp.read()).get('results', [])


def _osv_vuln_detail(vid):
    try:
        with urllib.request.urlopen(
                f'https://api.osv.dev/v1/vulns/{urllib.parse.quote(vid)}', timeout=NET_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def query_osv_batch(ecosystem, deps):
    """批量查询 OSV：querybatch 取漏洞 ID，再并发拉详情；批次失败则回退逐包查询。"""
    out = {}
    pending = {}
    all_ids = set()
    pairs = [(n, v) for n, v in deps if v]
    for n, v in deps:
        if not v:
            out[(n, v)] = {'vulns': []}
    for i in range(0, len(pairs), OSV_BATCH_SIZE):
        chunk = pairs[i:i + OSV_BATCH_SIZE]
        try:
            results = _osv_querybatch(ecosystem, chunk)
        except Exception:
            results = []
        if len(results) != len(chunk):
            out.update(_parallel(lambda nv: query_osv(ecosystem, nv[0], nv[1]), chunk, NET_TIMEOUT))
            continue
        for (n, v), res in zip(chunk, results):
            ids = [x.get('id') for x in ((res or {}).get('vulns') or []) if x.get('id')]
            if ids:
                pending[(n, v)] = ids
                all_ids.update(ids)
            else:
                out[(n, v)] = {'vulns': []}
    cache = {}
    if all_ids:
        for vid, val in _parallel(_osv_vuln_detail, sorted(all_ids), NET_TIMEOUT).items():
            if val:
                cache[vid] = val
    for key, ids in pending.items():
        out[key] = {'vulns': [cache[i] for i in ids if i in cache]}
    return out


# ==================== 最新版本查询 ====================
def get_latest_version(ecosystem, name):
    try:
        q = urllib.parse.quote(str(name), safe='/@')
        if ecosystem == 'PyPI':
            url = f'https://pypi.org/pypi/{q}/json'
        elif ecosystem == 'npm':
            url = f'https://registry.npmjs.org/{q}/latest'
        elif ecosystem == 'Go':
            url = f'https://proxy.golang.org/{q}/@latest'
        elif ecosystem == 'crates.io':
            url = f'https://crates.io/api/v1/crates/{q}'
        elif ecosystem == 'RubyGems':
            url = f'https://rubygems.org/api/v1/gems/{q}.json'
        elif ecosystem == 'Packagist':
            url = f'https://repo.packagist.org/p2/{q}.json'
        else:
            return None
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
            if ecosystem == 'PyPI':
                return data['info']['version']
            if ecosystem == 'npm':
                return data.get('version')
            if ecosystem == 'Go':
                return data.get('Version', '').lstrip('v')
            if ecosystem == 'crates.io':
                return data['crate'].get('max_version')
            if ecosystem == 'RubyGems':
                return data.get('version')
            if ecosystem == 'Packagist':
                pkgs = data.get('packages', {}).get(name, [])
                if pkgs:
                    return pkgs[0].get('version', '').lstrip('v')
    except Exception:
        pass
    return None


# ==================== 漏洞数据提取 ====================
def extract_fixed_versions(vuln):
    fixed = []
    for affected in vuln.get('affected', []):
        for r in affected.get('ranges', []):
            for event in r.get('events', []):
                if 'fixed' in event:
                    fixed.append(event['fixed'])
    return sorted(set(fixed))


def extract_cve(vuln):
    for alias in vuln.get('aliases', []):
        if alias.startswith('CVE-'):
            return alias
    return vuln.get('id', '未知')


def _ver_key(v):
    """版本号 → 可比较的数字元组（处理 v 前缀、1.0.0-beta 这类）"""
    parts = []
    for seg in str(v).lstrip('vV').split('.'):
        m = re.match(r'(\d+)', seg)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def min_fixed_above(current, fixed_list):
    """返回「大于当前版本的最小修复版本」，用于避免破坏性升级；无则 None"""
    cur = _ver_key(current)
    above = [f for f in fixed_list if _ver_key(f) > cur]
    return min(above, key=_ver_key) if above else None


def is_malicious(vuln):
    """恶意包判断：OSV.dev 恶意包 ID 以 MAL- 开头，或 summary 明确说「这个包本身恶意」。
    不能用 'malicious' 关键字（普通 CVE 常写 'via malicious redirect' 描述攻击场景，会误判）。"""
    if vuln.get('id', '').upper().startswith('MAL-'):
        return True
    s = vuln.get('summary', '').lower()
    return 'malicious package' in s or 'malicious code' in s


def _severity_from_text(s):
    t = str(s or '').strip().lower()
    if t in ('critical', 'high'):
        return '高危'
    if t in ('moderate', 'medium'):
        return '中危'
    if t == 'low':
        return '低危'
    return None


def _severity_from_cvss(score):
    if isinstance(score, (int, float)):
        return '高危' if score >= 9.0 else '中危' if score >= 7.0 else '低危'
    s = str(score or '')
    if not ('CVSS' in s or ':' in s):
        return None
    if 'C:H' in s and 'I:H' in s:
        return '高危'
    if 'C:H' in s or 'I:H' in s or 'A:H' in s:
        return '中危'
    return '低危'


def classify_severity(vuln):
    for sev in vuln.get('severity', []):
        r = _severity_from_cvss(sev.get('score', ''))
        if r:
            return r
    ds = vuln.get('database_specific') or {}
    r = _severity_from_text(ds.get('severity')) or _severity_from_cvss(ds.get('cvss'))
    if r:
        return r
    for aff in vuln.get('affected', []):
        ads = aff.get('database_specific') or {}
        r = _severity_from_text(ads.get('severity')) or _severity_from_cvss(ads.get('cvss'))
        if r:
            return r
    return '未知'


# ==================== 主扫描 ====================
_SAFE_PAT = re.compile(r'^[\w.@/+-]+$')


def _safe(v):
    """校验依赖名/版本，只允许 URL-safe 字符；含 shell 元字符（如 ; $ ` 空格）返回 None"""
    s = str(v)
    return s if _SAFE_PAT.match(s) else None


def fix_command(ecosystem, name, target):
    n = _safe(name)
    if n is None:
        return f'依赖 {name} 名称含特殊字符，请手动确认升级方式'
    t = _safe(target) if target else None
    if not t:
        return f'升级 {n} 到最新版'
    cmds = {
        'PyPI': f'pip install {n}=={t}',
        'npm': f'npm install {n}@{t}',
        'Go': f'go get {n}@{t}',
        'Maven': f'将 pom.xml 中 {n} 版本改为 {t}',
        'crates.io': f'cargo add {n}@{t}',
        'RubyGems': f"gem '{n}', '{t}'",
        'Packagist': f'composer require {n}:{t}',
    }
    return cmds.get(ecosystem, f'升级 {n} 到 {t}')


def remove_command(ecosystem, name):
    n = _safe(name)
    if n is None:
        return f'依赖 {name} 名称含特殊字符，请手动确认移除方式'
    cmds = {
        'PyPI': f'pip uninstall {n}',
        'npm': f'npm uninstall {n}',
        'Go': f'go mod edit -droprequire {n}',
        'Maven': f'从 pom.xml 移除 {n} 依赖',
        'crates.io': f'cargo remove {n}',
        'RubyGems': f'从 Gemfile 移除 gem "{n}"',
        'Packagist': f'composer remove {n}',
    }
    return cmds.get(ecosystem, f'移除依赖 {n}')


EPSS_BATCH = 50


def query_epss_batch(cves):
    """批量查 EPSS（FIRST.org 免费公开），返回 {cve: (epss, percentile)}"""
    result = {}
    cves = [c for c in cves if str(c).startswith('CVE-')]
    if not cves:
        return result
    batches = [cves[i:i + EPSS_BATCH] for i in range(0, len(cves), EPSS_BATCH)]

    def fetch(batch):
        url = 'https://api.first.org/data/v1/epss?cve=' + ','.join(batch)
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.loads(resp.read())
            return [(item['cve'], (float(item.get('epss', 0.0)), float(item.get('percentile', 0.0))))
                    for item in data.get('data', [])]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=len(batches)) as ex:
        for items in ex.map(fetch, batches):
            for cve, val in items:
                result[cve] = val
    return result


def epss_label(f):
    """CVE 的在野利用概率标签（客观事实，不下「必须修」的结论）"""
    if f['sev'] == '恶意':
        return '⛔ 恶意包（立即移除）'
    epss = f.get('epss')
    if epss is None:
        return 'EPSS 无数据'
    if epss >= 0.1:
        return f'🔴 正在被攻击（在野利用 {epss * 100:.1f}%）'
    if epss >= 0.01:
        return f'🟠 较可能被利用（在野利用 {epss * 100:.1f}%）'
    return f'⚪ 理论风险（在野利用 {epss * 100:.2f}%）'


MAX_WORKERS = 16
NET_TIMEOUT = 20
BATCH_SIZE = 60


def _parallel(fn, items, timeout):
    """并发执行 fn(item)，返回 {item: result}。按批处理：每批独立超时，避免大规模项目（如 300+ 依赖）因总超时不够而漏扫。"""
    results = {}
    items = list(items)
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        ex = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        try:
            futures = {ex.submit(fn, item): item for item in batch}
            for fut in as_completed(futures, timeout=timeout):
                item = futures[fut]
                try:
                    results[item] = fut.result()
                except Exception:
                    results[item] = None
        except Exception:
            pass
        finally:
            ex.shutdown(wait=False)
    return results


def scan(path):
    deps, ecosystem, is_lock, fmt_name, range_specs = parse_deps(path)
    if ecosystem == 'unknown':
        return {'error': '无法识别依赖文件格式'}
    if not deps:
        return {'error': '未解析到任何依赖'}

    deps = list(dict.fromkeys(deps))

    # ---- 版本解析：从兄弟锁文件获取实际安装版本 ----
    version_source = 'lock' if is_lock else 'declared'
    lock_file = Path(path).name if is_lock else None

    if not is_lock:
        lock_map, lock_name = _build_lock_map(path)
        if lock_map:
            deps = [(n, lock_map.get(n, v)) for n, v in deps]
            version_source = 'lock'
            lock_file = lock_name
            range_specs = False

    print(f'  批量扫描 {len(deps)} 个依赖（OSV.dev）...', file=sys.stderr)
    osv_results = query_osv_batch(ecosystem, deps)

    vuln_pkgs = {n for (n, v), r in osv_results.items() if r and 'error' not in r and r.get('vulns')}
    latest_cache = {}
    if vuln_pkgs:
        print(f'  并发查询 {len(vuln_pkgs)} 个包的最新版本...', file=sys.stderr)
        latest_cache = _parallel(lambda n: get_latest_version(ecosystem, n), list(vuln_pkgs), NET_TIMEOUT)

    findings = []
    errors = 0
    seen = set()
    for name, version in deps:
        result = osv_results.get((name, version))
        if result is None or 'error' in result:
            errors += 1
            continue
        vulns = result.get('vulns', [])
        if not vulns:
            continue
        latest = latest_cache.get(name)
        for vuln in vulns:
            cve = extract_cve(vuln)
            if (name, cve) in seen:
                continue
            seen.add((name, cve))
            fixed = extract_fixed_versions(vuln)
            is_mal = is_malicious(vuln)
            min_fix = min_fixed_above(version, fixed)
            sev = '恶意' if is_mal else classify_severity(vuln)
            target = min_fix or latest
            cmd = remove_command(ecosystem, name) if is_mal else fix_command(ecosystem, name, target)
            findings.append({
                'name': name, 'version': version, 'cve': cve, 'sev': sev,
                'summary': vuln.get('summary', ''),
                'fixed': ('/'.join(fixed[:3]) + ('...' if len(fixed) > 3 else '')) if fixed else '未知',
                'min_fix': min_fix,
                'latest': latest or '未知',
                'fix_cmd': cmd,
            })

    cves = [f['cve'] for f in findings if str(f['cve']).startswith('CVE-')]
    epss_map = query_epss_batch(cves)
    for f in findings:
        epss, pct = epss_map.get(f['cve'], (None, None))
        f['epss'] = epss
        f['percentile'] = pct

    return {
        'ecosystem': ecosystem,
        'is_lock': is_lock,
        'dep_count': len(deps),
        'vuln_count': len(findings),
        'findings': findings,
        'errors': errors,
        'version_source': version_source,
        'lock_file': lock_file,
        'range_specs': range_specs,
    }


def safety_grade(findings):
    if not findings:
        return '🟢 A 级（未发现已知漏洞）'
    if any(f['sev'] == '恶意' for f in findings):
        return '⛔ 最高危（发现恶意包/供应链投毒）'
    if any(f['sev'] == '高危' for f in findings):
        return '🔴 D 级（存在高危漏洞）'
    if any(f['sev'] == '中危' for f in findings):
        return '🟠 C 级（存在中危漏洞）'
    return '🟡 B 级（仅低危漏洞）'


LOCK_HINT = {
    'PyPI': 'poetry.lock（或 Pipfile.lock）',
    'npm': 'package-lock.json',
    'crates.io': 'Cargo.lock',
    'RubyGems': 'Gemfile.lock',
    'Packagist': 'composer.lock',
    'Maven': '（Maven 无锁文件，可用 mvn dependency:tree 查看传递依赖）',
    'Go': '（Go 无锁文件，传递依赖已含在 go.mod 的 require 块里）',
}


def print_priority_summary(findings):
    mal = [f for f in findings if f['sev'] == '恶意']
    high = [f for f in findings if f['sev'] != '恶意' and f.get('epss') is not None and f['epss'] >= 0.1]
    mid = [f for f in findings if f['sev'] != '恶意' and f.get('epss') is not None and 0.01 <= f['epss'] < 0.1]
    low = [f for f in findings if f['sev'] != '恶意' and f.get('epss') is not None and f['epss'] < 0.01]
    unknown = [f for f in findings if f['sev'] != '恶意' and f.get('epss') is None]

    if not (mal or high or mid or low or unknown):
        return

    print('===== 优先级排序（先修哪些）=====')
    if mal:
        print(f'⛔ 恶意包（{len(mal)} 个）：立即移除，不要升级')
        for f in mal:
            print(f'    · {f["name"]} {f["version"]} — {f["cve"]}')
    if high:
        print(f'🔴 正在被攻击（EPSS ≥ 10%，共 {len(high)} 个）：优先处理')
        for f in sorted(high, key=lambda x: -(x.get('epss') or 0)):
            print(f'    · {f["name"]} {f["version"]} — {f["cve"]}（在野利用 {f["epss"] * 100:.1f}%）')
    if mid:
        print(f'🟠 较可能被利用（EPSS 1%~10%，共 {len(mid)} 个）：纳入近期计划')
        for f in sorted(mid, key=lambda x: -(x.get('epss') or 0)):
            print(f'    · {f["name"]} {f["version"]} — {f["cve"]}（在野利用 {f["epss"] * 100:.1f}%）')
    if low:
        print(f'⚪ 理论风险（EPSS < 1%，共 {len(low)} 个）：暂无在野利用，可纳入长期计划')
    if unknown:
        print(f'❓ EPSS 无数据（{len(unknown)} 个）：新披露漏洞，暂无利用数据')
    if not (mal or high or mid) and (low or unknown):
        rest = low + unknown
        rank = {'恶意': 0, '高危': 1, '中危': 2, '低危': 3, '未知': 4}
        counts = {}
        for f in rest:
            counts[f['sev']] = counts.get(f['sev'], 0) + 1
        summary = ' / '.join(f'{k} {v}' for k, v in sorted(counts.items(), key=lambda kv: rank.get(kv[0], 9)))
        print(f'📋 均为低在野概率，建议打包进一次例行依赖更新；若需排序，按严重度（{summary}）：')
        for f in sorted(rest, key=lambda x: rank.get(x['sev'], 9))[:5]:
            print(f'    · {f["name"]} {f["version"]} — {f["cve"]}（{f["sev"]}）→ {f.get("min_fix") or "升级到最新版"}')
        if len(rest) > 5:
            print(f'    … 其余 {len(rest) - 5} 个见上方清单')
    print()


_ACTUAL_HINT = {
    'PyPI': '运行 pip freeze > requirements-lock.txt（或 python -m pip freeze），再把该文件发我',
    'npm': '提供 package-lock.json（含实际安装版本）',
    'crates.io': '提供 Cargo.lock',
    'RubyGems': '提供 Gemfile.lock',
    'Packagist': '提供 composer.lock',
    'Maven': '提供精确版本，或运行 mvn dependency:tree 获取实际版本',
    'Go': 'Go 模块已在 go.mod 中使用精确版本，无需锁文件',
}


def print_report(r, path):
    if 'error' in r:
        print(f'[错误] {r["error"]}')
        return

    findings = r['findings']
    sev_order = {'恶意': 0, '高危': 1, '中危': 2, '低危': 3, '未知': 4}
    findings.sort(key=lambda f: sev_order.get(f['sev'], 9))

    dep_type = '（含传递依赖）' if r['is_lock'] else '（仅直接依赖）'
    print('===== 依赖漏洞扫描报告 =====')
    # 版本来源提示
    vs = r.get('version_source', 'declared')
    lf = r.get('lock_file')
    if vs == 'lock' and not r['is_lock'] and lf:
        print(f'ℹ️ 已按 {lf} 的实际安装版本解析（更准确）')
    elif vs == 'declared':
        if r.get('range_specs'):
            print('⚠️ 本次基于「声明范围的最低版本」（如 Pillow>=9.0 按 9.0 计），可能显著高估漏洞，请勿直接照单全收')
            print(f'   建议基于实际安装版本复扫：{_ACTUAL_HINT.get(r["ecosystem"], "提供锁文件")}')
        else:
            print('⚠️ 基于声明版本（可能与实际安装版本不同）；若项目有锁文件（如 package-lock.json）请提供，以获得实际版本与传递依赖')
    print(f'项目: {path}')
    print(f'生态: {r["ecosystem"]} | 依赖数: {r["dep_count"]} 个{dep_type} | 发现漏洞: {r["vuln_count"]} 个')
    print(f'安全评级: {safety_grade(findings)}')
    if not r['is_lock'] and vs != 'lock':
        lock = LOCK_HINT.get(r['ecosystem'], '锁文件')
        print(f'💡 想查更深一层的「传递依赖」（依赖的依赖）？把项目里的 {lock} 文件发我，我再扫一次')
    print()

    if not findings:
        print('未发现已知漏洞（OSV.dev 收录范围内）。')
        print('💡 干净 ≠ 零风险：0-day 未入库、未扫描的生态/依赖都可能漏。建议：')
        print('   1) 把扫描接进 CI（--sarif），每次提交自动查；')
        print('   2) 定期复扫（漏洞库每天更新）——和用户约定下次复扫日期并记入漏洞台账；')
        print('   3) 用锁文件固定传递依赖，减少依赖漂移引入的新漏洞。')
        if r['errors']:
            print(f'（注：{r["errors"]} 个依赖查询失败，可能未覆盖）')
        print()
        print('===== 扫描结束 =====')
        return

    current_sev = None
    for i, f in enumerate(findings, 1):
        if f['sev'] != current_sev:
            current_sev = f['sev']
            icon = {'恶意': '⛔', '高危': '🔴', '中危': '🟠', '低危': '🟡', '未知': '⚪'}[current_sev]
            count = sum(1 for x in findings if x['sev'] == current_sev)
            print(f'## {icon} {current_sev}（{count} 个）')
        print(f'[{i}] {f["name"]} {f["version"]}')
        print(f'    {f["cve"]} — {f["summary"][:60]}')
        if f['sev'] == '恶意':
            print('    ⚠️ 恶意包/供应链投毒：建议立即移除，不要升级')
        else:
            print(f'    修复于: {f["fixed"]} | 最新版本: {f["latest"]}')
            print(f'    {epss_label(f)}')
            if f.get('min_fix'):
                print(f'    → 建议升级到 {f["min_fix"]}（最小修复版本，避免破坏性变更）')
            else:
                print('    → 无已知最小修复版本，建议升级到最新版')
        print(f'    → {f["fix_cmd"]}')
        print('    → 改完请重新扫描确认')
        print()

    if r['errors']:
        print(f'（注：{r["errors"]} 个依赖查询失败，可能未覆盖）')
    print_priority_summary(findings)
    print('===== 扫描结束 =====')


SEV_TO_LEVEL = {'恶意': 'error', '高危': 'error', '中危': 'warning', '低危': 'note', '未知': 'note'}


def _build_sarif_run(r, path):
    """构建单个 SARIF run 对象（用于单文件和项目模式多 run）"""
    resolved_uri = Path(path).resolve().as_uri()

    driver_props = {}
    vs = r.get('version_source')
    lf = r.get('lock_file')
    if vs is not None:
        driver_props['versionSource'] = vs
    if lf is not None:
        driver_props['lockFile'] = lf
    if r.get('range_specs'):
        driver_props['rangeSpecs'] = True

    if 'error' in r:
        run = {
            'tool': {
                'driver': {
                    'name': 'dependency-vuln-scanner',
                    'version': '2.6.0',
                    'informationUri': 'https://osv.dev',
                    'rules': [{
                        'id': 'scanner-error',
                        'shortDescription': {'text': r['error']},
                    }],
                },
            },
            'results': [{
                'ruleId': 'scanner-error',
                'level': 'error',
                'message': {'text': r['error']},
                'locations': [{
                    'physicalLocation': {
                        'artifactLocation': {'uri': resolved_uri},
                        'region': {'startLine': 1},
                    },
                }],
            }],
        }
        if driver_props:
            run['tool']['driver']['properties'] = driver_props
        return run

    seen_cves = {}
    for f in r['findings']:
        cve = f['cve']
        if cve not in seen_cves:
            seen_cves[cve] = f['summary']

    rules = [
        {'id': cve, 'shortDescription': {'text': summary}}
        for cve, summary in seen_cves.items()
    ]

    results = []
    for f in r['findings']:
        results.append({
            'ruleId': f['cve'],
            'level': SEV_TO_LEVEL.get(f['sev'], 'note'),
            'message': {
                'text': f'{f["name"]} {f["version"]}: {f["summary"]} ({f["cve"]})',
            },
            'locations': [{
                'physicalLocation': {
                    'artifactLocation': {'uri': resolved_uri},
                    'region': {'startLine': 1},
                },
            }],
            'properties': {
                'dependency': f['name'],
                'installedVersion': f['version'],
                'severity': f['sev'],
                'epss': f.get('epss'),
                'percentile': f.get('percentile'),
                'minFixVersion': f.get('min_fix'),
                'latestVersion': f.get('latest', '未知'),
                'fixCommand': f.get('fix_cmd', ''),
            },
        })

    run = {
        'tool': {
            'driver': {
                'name': 'dependency-vuln-scanner',
                'version': '2.6.0',
                'informationUri': 'https://osv.dev',
                'rules': rules,
            },
        },
        'results': results,
    }
    if driver_props:
        run['tool']['driver']['properties'] = driver_props
    return run


def build_sarif(r, path):
    return {
        '$schema': 'https://json.schemastore.org/sarif-2.1.0.json',
        'version': '2.1.0',
        'runs': [_build_sarif_run(r, path)],
    }


def _candidate_manifest_name(name):
    n = name.lower()
    if 'requirements' in n and n.endswith(('.txt', '.in')):
        return True
    if 'lock' in n and n.endswith(('.json', '.txt', '.lock')):
        return True
    if n.startswith('gemfile'):
        return True
    if n.endswith('.toml'):
        return True
    if n.endswith('.json') and ('package' in n or 'composer' in n):
        return True
    return False


def _classify_manifest(path):
    name = path.name.lower()
    if name in FILE_FORMATS:
        eco, is_lock = FILE_FORMATS[name]
        return (name, eco, is_lock)
    if not _candidate_manifest_name(name):
        return None
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    return detect_format(text)


def _discover_targets(root_dir):
    """递归发现支持的依赖文件（含非标准命名，按内容识别），每个目录优先选择锁文件。返回 [Path]"""
    targets = []
    seen = set()
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        current_dir = Path(dirpath)
        supported = []
        for f in filenames:
            p = current_dir / f
            info = _classify_manifest(p)
            if info:
                supported.append((p, info[2]))
        if supported:
            locks = [p for p, is_lock in supported if is_lock]
            manifests = [p for p, is_lock in supported if not is_lock]
            for p in (locks if locks else manifests):
                rp = str(p)
                if rp not in seen:
                    seen.add(rp)
                    targets.append(p)
    return targets


_EXIT_OVERRIDE = 0

_FAIL_RANK = {'恶意': 4, '高危': 3, '中危': 2, '低危': 1, '未知': 0}


def _fail_code(findings, threshold):
    if not threshold:
        return 0
    limit = _FAIL_RANK.get(threshold, 99)
    return 2 if any(_FAIL_RANK.get(f.get('sev'), 0) >= limit for f in findings) else 0


def _hard_exit(code=0):
    """刷新输出后立即退出，避免 atexit 等待卡住的网络线程导致挂起。"""
    if code == 0 and _EXIT_OVERRIDE:
        code = _EXIT_OVERRIDE
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def main():
    global _EXIT_OVERRIDE
    ap = argparse.ArgumentParser(description='依赖漏洞扫描器')
    ap.add_argument('path', help='依赖文件或目录路径')
    out_group = ap.add_mutually_exclusive_group()
    out_group.add_argument('--json', action='store_true',
                           help='以 JSON 输出扫描结果，便于复扫时对比漏洞台账（错误也以 JSON 输出并正常退出，便于调用方解析）')
    out_group.add_argument('--sarif', action='store_true',
                           help='以 SARIF 2.1.0 输出（用于接入 CI/代码扫描平台，如 GitHub Code Scanning）；扫描失败也输出合法 SARIF 并正常退出，便于 CI 解析')
    ap.add_argument('--fail-on', choices=['恶意', '高危', '中危', '低危'],
                    help='命中该级别及以上的漏洞时退出码为 2（用于 CI 卡构建）；默认不因漏洞改变退出码')
    args = ap.parse_args()

    target_path = Path(args.path)

    # ---- 目录/项目模式 ----
    if target_path.is_dir():
        targets = _discover_targets(str(target_path))
        if not targets:
            err_msg = '未发现任何依赖文件'
            if args.json:
                print(json.dumps({'error': err_msg}, ensure_ascii=False, indent=2))
                sys.stdout.flush()
                _hard_exit(0)
            if args.sarif:
                print(json.dumps(build_sarif({'error': err_msg}, str(target_path)),
                                 ensure_ascii=False, indent=2))
                sys.stdout.flush()
                _hard_exit(0)
            print(f'[错误] {err_msg}')
            _hard_exit(1)

        results = []
        for i, t in enumerate(targets, 1):
            print(f'  [{i}/{len(targets)}] 扫描 {os.path.relpath(str(t), str(target_path))} ...', file=sys.stderr)
            r = scan(str(t))
            r['file'] = str(t)
            results.append(r)
        _EXIT_OVERRIDE = _fail_code([f for rr in results for f in rr.get('findings', [])], args.fail_on)

        # ---- 人类可读：逐行 + 汇总 ----
        if not args.json and not args.sarif:
            for r in results:
                rel = os.path.relpath(r['file'], str(target_path))
                grade = safety_grade(r.get('findings', []))
                vs_tag = f' [锁:{r["lock_file"]}]' if r.get('version_source') == 'lock' and r.get('lock_file') and not r.get('is_lock') else ''
                print(f'{rel} | {r.get("ecosystem", "?")} | 依赖 {r.get("dep_count", 0)} | 漏洞 {r.get("vuln_count", 0)} | {grade}{vs_tag}')
            print()

            dep_total = sum(r.get('dep_count', 0) for r in results)
            vuln_total = sum(r.get('vuln_count', 0) for r in results)
            all_findings = []
            for r in results:
                all_findings.extend(r.get('findings', []))

            sev_counts = {}
            for f in all_findings:
                s = f.get('sev', '未知')
                sev_counts[s] = sev_counts.get(s, 0) + 1

            print('===== 项目汇总 =====')
            print(f'目标文件数: {len(results)} | 依赖总数: {dep_total} | 漏洞总数: {vuln_total}')
            parts = []
            for sev in ('恶意', '高危', '中危', '低危', '未知'):
                c = sev_counts.get(sev, 0)
                if c:
                    parts.append(f'{sev}: {c}')
            if parts:
                print(' | '.join(parts))

            print_priority_summary(all_findings)

            # 声明版本提示
            declared_targets = [r for r in results if r.get('version_source') == 'declared']
            if declared_targets:
                print('⚠️ 以下目标基于声明版本扫描（未找到锁文件），结果可能与实际安装版本不同：')
                for r in declared_targets:
                    tag = '（范围版本，可能显著高估）' if r.get('range_specs') else '（精确锁定）'
                    print(f'   · {os.path.relpath(r["file"], str(target_path))} {tag}')
                if any(r.get('range_specs') for r in declared_targets):
                    eco0 = declared_targets[0].get('ecosystem')
                    print(f'   → 建议基于实际安装版本复扫：{_ACTUAL_HINT.get(eco0, "提供锁文件")}')

            print('===== 扫描结束 =====')
            _hard_exit(0)

        # ---- --json 项目模式 ----
        if args.json:
            dep_total = sum(r.get('dep_count', 0) for r in results)
            vuln_total = sum(r.get('vuln_count', 0) for r in results)
            targets_out = []
            for r in results:
                targets_out.append({
                    'file': r['file'],
                    'ecosystem': r.get('ecosystem'),
                    'is_lock': r.get('is_lock'),
                    'dep_count': r.get('dep_count', 0),
                    'vuln_count': r.get('vuln_count', 0),
                    'version_source': r.get('version_source'),
                    'lock_file': r.get('lock_file'),
                    'range_specs': r.get('range_specs'),
                    'findings': r.get('findings', []),
                    'errors': r.get('errors', 0),
                })
            project_output = {
                'project': str(target_path),
                'target_count': len(results),
                'dep_total': dep_total,
                'total_vuln_count': vuln_total,
                'targets': targets_out,
            }
            print(json.dumps(project_output, ensure_ascii=False, indent=2))
            sys.stdout.flush()
            _hard_exit(0)

        # ---- --sarif 项目模式 ----
        if args.sarif:
            runs = []
            for r in results:
                runs.append(_build_sarif_run(r, r['file']))
            sarif_doc = {
                '$schema': 'https://json.schemastore.org/sarif-2.1.0.json',
                'version': '2.1.0',
                'runs': runs,
            }
            print(json.dumps(sarif_doc, ensure_ascii=False, indent=2))
            sys.stdout.flush()
            _hard_exit(0)

    # ---- 单文件模式（原有逻辑） ----
    if not target_path.exists():
        if args.json:
            print(json.dumps({'error': f'文件不存在: {args.path}'}, ensure_ascii=False, indent=2))
            sys.stdout.flush()
            _hard_exit(0)
        if args.sarif:
            print(json.dumps(build_sarif({'error': f'文件不存在: {args.path}'}, args.path),
                             ensure_ascii=False, indent=2))
            sys.stdout.flush()
            _hard_exit(0)
        print(f'[错误] 文件不存在: {args.path}')
        _hard_exit(1)

    r = scan(args.path)
    _EXIT_OVERRIDE = _fail_code(r.get('findings', []) if isinstance(r, dict) else [], args.fail_on)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.stdout.flush()
        _hard_exit(0)
    if args.sarif:
        print(json.dumps(build_sarif(r, args.path), ensure_ascii=False, indent=2))
        sys.stdout.flush()
        _hard_exit(0)
    print_report(r, args.path)
    _hard_exit(1 if isinstance(r, dict) and 'error' in r else 0)


if __name__ == '__main__':
    main()
