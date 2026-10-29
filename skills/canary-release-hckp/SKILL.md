---
name: canary-release-hckp
display_name: 灰度发布方案
display_name_en: Canary Release
description: |
  用按比例/按用户灰度降低发布风险。
description_zh: |
  用按比例/按用户灰度降低发布风险。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 灰度发布方案

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
全量发布怕出大面积故障。

## 核心方法
小流量先行，指标无异常再放量。

## 标准步骤
1. 选灰度维度（比例/用户/区域）
2. 放 1-5% 观察核心指标
3. 设自动回滚阈值
4. 分批放量至 100%
5. 全程监控

## 可直接套用的模板
```
1%→5%→20%→50%→100% 每步看错误率/RT
```

## 常见错误
- 直接全量
- 无回滚阈值
- 不看指标

## 自检清单
- [ ] 有灰度
- [ ] 有回滚
- [ ] 看指标

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

