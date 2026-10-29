---
name: server-checklist-hckp
display_name: 服务器巡检清单
display_name_en: Server Checklist
description: |
  一份覆盖 CPU/磁盘/日志/安全的日周月巡检。
description_zh: |
  一份覆盖 CPU/磁盘/日志/安全的日周月巡检。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 服务器巡检清单

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
服务器多了怕漏查。

## 核心方法
指标基线+阈值+周期三段式巡检。

## 标准步骤
1. 定关键指标与基线
2. 每日自动看盘/CPU/连接
3. 每周查安全更新与日志
4. 每月盘容量与证书
5. 异常留痕

## 可直接套用的模板
```
每日：CPU<80% 磁盘<85% 连接<N｜每周：更新+日志｜每月：容量+证书
```

## 常见错误
- 无基线难判异常
- 只看不处理
- 周期混乱

## 自检清单
- [ ] 有基线
- [ ] 有周期
- [ ] 有留痕

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

