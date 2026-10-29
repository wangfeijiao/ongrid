# 服务器安全维护 · 杀木马病毒

> 诺盾网络出品 · 作者：**泰安诺盾 · 仇苏毅**

给服务器与电脑做一套**从体检 → 查杀 → 加固 → 备份 → 出事能取证**的完整闭环。
所有判定基于本机真实读到的数据，并明确告知**哪些东西没查到**。

---

## 安装

把本目录放到 WorkBuddy 的技能目录下即可：

| 平台 | 路径 |
|---|---|
| Windows | `%USERPROFILE%\.workbuddy\skills\nuodun-server-defense` |
| macOS / Linux | `~/.workbuddy/skills/nuodun-server-defense` |

首次使用先做环境自检：

```bash
python scripts/cli.py doctor
```

## 快速开始

```bash
# 环境自检
python scripts/cli.py doctor

# 全面体检（只读）
python scripts/cli.py audit

# 图形控制台（浏览器操作，推荐）
python scripts/cli.py ui
```

Windows 下也可直接双击 `launch.cmd` 一键启动控制台。

## 能力一览

| 能力 | 命令 |
|---|---|
| ⚡ 性能体检与优化 | `audit --scope perf` |
| 🛡 安全体检与加固 | `audit --scope sec` / `harden` |
| 🧹 硬盘瘦身（只读扫描+隔离还原） | `clean scan` / `clean plan` / `clean clean` |
| 🚀 上网提速（可回滚调优） | `net diag` / `net tune` |
| 🦠 木马病毒深度查杀 | `hunt scan` / `hunt forensics` / `hunt block` / `hunt kill` |
| 🔎 持久化足迹体检 | `audit --scope malware --deep` |
| 🧯 日志与崩溃诊断 | `audit --scope crash` / `logs` |
| 💾 主动备份（哈希校验+回滚） | `backup run` / `verify-latest` / `restore` |
| 🖥 图形控制台 | `ui` |
| 🌐 远程 Linux/UNIX 只读巡检 | `audit --target root@host` |
| 🧩 IIS / Tomcat / 数据库 | `references/iis-tomcat-deploy.md`、`references/database-install.md` |

## 出错时会怎么表现

用起来遇到问题时，本工具**不会崩、不会只甩一串英文错误**，而是用中文把四件事说清楚：

| 说清楚什么 | 含义 |
|---|---|
| **场景** | 哪个命令 / 哪个界面操作 / 哪个后台任务出的问题 |
| **原因** | 到底哪里错了（例如"没有管理员权限""扫描结果文件不存在"） |
| **处理** | 你现在该怎么做（补权限、先跑一次扫描、刷新列表…） |
| **影响** | 系统状态有没有被改动（变更类操作失败会明确说"没有改动系统"） |

覆盖到每一层：命令行参数错误、图形控制台接口异常、后台任务失败、**生成的处置脚本**（`harden`/`net tune`/`hunt block`/`hunt kill` 等）、报告生成。

### 出错后怎么回看

```bash
# 最近一次错误（人话摘要）
python scripts/cli.py errors

# 加 --all 读跨天记录，--clear 清空
python scripts/cli.py errors --all
python scripts/cli.py errors --clear

# 出错时想看完整堆栈，命令前后都能加 --debug
python scripts/cli.py audit --debug
python scripts/cli.py --debug ui
```

图形控制台左侧「错误日志」页也会列出最近的错误，并可一键清空。

### 错误留痕在哪

错误以 JSONL 逐行落盘到 `outputs/logs/errors-YYYYMMDD.jsonl`（含完整堆栈），单个文件超过 2MB 自动轮转，便于事后排查。

### 退出码约定

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 用法 / 参数错误 |
| `2` | 执行出错（已给出原因与处理） |
| `130` | 用户主动中断（Ctrl+C），会提示如何回滚 |

生成的处置脚本同样自带逐项错误捕获：某一项失败不会中断整份脚本，脚本末尾汇总失败项并以非零码退出。

## 安全边界

- **数据不出本机**：体检、扫描、报告全部在本机完成，不外传任何配置、日志或报告。
- **远程巡检不留痕**：探针通过 stdin 管道下发，不在目标机写入任何文件、不装任何依赖。
- **改系统的动作默认不动手**：`harden` 等不带 `--apply` 只生成脚本；执行前自动做配置快照并生成回滚脚本。
- **清理优先隔离而非删除**：默认进隔离区，可一键还原。
- **只能对自有或已获授权的资产做变更**。

## 目录结构

```
nuodun-server-defense/
├── SKILL.md              # 技能说明（能力清单 / 行为契约 / 命令速查）
├── SKILL.md 引用的参考文档
├── scripts/              # 采集、判定、报告、控制台、自检
├── references/           # 各领域详细参考（按需读取）
├── assets/               # 控制台界面与品牌资源
└── launch.cmd            # Windows 一键启动控制台
```

## 自检

```bash
python scripts/selftest.py
```

全部使用合成数据，**不改动本机任何设置**。改完脚本应重跑，保持全绿。
