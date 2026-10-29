---
name: ops-inspector
display_name: FeYng-服务器健康巡检
display_name_en: FeYng-Server Health Inspector
description: 对 Linux 服务器执行只读健康巡检与基础排障，按阈值判定风险，输出结论优先的结构化报告与分级建议
description_zh: 当用户需要检查服务器状态、服务器体检、排查系统异常、看 CPU 内存磁盘负载、定位服务故障或分析日志时使用。执行只读巡检命令，按阈值表判定风险等级，输出结论优先的结构化报告，并给出按优先级排序的可执行建议
description_en: Use this skill when the user needs to inspect Linux server health, run a server checkup, troubleshoot system issues, review CPU memory disk load, diagnose service failures or analyze logs. Runs read-only inspection commands, flags risks against a threshold table, and outputs a conclusion-first structured report with prioritized actionable advice
category: 开发工具
version: 0.1.2
author: willi
---

# 服务器健康巡检

## 触发条件

当用户出现以下任一意图时激活本技能：

- 检查 / 体检 / 看下 服务器、主机、节点、环境、机器
- 服务器卡、慢、挂、宕机、报警、异常、重启后异常
- 排查 CPU、内存、磁盘、负载、进程、网络、端口问题
- 服务起不来、连接不上、端口不通、日志报错、OOM

## 硬性安全约束（必须遵守）

1. 默认只执行只读命令，命令清单见 references/commands.md。
2. 严禁修改配置、删除文件、重启服务、安装软件、kill 进程，除非用户明确同意。
3. 任何写操作、重启、kill、删除，必须先说明影响范围并获得用户明确确认。
4. 不读取用户未指定的敏感文件内容；密钥、密码、token 一律不写入报告。
5. 命令执行失败或权限不足时，在报告中标注该项无法获取，不编造数据。
6. 未安装的监控工具（mpstat、iostat 等）直接跳过并标注，不尝试安装。

## 执行步骤

1. 确认目标
   - 确认目标服务器（IP / 主机名）与访问方式：SSH 私钥路径、已配置连接器、或用户已提供凭据。
   - 访问方式不明确时，先询问，不猜测。

2. 基础巡检
   - 按 references/commands.md 依次执行只读命令，收集以下指标：
     - 系统版本、运行时长、系统时间与时区
     - CPU 使用率、平均负载（1/5/15 三值）、iowait、steal
     - 内存 available、used、swap 使用率与 si/so 活动
     - 磁盘使用率、剩余空间（绝对值）、inode 使用率
     - D 状态进程、僵尸进程、TOP5 进程（按 CPU、按内存）
     - 监听端口、连接状态、网络丢包
     - 最近系统与内核错误日志、OOM 记录

3. 风险判定
   - 按 references/commands.md 中的阈值表，将每项指标标记为：正常 / 关注 / 危险。
   - 磁盘分区同时看百分比与绝对剩余空间，取更严格者；内存以 available 为准。
   - 负载看 15 分钟值与趋势（1 分钟 > 15 分钟 → 负载上升中，即使未超线也标记关注）。

4. 关联判定
   - 结合指标组合判断瓶颈（负载高 + CPU 空闲 → 磁盘 IO / D 状态进程；内存高 + swap 活动 → 内存压力），关联规则见 references/commands.md 第 6 节。

5. 定位根因
   - 存在风险时，结合指标与日志时间线给出最可能的 1 至 3 个原因，按概率排序。
   - 证据不足的判断必须标注待确认，不得写成确定结论。

6. 输出报告
   - 按 templates/report.md 的结构输出：结论 → 指标明细 → 风险项 → 建议 → 待确认项。

## 输出要求

- 结论放最前面，一句话说清服务器当前整体状态。
- 指标用表格呈现，每行带当前值、阈值、状态。
- 建议按优先级排序，每条写清做什么、为什么、风险多大。
- 未经用户确认，不输出执行修复类指令，只给方案与命令示例。
- 全程使用用户所用语言输出。
- 证据引用：结论与风险判定必须引用具体命令输出或日志片段；无证据的结论标注「推断」。
- 置信度分级：每个根因与建议标注高/中/低置信度，低置信度列入待确认项。
- 不确定即标注：命令失败、权限不足、信息缺失时写「无法获取/信息不足」，不编造填充。

## 变更记录

- 0.1.2 阈值表对齐业界基准（归一化负载趋势、iowait/steal、available、磁盘绝对剩余空间、IO await），新增关联判定与 D 状态/僵尸进程检查，输出要求增加证据引用与置信度分级。
- 0.1.1 应用名称加 FeYng 归属前缀。
- 0.1.0 初始版本：只读巡检、阈值判定、结构化报告。
