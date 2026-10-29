---
name: cicd-standard-hckp
display_name: CI/CD 流水线规范
display_name_en: Cicd Standard
description: |
  用「构建-测试-部署」三段固化发布。
description_zh: |
  用「构建-测试-部署」三段固化发布。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# CI/CD 流水线规范

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
发布靠手工易出错。

## 核心方法
自动构建测试，部署分级可回滚。

## 标准步骤
1. 提交触发自动构建
2. 跑单元/集成测试门禁
3. 制品版本化
4. 分级部署（灰→全）
5. 一键回滚预案

## 可直接套用的模板
```
commit→build→test门禁→artifact→灰度→全量→回滚
```

## 常见错误
- 无测试门禁
- 直接全量
- 无回滚

## 自检清单
- [ ] 有测试
- [ ] 有灰度
- [ ] 可回滚

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

