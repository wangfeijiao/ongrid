---
name: perf-stress-hckp
display_name: 接口性能压测指南
display_name_en: Perf Stress
description: |
  按目标 TPS/RT/错误率设计压测不走火。
description_zh: |
  按目标 TPS/RT/错误率设计压测不走火。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 接口性能压测指南

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
上线前不知能否扛住。

## 核心方法
梯度加压看拐点，禁压生产高峰。

## 标准步骤
1. 定目标 TPS/RT/错误率
2. 造压测数据与环境
3. 梯度加压找拐点
4. 观测资源与错误
5. 留余量与扩容建议

## 可直接套用的模板
```
目标：RT<200ms 错<0.1%｜梯度加压→拐点→留30%余量
```

## 常见错误
- 直压生产
- 只看均值
- 无资源观测

## 自检清单
- [ ] 有目标
- [ ] 非生产
- [ ] 看资源

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

