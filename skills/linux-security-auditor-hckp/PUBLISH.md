# 发布手册 · linux-security-auditor-hckp

> 状态：已发布 ✓ skillId=205719（pending_review，审核三线通过后自动上架）
> slug：`linux-security-auditor-hckp` ｜ 计费：per_call / 1.00 元/次

## 前置条件（需发布者本人准备）
1. skillhub.cn 注册 + 实名认证（人脸核身）
2. 个人中心 → API keys 创建 API Token
3. 完成商户入驻（按次收费的硬性前提）
4. 安装 SkillHub CLI：`pip install skillhub`，并执行 `skillhub login --key <YOUR_TOKEN>` 保存登录态

## 发布与更新命令
```bash
SKILL="<技能目录绝对路径>/linux-security-auditor-hckp"
HOST="https://api.skillhub.cn"
TOKEN="<YOUR_SKILLHUB_TOKEN>"

# 1) 本地预检（无 HTTP）
skillhub publish "$SKILL" --dry-run

# 2) 真实发布（带 token，可跳过 login）
skillhub publish "$SKILL" --token "$TOKEN" --host "$HOST" --changelog "更新版本"
```

## 更新版本
改 SKILL.md 的 version → 保持 slug 不变 → 重跑第 2 步 --changelog "..."。

## 审核
三线并行：内容合规 + 漏洞扫描 + AI 安全评估，全过自动上架。个人中心 → 我的 Skill 看实时状态。
