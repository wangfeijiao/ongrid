---
name: security-baseline-check-hckp
display_name: 安全基线核查 · 专业规范包按次调用
display_name_en: Security Baseline Check
description: |
  按主机与服务安全基线逐项核查，输出合规率与整改项。
description_zh: |
  按主机与服务安全基线逐项核查，输出合规率与整改项。（WorkBuddy 免费兼容版；付费按次版见 SkillHub 同名技能）
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 安全基线核查 · 专业规范包按次调用（WorkBuddy 免费兼容版）

> 本技能为 WorkBuddy 免费版：专业规范包已随包内置（references/security-baseline-check-hckp_spec.json），
> 本地直接运行即可产出结构化交付物，无需付费或联网。付费按次版（402 网关）见 SkillHub 同名技能。

## 能力
按主机与服务安全基线逐项核查，输出合规率与整改项。

## 使用方法
1. 准备输入：把待处理对象整理成 `input.json`（结构见下）。
2. 运行本地生成器：
   ```bash
   python3 scripts/apply_spec.py --spec references/security-baseline-check-hckp_spec.json --input input.json --out out.md
   ```
3. 打开 `out.md` 即为结构化交付物（按规范包自动核算，使用前请人工复核）。

### input.json 结构
```json
{
  "subject": "对象名称",
  "items": [
    {"name": "条目名", "evidence": "该条目的事实/文本", "value": 0}
  ]
}
```

## 规范包要点（安全基线规范包）
### 维度与权重
| 维度 | 权重 |
|------|------|
| 账 | 号 |
| 服 | 务 |
| 补 | 丁 |
| 日 | 志 |

### 判定标准
1. 须禁用或改密默认账号
2. 仅开放必要端口与服务
3. 高危漏洞须限期修复
4. 审计日志须开启并留存

### 交付物章节
- 合规率总评
- 分项核查表
- 不合规项
- 整改清单

## 付费按次版
搜索 SkillHub 同名技能 `security-baseline-check-hckp` 可使用 402 网关按次下发的最新规范包与更多资产。
