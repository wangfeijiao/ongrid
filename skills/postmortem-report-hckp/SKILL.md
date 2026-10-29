---
name: postmortem-report-hckp
display_name: 故障复盘报告 · 专业规范包按次调用
display_name_en: Postmortem Report
description: |
  按时间线还原故障，定位根因并输出改进项。
description_zh: |
  按时间线还原故障，定位根因并输出改进项。（WorkBuddy 免费兼容版；付费按次版见 SkillHub 同名技能）
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.0
author: hckp
---

# 故障复盘报告 · 专业规范包按次调用（WorkBuddy 免费兼容版）

> 本技能为 WorkBuddy 免费版：专业规范包已随包内置（references/postmortem-report-hckp_spec.json），
> 本地直接运行即可产出结构化交付物，无需付费或联网。付费按次版（402 网关）见 SkillHub 同名技能。

## 能力
按时间线还原故障，定位根因并输出改进项。

## 使用方法
1. 准备输入：把待处理对象整理成 `input.json`（结构见下）。
2. 运行本地生成器：
   ```bash
   python3 scripts/apply_spec.py --spec references/postmortem-report-hckp_spec.json --input input.json --out out.md
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

## 规范包要点（故障复盘规范包）
### 维度与权重
| 维度 | 权重 |
|------|------|
| 时 | 间 |
| 根 | 因 |
| 影 | 响 |
| 改 | 进 |

### 判定标准
1. 须还原精确时间线
2. 须区分直接原因与根本原因
3. 须量化影响
4. 改进项须可跟踪

### 交付物章节
- 故障概要
- 时间线
- 根因分析
- 改进项与跟踪

## 付费按次版
搜索 SkillHub 同名技能 `postmortem-report-hckp` 可使用 402 网关按次下发的最新规范包与更多资产。
