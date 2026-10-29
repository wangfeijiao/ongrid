# 安全头详解与 CSP 策略分级

## 核心安全头

### X-Frame-Options

```
SAMEORIGIN      # 只允许同域嵌入（推荐）
DENY            # 完全禁止嵌入
```

**作用**：防止点击劫持（clickjacking）。

---

### X-Content-Type-Options

```
nosniff         # 唯一有效值
```

**作用**：禁止浏览器 MIME 嗅探，防止上传的 `.jpg` 被当脚本执行。

---

### Referrer-Policy

| 值 | 含义 |
|---|---|
| `no-referrer` | 完全不发送 Referer |
| `strict-origin-when-cross-origin` | 同域完整 URL，跨域只发 origin（推荐）|
| `same-origin` | 只同域发送 |

---

### Strict-Transport-Security (HSTS)

```
max-age=63072000; includeSubDomains; preload
```

⚠️ **开发环境不要开** —— 一旦设置，浏览器在 max-age 内强制 HTTPS，无法回退。

---

## Content-Security-Policy (CSP) 分级

### 级别 1：最严格（从这儿开始）

```
default-src 'self'
```

只允许加载同域资源。任何外部脚本/样式/图片都会被阻止。

### 级别 2：允许必要外部资源

```
default-src 'self';
script-src 'self' https://cdn.example.com;
style-src 'self' 'unsafe-inline';
img-src 'self' https: data:;
font-src 'self' https://fonts.gstatic.com;
connect-src 'self' https://api.example.com;
```

### 级别 3：报告模式（上线前测试）

```
Content-Security-Policy-Report-Only: default-src 'self'; report-uri /csp-report
```

只报告违规，不阻止。用于收集需要放宽的规则。

---

## 快速检查清单

- [ ] HTTPS 强制跳转
- [ ] TLS 1.2+ 且禁用弱密码套件
- [ ] 安全头全部配置
- [ ] CSP 从严格开始
- [ ] 隐藏服务器版本
- [ ] 速率限制配置
- [ ] 日志包含请求时间、响应大小、上游耗时
- [ ] 健康检查端点可用
- [ ] 错误页面不泄露路径信息
