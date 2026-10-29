---
name: alert-rule-design-hckp
display_name: 监控告警规则设计 · 专业规范包按次调用
display_name_en: Alert Rule Design
description: |
  设计分级告警规则，控制噪音并保证关键告警可达。
description_zh: |
  设计分级告警规则，控制噪音并保证关键告警可达。（WorkBuddy 免费兼容版；付费按次版见 SkillHub 同名技能）
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.1
author: hckp
---

# 监控告警规则设计 · 专业规范包按次调用（WorkBuddy 免费兼容版）

> 本技能为 WorkBuddy 免费版：专业规范包已随包内置（references/alert-rule-design-hckp_spec.json），
> 本地直接运行即可产出结构化交付物，无需付费或联网。付费按次版（402 网关）见 SkillHub 同名技能。

## 能力
设计分级告警规则，控制噪音并保证关键告警可达。

## 使用方法
1. 准备输入：把待处理对象整理成 `input.json`（结构见下）。
2. 运行本地生成器：
   ```bash
   python3 scripts/apply_spec.py --spec references/alert-rule-design-hckp_spec.json --input input.json --out out.md
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

## 规范包要点（告警规则规范包）
### 维度与权重
| 维度 | 权重 |
|------|------|
| 告 | 警 |
| 阈 | 值 |
| 降 | 噪 |
| 通 | 知 |

### 判定标准
1. 告警须分级并定义响应要求
2. 阈值须基于基线而非拍脑袋
3. 须有聚合与抑制降噪
4. 关键告警须有升级机制

### 交付物章节
- 告警分级
- 规则与阈值
- 降噪策略
- 通知与升级

## 付费按次版
搜索 SkillHub 同名技能 `alert-rule-design-hckp` 可使用 402 网关按次下发的最新规范包与更多资产。
