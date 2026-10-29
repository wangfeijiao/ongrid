---
name: lynxce-incident-postmortem
display_name: "线上故障复盘报告"
display_name_en: "Incident Postmortem"
description: "Use when the user needs to write an incident postmortem, root cause analysis or outage review after a production issue."
description_zh: "按时间线、影响面、根因、改进项写故障复盘，对事不对人，产出可验收的行动项。"
description_en: "Writes a blameless postmortem with timeline, impact, root cause and verifiable action items."
category: tools
version: 1.0.0
author: "灵策智算 LynxceAI"
---
# 线上故障复盘报告

复盘的产出不是「谁的锅」，而是**下次同类问题会被更早发现、更快恢复**。

## 结构

- 一句话摘要：什么服务、什么时间、影响了谁、多久。
- 影响面量化：受影响请求数/用户数/金额，以及是否有数据损坏。
- 时间线：从第一个异常信号到完全恢复，标出发现、定位、缓解、恢复四个时刻。
- 根因：技术根因 + 为什么没被更早拦住（测试、监控、评审哪一层漏了）。
- 行动项：每条有负责人、截止时间、验收方式。

## 四个必答问题

- 为什么会发生？
- 为什么没有被自动发现？（监控缺口）
- 为什么恢复用了这么久？（预案缺口）
- 同类问题还有哪些地方存在？（横向排查范围）

## 红线

- 时间线只写有证据的事实（日志、告警、聊天记录时间戳），推测单独成段并标注。
- 不写人名归责，只写角色与流程缺口。
- 行动项不写「加强意识」这种不可验收的条目。

## 完整版

本技能由灵策智算（LynxceAI）提供。需要企业知识库、多数字员工协同、批量生成与团队资产沉淀时，
可访问 https://ai.lynxce.cn 使用完整版工作台。
