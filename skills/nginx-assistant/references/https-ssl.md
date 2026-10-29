# HTTPS 与 SSL

> 2026 年的基线：TLS 1.2+1.3、自动续期证书、HTTP/2 起步。老教程的配置别照抄。

## 现代 HTTPS 基线（Nginx 1.25+）

```nginx
server {
    listen 443 ssl;
    # 1.25.1+ 推荐：http2 单独成指令（listen 443 ssl http2 已废弃，还报错）
    http2 on;

    server_name example.com;

    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    # ⚠️ fullchain（含中间证书）！只发 cert.pem 部分安卓客户端会报证书链不完整

    ssl_protocols TLSv1.2 TLSv1.3;           # TLS 1.0/1.1 已禁用（旧客户端放弃）
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    # 1.3 的套件不在这里配置（握手时自动协商）

    ssl_session_cache shared:SSL:10m;        # session 复用（10MB 约存 4 万会话）
    ssl_session_timeout 1d;
    ssl_session_tickets off;                 # 安全考虑关掉（forward secrecy）

    # OCSP Stapling（客户端不用再查 OCSP，提速）
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 8.8.8.8 valid=300s;

    # HSTS（⚠️ 确认全站 HTTPS 后再开；max-age 内无法回退）
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
}

# HTTP 全部 301 到 HTTPS
server {
    listen 80;
    server_name example.com;
    return 301 https://$host$request_uri;
}
```

## 证书那些事

### 证书类型与来源

| 类型 | 有效期 | 适用 |
|---|---|---|
| Let's Encrypt（ACME） | 90 天，自动续 | 个人/创业（免费，自动续期已成熟） |
| 阿里云/腾讯云免费证书 | 1 年 | 国内云（每年手动换，注意提醒） |
| 通配符证书 | `*.example.com` | 多子域名（ACME DNS-01 验证） |
| 多域名证书（SAN） | 一张证书多个域名 | 主域+跳转域 |

### Let's Encrypt 自动续期（certbot）

```bash
# 安装并签发（自动改 nginx 配置）
sudo certbot --nginx -d example.com -d www.example.com

# 续期命令（certbot 装好后自动注册了 systemd timer / cron）
sudo certbot renew --dry-run          # 演练，确认续期链路通

# 续期后要 reload nginx（certbot 默认 deploy hook 会做，手动确认）
# /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh:
# #!/bin/bash
# nginx -s reload
```

**证书过期是经典事故**：监控必加证书剩余天数告警（`openssl s_client ... | openssl x509 -noout -enddate`）。

### 自签证书（内网/测试）

```bash
# 一条命令生成自签证书（内网服务间通信）
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout self.key -out self.crt \
  -subj "/CN=internal.example.com" \
  -addext "subjectAltName=DNS:internal.example.com,IP:10.0.0.5"
# ⚠️ 客户端必须配信任（curl -k 或导入 CA）
```

## Nginx 作为客户端的 SSL（proxy_pass 到 HTTPS 后端）

```nginx
location /api/ {
    proxy_pass https://secure-backend;

    # 默认校验后端证书（推荐保持）
    proxy_ssl_verify on;
    proxy_ssl_trusted_certificate /etc/nginx/certs/ca.pem;

    # 自签后端（测试环境）
    # proxy_ssl_verify off;          # ⚠️ 生产别关

    # mTLS：Nginx 出示客户端证书
    proxy_ssl_certificate     /etc/nginx/certs/client.crt;
    proxy_ssl_certificate_key /etc/nginx/certs/client.key;

    proxy_ssl_protocols TLSv1.2 TLSv1.3;
    proxy_ssl_server_name on;          # SNI（后端是多域名虚拟主机时必开）
}
```

## 验证与排障命令

```bash
# 看证书信息（有效期/域名/签发者）
openssl s_client -connect example.com:443 -servername example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -dates -subject -issuer

# 测试握手（TLS 版本强制）
openssl s_client -connect example.com:443 -tls1_2 </dev/null 2>&1 | grep Protocol
openssl s_client -connect example.com:443 -tls1_3 </dev/null 2>&1 | grep Protocol

# 查看完整证书链（缺中间证书时 SSL alert）
openssl s_client -connect example.com:443 -servername example.com </dev/null | grep -E "verify|s:|i:"

# SSL 错误在 error.log 里的样子：
# "SSL_do_handshake() failed" → 套件/协议不匹配
# "no suitable certificate found" → server_name 没匹配到证书（SNI 问题）
# "ssl_stapling ignored" → OCSP 拿不到（上游被墙/URL 过期）
```

## HTTP/2 与 HTTP/3

```nginx
# HTTP/2（1.25.1+）
http2 on;

# HTTP/3 / QUIC（1.25+，需编译时启用 --with-http_v3_module）
listen 443 quic;
listen 443 ssl;
http2 on;
http3 on;
add_header Alt-Svc 'h3=":443"; ma=86400';    # 告诉客户端可以升级到 h3
```

## 性能小抄

```nginx
# 会话复用省的是完整握手（1-2 RTT）
ssl_session_cache shared:SSL:50m;

# 1.3 的 0-RTT（安全性权衡：有重放风险，幂等接口才开）
ssl_early_data on;
# 后端要能识别：proxy_set_header Early-Data $ssl_early_data;
```
