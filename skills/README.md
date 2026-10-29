# 运维技能库（skills/）

78 个运维领域技能（`SKILL.md` 格式），来源为 WorkBuddy 技能市场，供 ongrid 管理节点的 AI 聊天系统（chatruntime）加载使用。

## 目录结构

每个子目录为一个技能：

    <skill-name>/
      SKILL.md        # 技能定义：YAML frontmatter（name/description）+ 正文操作指引
      references/     # 参考文档（部分技能）
      scripts/        # 可执行脚本（部分技能）
      assets/         # 资源文件（部分技能）

`tools/` 为维护脚本，不参与部署。

## 与 ongrid 技能系统的关系

ongrid manager 的 chatruntime 原生加载 `SKILL.md` 技能（兼容 skills.sh / claude-plugin / openclaw 布局）。管理节点技能目录为 `$ONGRID_DATA_DIR/skills`（默认 `/var/lib/ongrid/skills`，容器内同名路径，持久化 bind-mount，主控升级不清除）。

本目录是技能的**源（原样保存）**；部署包由 `tools/prepare-skills.py` 生成，部署版相对源版做两项适配：

| 适配 | 原因 |
| --- | --- |
| 注入 `activation`（mode=keyword + 关键词表） | 78 个技能正文合计约 570KB；activation 缺省（always）会把全文注入每轮 AI 系统提示词。keyword 模式仅在用户消息命中关键词子串（大小写不敏感）时注入 |
| `name` 归一化为 snake_case | 与 ongrid 加载规则一致，消除启动日志中的 `name_normalized` 告警 |

## 使用

    # 1. 生成部署包（.cache/skills-deploy/ + .cache/ongrid-ops-skills.tar.gz）
    python skills/tools/prepare-skills.py

    # 2. 部署到管理节点（上传 → 解包 → 校正属主/权限 → 重启 ongrid → 验证日志）
    python skills/tools/deploy-skills.py --host 10.7.30.47 --user ubuntu --password '***'

    # 密码也可通过环境变量提供：ONGRID_REMOTE_PASSWORD

## 回滚

管理节点上移除技能目录并重启：

    sudo rm -rf /var/lib/ongrid/skills/<skill_name>
    sudo docker restart ongrid

## 注意

- 关键词表在 `tools/prepare-skills.py` 内维护（`KEYWORDS`），**新增技能时必须同步配置**，否则 prepare 脚本会报缺。
- activation 为大小写不敏感的子串匹配，关键词建议 2-4 字短词并覆盖技能核心触发场景。
- 管理节点 `skills` 目录中，平铺的技能目录会被 ongrid 识别为 bare-skills 包逐目录加载；市场（marketplace）安装的包与手动部署的技能可以共存。
