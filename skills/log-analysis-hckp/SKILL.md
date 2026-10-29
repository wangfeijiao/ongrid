---
name: log-analysis-hckp
display_name: 日志分析套路
display_name_en: Log Analysis
description: |
  用「聚合-异常-溯源」三步从日志找问题。
description_zh: |
  用「聚合-异常-溯源」三步从日志找问题。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 日志分析套路

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
出故障不知道看哪。

## 核心方法
先聚合错误率，再定位异常，最后溯源。

## 标准步骤
1. 集中收集日志
2. 按级别/接口聚合错误
3. 定位突增时间点
4. 关联部署与变更
5. 沉淀为告警规则

## 可直接套用的模板
```
收集→聚合ERROR率→定位尖峰→关联变更
```

## 常见错误
- 日志散落
- 只看报错不看趋势
- 不复盘

## 自检清单
- [ ] 已集中
- [ ] 看趋势
- [ ] 已关联

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

