---
name: linux-hardening-hckp
display_name: Linux 安全加固清单
display_name_en: Linux Hardening
description: |
  SSH/账户/防火墙/审计九项加固点。
description_zh: |
  SSH/账户/防火墙/审计九项加固点。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# Linux 安全加固清单

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
新服务器上线前加固。

## 核心方法
最小暴露+最小权限+可审计。

## 标准步骤
1. 禁用 root 直登改用密钥
2. 改 SSH 端口或限源
3. 关无用服务与端口
4. 配防火墙默认拒绝
5. 开审计与失败锁定

## 可直接套用的模板
```
SSH禁密/限源｜防火墙默认DROP｜审计on｜ fail2ban
```

## 常见错误
- 仍允许密码登录
- 防火墙未默认拒绝
- 无审计

## 自检清单
- [ ] 密钥登录
- [ ] 最小端口
- [ ] 有审计

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

