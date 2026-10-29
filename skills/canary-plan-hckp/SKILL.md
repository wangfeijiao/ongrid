---
name: canary-plan-hckp
display_name: 灰度发布方案 · 专业规范包按次调用
display_name_en: Canary Plan
description: |
  设计灰度批次、观察指标与回滚触发条件。
description_zh: |
  设计灰度批次、观察指标与回滚触发条件。（WorkBuddy 免费兼容版；付费按次版见 SkillHub 同名技能）
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 灰度发布方案 · 专业规范包按次调用（WorkBuddy 免费兼容版）

> 本技能为 WorkBuddy 免费版：专业规范包已随包内置（references/canary-plan-hckp_spec.json），
> 本地直接运行即可产出结构化交付物，无需付费或联网。付费按次版（402 网关）见 SkillHub 同名技能。

## 能力
设计灰度批次、观察指标与回滚触发条件。

## 使用方法
1. 准备输入：把待处理对象整理成 `input.json`（结构见下）。
2. 运行本地生成器：
   ```bash
   python3 scripts/apply_spec.py --spec references/canary-plan-hckp_spec.json --input input.json --out out.md
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

## 规范包要点（灰度发布规范包）
### 维度与权重
| 维度 | 权重 |
|------|------|
| 灰 | 度 |
| 观 | 察 |
| 回 | 滚 |
| 放 | 量 |

### 判定标准
1. 灰度须按明确维度分流
2. 须同时观察业务与技术指标
3. 须设自动回滚触发条件
4. 放量须小步慢跑

### 交付物章节
- 灰度策略
- 观察指标
- 回滚条件
- 放量计划

## 付费按次版
搜索 SkillHub 同名技能 `canary-plan-hckp` 可使用 402 网关按次下发的最新规范包与更多资产。
