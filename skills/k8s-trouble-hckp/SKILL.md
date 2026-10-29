---
name: k8s-trouble-hckp
display_name: K8s 排错清单
display_name_en: K8s Trouble
description: |
  按「Pod-调度-网络-资源」顺序查 K8s 故障。
description_zh: |
  按「Pod-调度-网络-资源」顺序查 K8s 故障。
description_en: |
  IT-ops and security AI skill for audits, hardening checklists, and incident playbooks.
category: it-ops-security
version: 1.0.1
author: hckp
---

# K8s 排错清单

本技能为**免费**知识型技能：提供方法论与模板，不涉及付费服务。

## 适用场景
Pod 起不来或访问不通。

## 核心方法
看状态→事件→资源→网络。

## 标准步骤
1. kubectl describe 看事件
2. 查镜像拉取与探活
3. 查资源限额与节点
4. 查 Service/网络策略
5. 查日志定位应用

## 可直接套用的模板
```
describe事件→镜像/探针→资源limit→svc/网络→logs
```

## 常见错误
- 只看 logs
- 忽略探针
- 忽略 limit

## 自检清单
- [ ] 看事件
- [ ] 查探针
- [ ] 查资源

## 免责声明
本技能提供的是方法论与本地工具，不替代专业判断；涉及安全、合规、财务等场景请以官方规范与专业人士意见为准。所有脚本仅在本地运行，不上传数据，不联网、不收费。

