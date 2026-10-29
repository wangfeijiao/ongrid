---
name: server-setup
display_name: 服务器配置向导
display_name_en: Server Setup
description: Generates server configuration files that are secure by default and match the actual workload, instead of copy-paste snippets that open unnecessary ports or run as root. It picks the right process model, sets up HTTPS with modern TLS, configures rate limiting and logging, and structures the file so security headers and caching rules do not conflict. Use when the user asks nginx、nginx配置、反向代理、负载均衡、服务器配置、web服务器、https配置、ssl证书、tls、域名配置、端口转发、server配置、apache、caddy、服务器安全、防火墙、rate limit、访问控制、静态资源、缓存策略、gzip、 brotli、http2、http3、cdn配置、server block、virtual host、upstream、location、rewrite、redirect、https强制、安全头、csp、hsts、xss防护、服务器优化、性能调优、并发连接、worker进程, or says nginx, nginx config, reverse proxy, load balancer, server config, web server, https config, ssl certificate, tls, domain config, port forwarding, server setup, apache, caddy, server security, firewall, rate limit, access control, static assets, cache strategy, gzip, brotli, http2, http3, cdn, server block, virtual host, upstream, location, rewrite, redirect, https enforce, security headers, csp, hsts, xss protection, server optimization, performance tuning, concurrent connections, worker process.
description_zh: 从暴露面分析开始，生成默认安全的 Nginx/Caddy/Apache 配置，含 TLS、安全头、速率限制、健康检查与回滚策略。
description_en: Starts from exposure analysis, generates secure-by-default Nginx/Caddy/Apache configurations with TLS, security headers, rate limiting, health checks and rollback strategies.
category: engineering
version: 1.0.0
author: 昆仑增长
allowed-tools: Read, Write, Bash
---

# Server Setup

生成**默认安全、不暴露过多攻击面**的服务器配置，而不是复制粘贴后端口全开、root 运行的片段。

大多数服务器配置的问题不是「指令不会写」，是**
没想清楚「谁可以访问什么、以什么身份运行、出错了怎么发现」**。
所以流程从「威胁模型」开始：这台服务器暴露给谁、提供什么服务、最坏情况是什么。

## 何时使用

- 新服务需要 Nginx/Apache/Caddy 配置 → 走完整流程
- 现有配置想加 HTTPS → 走「TLS」分支
- 需要反向代理到后端 → 走「代理」分支
- 静态资源托管 → 走「静态」分支
- 多域名/多服务 → 走「虚拟主机」分支
- 性能优化 → 走「调优」分支

## 工作流

### 第 1 步：明确暴露面

回答三个问题：

1. **谁连得上** —— 公网？内网？特定 IP？
2. **提供什么** —— 静态文件？API 代理？WebSocket？上传接口？
3. **最坏情况** —— 被 DDoS？被注入？配置泄露？

暴露面决定配置严格度：

| 暴露面 | 必须开启 | 必须限制 |
|---|---|---|
| 公网生产 | HTTPS、HSTS、CSP、速率限制 | 禁止明文 HTTP、禁止目录索引、禁止版本信息泄露 |
| 内网服务 | 基础认证或 IP 白名单 | 不对外暴露管理端口 |
| 开发环境 | 最小可用配置 | 不复制生产证书、不开启 HSTS（会缓存到浏览器）|

### 第 2 步：写核心配置

见 `references/config-templates.md`。每个配置必须包含：

1. **监听与 TLS** —— 80 端口重定向到 443；TLS 1.2+；优先 ECDHE 密码套件
2. **安全头** —— `X-Frame-Options`、`X-Content-Type-Options`、`Referrer-Policy`
3. **内容安全策略（CSP）** —— 从最严格的 `default-src 'self'` 开始，按需放宽
4. **速率限制** —— 按路径区分：静态资源宽限、API 严格、登录页最严
5. **日志** —— 统一格式（JSON）、包含请求时间、响应大小、上游耗时
6. **错误页面** —— 不泄露服务器版本和路径信息

### 第 3 步：验证配置

任何配置写完必须能过语法检查：

```bash
nginx -t              # Nginx
apachectl configtest  # Apache
caddy validate        # Caddy
```

**判据**：配置保存后，语法检查必须通过，否则不交付。

### 第 4 步：给健康检查与回滚

生产环境必须能验证服务是否真可用：

```bash
# 健康检查端点
location /health {
    access_log off;
    return 200 "ok\n";
    add_header Content-Type text/plain;
}
```

回滚策略：
- 配置修改前备份旧配置
- 使用 `reload` 而非 `restart`（不中断现有连接）
- 修改后 5 分钟内确认无错误日志激增

## 硬约束

- **不生成 HTTP（明文）生产配置** —— 公网服务必须 HTTPS
- **不暴露服务器版本信息** —— 关闭 `Server` 头或隐藏版本号
- **不给 777 权限** —— 静态文件目录最小权限（通常为 755/644）
- **不替用户写具体域名和证书路径** —— 用占位符，标注「替换为你的」
- **涉及上传/执行接口时，必须加文件类型白名单和大小限制** —— 不直接透传

## References

- `references/config-templates.md` —— Nginx/Caddy/Apache 的安全基线配置模板
- `references/security-headers.md` —— 安全头详解与 CSP 策略分级
