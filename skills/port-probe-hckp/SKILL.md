---
name: port-probe-hckp
display_name: 端口连通性探测
display_name_en: Port Probe
description: |
  本地对主机做常见端口 TCP 探测，零依赖。
description_zh: |
  本地对主机做常见端口 TCP 探测，零依赖。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 端口连通性探测

本技能为**免费**工具型技能：核心处理脚本在本地运行，不联网、不上传数据、不收费。

## 适用场景
想确认服务器开放了哪些端口。

## 核心方法
标准库 socket 连通性探测，超时 0.5s。

## 标准步骤
1. 指定目标主机
2. 默认扫常见端口或自定义
3. 运行查看开放端口
4. 对照最小化原则关闭多余

## 使用方式（本地零依赖，Python 标准库即可运行）
```bash
python scripts/portscan.py --host example.com
```

## 常见错误
- 扫未授权主机
- 只看开放不治理
- 误报（超时）

## 自检清单
- [ ] 已授权
- [ ] 结果复核
- [ ] 多余端口关闭

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

