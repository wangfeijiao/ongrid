# Nginx 配置审计方法论

本审计按"防御性配置基线"思路，对 Nginx 配置的 8 个维度做静态文本检查。所有检查均基于你
主动提供的配置文本，不发起任何网络请求、不读取服务器实际状态。

## 维度与判定依据

### 1. server_tokens 信息泄露
- 默认 `on`，错误页与 `Server` 响应头会暴露 `nginx/x.y.z`，便于攻击者定位已知漏洞版本。
- 建议 `server_tokens off;`（如需保留产品名可 `server_tokens on;` 但隐藏版本）。

### 2. TLS 协议与加密套件
- SSLv2/SSLV3/TLS1.0/TLS1.1 均已被公开攻破（POODLE/BEAST），必须禁用。
- 现代基线：`ssl_protocols TLSv1.2 TLSv1.3;`
- 弱套件（RC4/DES/3DES/EXPORT/MD5）应排除：`ssl_ciphers HIGH:!aNULL:!MD5;`

### 3. 安全响应头
- `X-Frame-Options: DENY/SAMEORIGIN`：防止点击劫持。
- `X-Content-Type-Options: nosniff`：防止 MIME 嗅探。
- `Referrer-Policy`：限制 referrer 泄露路径。
- `Content-Security-Policy`：XSS 纵深防御（按需定制）。
- 所有头建议加 `always`，确保错误响应也带。

### 4. HTTP→HTTPS 重定向
- 监听 80 的 server 应 `return 301 https://$host$request_uri;`，避免明文降级。

### 5. 请求体 / 超时防护
- `client_max_body_size` 限制上传体积，防磁盘/内存耗尽。
- `client_body_timeout` / `client_header_timeout` 防慢速连接占满 worker。

### 6. 目录列表与限流
- `autoindex off;`（默认 off，但显式声明更稳），避免站点目录被遍历下载。
- `limit_req` / `limit_conn` 提供基础防 CC 与并发控制。

### 7. 运行身份与权限
- 显式 `user www-data;`（或专用低权账户），避免以 root/编译期默认身份运行。

### 8. 完整性提示
- 仅粘贴片段时，server/location 块计数偏低，提示补充完整 `http {}` 以获得更完整评估。

## 评分模型
- 每项按级别扣分：CRITICAL 25 / HIGH 12 / MEDIUM 5 / LOW 1。
- 评分 = max(0, 100 − 扣分总和)。
- 评分仅反映"配置基线完整度"，不代表真实渗透测试结果。
