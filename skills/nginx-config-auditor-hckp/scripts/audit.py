#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nginx 配置安全与性能审计（纯标准库，离线）

用法：
  python audit.py nginx.conf
  python audit.py nginx.conf --format json --output report.json
  cat nginx.conf | python audit.py -

输入：nginx.conf / 站点 .conf 纯文本
输出：分级风险 (CRITICAL/HIGH/MEDIUM/LOW/PASS) + 修复片段 + 0-100 安全评分
"""
import argparse
import json
import re
import sys


def read_text(path):
    """读取配置文本；缺文件返回 None；空文件返回 ''。绝不抛异常。"""
    if path == "-":
        try:
            return sys.stdin.read()
        except Exception:
            return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def strip_comments(text):
    out = []
    for line in (text or "").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def parse_kv(lines):
    """提取 `key value;` 形式指令，忽略块括号。返回 {key_lower: [values]}。"""
    kv = {}
    for line in lines:
        m = re.match(r"^([A-Za-z_][\w.]*)\s+(.+?)\s*;?\s*$", line)
        if m:
            k = m.group(1).lower()
            v = m.group(2).rstrip("{").strip()
            kv.setdefault(k, []).append(v)
    return kv


def parse_blocks(text):
    servers = len(re.findall(r"\bserver\s*\{", text or ""))
    locations = len(re.findall(r"\blocation\s+", text or ""))
    return servers, locations


def _joined(kv, key):
    return " ".join(kv.get(key, [])).lower()


def audit(kv, raw_text, servers, locations):
    issues = []

    def add(level, rule, message, fix):
        issues.append({"level": level, "rule": rule, "message": message, "fix": fix})

    # 1) server_tokens 信息泄露
    st = _joined(kv, "server_tokens")
    if "on" in st.split():
        add("MEDIUM", "server_tokens", "server_tokens 为 on，Nginx 版本号会随错误页/响应头泄露", "server_tokens off;")
    elif "server_tokens" not in kv:
        add("LOW", "server_tokens", "未显式设置 server_tokens（默认 on，泄露版本号）", "server_tokens off;")

    # 2) TLS 协议与加密套件
    proto = _joined(kv, "ssl_protocols")
    if proto:
        bad = [p for p in ("sslv2", "sslv3", "tlsv1", "tlsv1.1") if p in proto.replace(".", "")]
        if bad:
            add("HIGH", "tls_protocol", "启用不安全/已废弃协议: " + ", ".join(bad),
                "ssl_protocols TLSv1.2 TLSv1.3;")
        if "tlsv1.3" not in proto and not bad:
            add("LOW", "tls_protocol", "未启用 TLSv1.3（现代浏览器已广泛支持，建议开启）",
                "ssl_protocols TLSv1.2 TLSv1.3;")
    else:
        # 仅有 https server 但没显式协议：提示
        if servers and ("listen" in kv and any("ssl" in v for v in kv.get("listen", []))):
            add("MEDIUM", "tls_protocol", "未显式设置 ssl_protocols（可能沿用旧默认值）",
                "ssl_protocols TLSv1.2 TLSv1.3;")

    ciphers = _joined(kv, "ssl_ciphers")
    if ciphers and ("rc4" in ciphers or "des" in ciphers or "md5" in ciphers or "exp" in ciphers):
        add("HIGH", "tls_cipher", "ssl_ciphers 含弱加密套件(RC4/DES/MD5/EXPORT)",
            "ssl_ciphers HIGH:!aNULL:!MD5;")

    # 3) 安全响应头
    headers = " ".join(kv.get("add_header", []))
    needed = {
        "X-Frame-Options": "点击劫持防护",
        "X-Content-Type-Options": "MIME 嗅探防护",
        "Referrer-Policy": "referrer 泄露防护",
    }
    csp = "content-security-policy" in headers.lower()
    for h, desc in needed.items():
        if h.lower() not in headers.lower():
            add("HIGH", "security_header", "缺安全响应头 %s（%s）" % (h, desc),
                "add_header %s \"...\" always;" % h)
    if not csp:
        add("MEDIUM", "security_header", "未设置 Content-Security-Policy（XSS/注入纵深防御）",
            "add_header Content-Security-Policy \"default-src 'self'\"; always;")

    # 4) HTTP→HTTPS 重定向
    listens = " ".join(kv.get("listen", []))
    has_ssl_listen = "ssl" in listens
    has_redirect = ("return" in kv and any("301" in v or "https" in v.lower() for v in kv.get("return", []))) \
        or "rewrite" in kv
    if has_ssl_listen and not has_redirect:
        # 仅当存在 80 端口监听且无重定向时才提示；宽松处理
        if re.search(r"\blisten\s+80\b", listens) or "listen" not in kv:
            add("LOW", "https_redirect", "建议对 80 端口请求做 301 跳转到 HTTPS",
                "server { listen 80; return 301 https://$host$request_uri; }")

    # 5) 请求体 / 超时防护
    if "client_max_body_size" not in kv:
        add("LOW", "body_limit", "未设置 client_max_body_size（大请求可能耗尽磁盘/内存）",
            "client_max_body_size 10m;")
    to = _joined(kv, "client_body_timeout")
    if not to:
        add("LOW", "timeout", "未设置 client_body_timeout（慢速请求可占连接）",
            "client_body_timeout 10s;")

    # 6) 目录列表与限流
    ai = _joined(kv, "autoindex")
    if ai == "on":
        add("MEDIUM", "autoindex", "autoindex 为 on，站点目录可被遍历下载",
            "autoindex off;")
    elif "autoindex" not in kv:
        # 默认 off，PASS，不报
        pass
    if "limit_req" not in kv and "limit_conn" not in kv:
        add("LOW", "rate_limit", "未配置 limit_req / limit_conn（缺少基础防 CC/慢速攻击）",
            "limit_req_zone $binary_remote_addr zone=one:10m rate=10r/s; + limit_req zone=one burst=20;")

    # 7) 运行身份与权限
    if "user" not in kv:
        add("LOW", "run_user", "未显式设置 user 指令（默认以编译期用户/root 风险）",
            "user www-data;")

    # 8) 隐藏后端 / 版本 (server_tokens 已覆盖)；附加统计
    if servers == 0 and locations == 0:
        add("LOW", "no_block", "未检测到 server/location 块，可能只粘贴了片段；按片段维度仍审计",
            "粘贴完整 http { ... } 块可获得更完整评估")

    return issues


def score(issues):
    w = {"CRITICAL": 25, "HIGH": 12, "MEDIUM": 5, "LOW": 1, "PASS": 0}
    penalty = sum(w.get(i["level"], 0) for i in issues)
    return max(0, 100 - penalty)


def render_text(issues, sc, meta):
    print("=" * 60)
    print("Nginx 配置安全与性能体检报告")
    print("=" * 60)
    if meta:
        for k, v in meta.items():
            print("%s: %s" % (k, v))
    if not issues:
        print("\n✅ 未发现问题（基于已提供的配置项）")
    else:
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        for it in sorted(issues, key=lambda x: order.get(x["level"], 9)):
            icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}.get(it["level"], "·")
            print("\n%s [%s] %s" % (icon, it["level"], it["rule"]))
            print("   问题: %s" % it["message"])
            print("   修复: %s" % it["fix"])
    print("\n" + "=" * 60)
    print("安全评分: %d / 100" % sc)
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description="Nginx 配置安全与性能审计")
    ap.add_argument("path", nargs="?", default="-", help="nginx.conf 路径，或 - 读标准输入")
    ap.add_argument("--format", default="text", choices=["text", "json"], help="输出格式")
    ap.add_argument("--output", default=None, help="输出文件路径（json 时）")
    args = ap.parse_args()

    text = read_text(args.path)
    if text is None:
        err = {"error": "无法读取文件: %s（请检查路径是否存在）" % args.path}
        if args.format == "json":
            print(json.dumps(err, ensure_ascii=False))
        else:
            print("[ERROR] %s" % err["error"])
        return 1

    kv = parse_kv(strip_comments(text))
    servers, locations = parse_blocks(text)
    issues = audit(kv, text, servers, locations)
    sc = score(issues)

    meta = {"server块": servers, "location块": locations, "命中项": len(issues)}
    if args.format == "json":
        out = {"score": sc, "meta": meta, "issues": issues}
        payload = json.dumps(out, ensure_ascii=False, indent=2)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(payload)
                print("已写出: %s" % args.output)
            except OSError as e:
                print("[WARN] 写出失败: %s" % e)
        else:
            print(payload)
    else:
        render_text(issues, sc, meta)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # 顶层兜底：绝不崩溃/强制退出
        print("[FATAL] 审计异常: %s" % e)
        sys.exit(3)
