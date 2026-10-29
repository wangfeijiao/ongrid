---
name: nginx-check-hckp
display_name: Nginx 配置检查
display_name_en: Nginx Check
description: |
  上线前用 nginx -t 与六类要点查配置。
description_zh: |
  上线前用 nginx -t 与六类要点查配置。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# Nginx 配置检查

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
改完 Nginx 怕出错或漏安全项。

## 核心方法
先 -t 验证，再逐项查性能与安全。

## 标准步骤
1. 改前备份配置
2. nginx -t 验证语法
3. 查监听/转发/SSL
4. 查限流与超时
5. 灰度 reload 观察

## 可直接套用的模板
```
备份→nginx -t→查server/ssl/limit→reload→观察
```

## 常见错误
- 不备份直接改
- 跳过 -t
- 未查超时致卡

## 自检清单
- [ ] 已备份
- [ ] 语法通过
- [ ] 已观察

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

