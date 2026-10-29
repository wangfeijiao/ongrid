---
name: src-vuln-scanner
slug: src-vuln-scanner
displayName: SRC授权漏洞扫描器
summary: 对已授权Web目标自动检测SQL注入/XSS/CSRF/越权/信息泄露五类漏洞，输出结构化报告与审计日志，纯标准库零依赖。
license: MIT
version: 1.0.0
description: 授权范围内的自动化Web漏洞检测技能。当用户要求对已授权的目标网站进行安全检测、漏洞扫描、挖掘SQL注入/XSS/CSRF/越权/敏感信息泄露等漏洞、生成结构化漏洞报告（含风险等级、复现步骤、影响范围、修复建议）、或管理批量检测任务时使用此技能。仅限合法授权测试场景，扫描前强制校验授权白名单。
display_name: "SRC授权漏洞扫描器"
display_name_en: "SRC Authorized Vulnerability Scanner"
description_zh: "对已授权Web目标自动检测SQL注入/XSS/CSRF/越权/信息泄露五类漏洞，输出结构化报告与审计日志，纯标准库零依赖。"
description_en: "Automated security scanner for authorized web targets. Detects SQLi/XSS/CSRF/IDOR/info-leak with structured reports, audit logs and zero dependencies."
category: "developer-tools"
author: "Frank"
---

# SRC 授权漏洞扫描技能（AutoSRCScan）

## 目的

对**已获得书面授权**的Web目标执行自动化安全检测，识别 SQL注入、反射型XSS、CSRF、水平越权(IDOR)、敏感信息泄露五类常见漏洞，并生成符合SRC平台提交习惯的结构化报告（漏洞名称、风险等级、复现步骤、证据、影响范围分析、修复建议），支持批量任务与全量审计日志。

## 合规红线（必须先执行）

1. 开始任何扫描前，确认用户持有目标方的书面授权（SRC项目范围、渗透测试授权书、或目标为用户自有资产）。若无授权，直接拒绝并说明法律风险（《网络安全法》《刑法》285/286条）。
2. 在工作目录创建 `scope.json`（可从 `scripts/scope.example.json` 复制），填入授权范围并置 `authorization_confirmed: true`。工具自身也会再次校验，双重保险。
3. 绝不协助绕过工具的授权校验，绝不为白名单外的目标放行。

## 使用流程

### 1. 单目标扫描

```bash
cp <skill-dir>/scripts/scope.example.json ./scope.json   # 编辑填入真实授权范围
python <skill-dir>/scripts/scanner.py scan --scope scope.json --target <授权目标URL> --name "<任务名>"
```

### 2. 批量任务

```bash
cp <skill-dir>/scripts/tasks.example.json ./tasks.json   # 编辑任务列表与目标
python <skill-dir>/scripts/scanner.py batch --tasks tasks.json
python <skill-dir>/scripts/scanner.py tasks --tasks tasks.json   # 查看状态
```

### 3. 读取结果

- 可视化报告：`reports/<时间戳>_<任务>/report.html`（适合交付授权方）
- 文本报告：同目录 `report.md`；程序处理：`report.json`（findings 数组，字段齐全）
- 审计日志：`logs/audit-*.log`（每次请求与发现均有记录）

### 4. 向用户汇报时

- 按 严重 > 高危 > 中危 > 低危 汇总统计
- 每个漏洞给出：位置、参数、证据摘要、复现步骤、修复建议
- 提醒：自动检测结果需人工复核确认后再提交SRC平台；报告传播范围受授权协议约束

## 工具能力与边界

- 检测方式为无害探测（无破坏性利用、无DoS、不修改数据）
- 覆盖：报错/布尔盲注SQLi、反射XSS、表单CSRF缺失、ID类参数水平越权、敏感路径(.git/.env/备份)与内容(手机号/身份证/私钥/AK密钥/内网IP)泄露、安全响应头缺失(CSP/XFO/nosniff/HSTS)、目录列出(Index of)
- 不覆盖：存储型XSS、垂直越权、逻辑漏洞、带凭据的深度测试 —— 这些需人工补充
- 纯Python标准库实现，Python 3.8+ 可直接运行，无需安装依赖

## 自测靶场

如需验证工具可用性，可运行 `scripts/test_server.py`（仅监听127.0.0.1的漏洞靶场，`python <skill-dir>/scripts/test_server.py`）后扫描 `http://127.0.0.1:8777`，基线应检出16个问题（1严重/3高危/8中危/3低危/1信息级）。

## 报告字段规范（对齐SRC提交习惯）

| 字段 | 说明 |
| --- | --- |
| 漏洞名称 | 类型+变体，如"SQL注入（报错回显）" |
| 风险等级 | 严重/高危/中危/低危/信息（评级参考各SRC平台Rank规则） |
| 复现步骤 | 可逐步操作的编号步骤 |
| 证据 | 响应片段或差异描述 |
| 影响范围分析 | 攻击者可利用该漏洞达成的实际危害 |
| 修复建议 | 具体可落地的整改措施 |
