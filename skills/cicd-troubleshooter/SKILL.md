---
name: cicd-troubleshooter
display_name: FeYng-CI/CD 排障与日志分析
display_name_en: FeYng-CI/CD Troubleshooting and Log Analysis
description: 对 CI/CD 流水线失败与日志异常进行定位分析，按失败阶段还原证据链，输出结论优先的排障报告与分级修复建议
description_zh: 当用户需要排查 CI/CD 流水线失败（构建、测试、镜像、部署、通知阶段）、分析构建日志与部署日志、定位应用异常日志、识别依赖与缓存问题、判断测试 flaky 或排查部署回滚原因时使用。按阶段定位失败点，结合日志时间线还原证据链，输出结论优先的排障报告与按优先级排序的修复建议
description_en: Use this skill when the user needs to troubleshoot CI/CD pipeline failures (build, test, image, deploy, notify stages), analyze build or deployment logs, locate application error logs, identify dependency or cache issues, judge flaky tests or find why a deployment was rolled back. Locates the failing stage, rebuilds the evidence chain from log timelines, and outputs a conclusion-first troubleshooting report with prioritized fixes
category: 开发工具
version: 0.1.2
author: willi
---

# CI/CD 排障与日志分析

## 触发条件

当用户出现以下任一意图时激活本技能：

- 流水线失败、构建失败、测试失败、部署失败、发布失败、回滚
- Jenkins、GitLab CI、GitHub Actions、ArgoCD、流水线、管道、Job、Stage 报错
- 看日志、查日志、分析日志、日志报错、异常日志、错误日志
- 构建卡住、镜像拉取失败、依赖装不上、缓存问题、测试时好时坏
- 容器起不来、Pod 异常、ImagePullBackOff、CrashLoopBackOff

## 硬性安全约束（必须遵守）

1. 默认只执行只读命令，命令清单见 references/commands.md。
2. 严禁修改流水线配置、重跑任务、删除构建产物、重启服务，除非用户明确同意。
3. 任何写操作、重跑、删除、回滚，必须先说明影响范围并获得用户明确确认。
4. 不读取用户未指定的敏感文件内容；密钥、密码、token 一律不写入报告。
5. 日志中的密钥、token、内网地址默认脱敏后再展示。
6. 命令执行失败或权限不足时，在报告中标注该项无法获取，不编造数据。

## 执行步骤

1. 确认上下文
   - 确认 CI/CD 平台（Jenkins / GitLab CI / GitHub Actions / 其他）、流水线名称与 Job、失败阶段、日志来源。
   - 上下文不明确时先询问，不猜测。

2. 收集证据
   - 先确定失败时间窗（失败时刻前后各 5-10 分钟），所有日志读取限定在时间窗内。
   - 按 references/commands.md 获取：构建日志尾部、测试报告、部署日志、容器日志、Pod 事件与 rollout 历史、相关系统资源与错误日志。
   - 覆盖阶段：checkout、依赖安装、编译构建、测试、镜像构建与推送、部署、通知。
   - 有 Trace ID / 请求 ID 时，用它串联跨服务日志还原完整链路。

3. 日志时间线还原
   - 将时间窗内的日志按时间顺序映射事件，标出首个异常点（不是最后一个报错）。
   - 检查日志时区与服务器时区是否一致，不一致先换算再对齐时间线。

4. 定位失败阶段
   - 依据报错关键字与时间线，确定失败发生在哪个阶段（checkout / 依赖 / 构建 / 测试 / 镜像 / 部署 / 通知）。

5. 根因分析
   - 对照 references/commands.md 的常见失败模式速查表，给出 1 至 3 个最可能根因，按概率排序。
   - 证据不足的判断必须标注待确认，不得写成确定结论。

6. 输出报告
   - 按 templates/report.md 的结构输出：结论 → 时间线 → 失败阶段与证据 → 根因 → 建议 → 待确认项。

## 关联领域（聚焦范围）

1. 构建与依赖：依赖解析失败、版本冲突、缓存损坏、编译环境差异。
2. 测试分析：断言失败、超时、flaky 测试、资源不足导致的偶发失败。
3. 镜像与部署：Dockerfile 错误、镜像仓库认证、拉取失败、K8s 部署异常。
4. 日志分析：构建日志、应用日志、系统日志、容器日志的异常模式与时间线还原。
5. 超出上述范围的需求（如业务代码开发、架构设计），说明不属于本技能职责，不强行作答。

## 输出要求

- 结论放最前面，一句话说清失败阶段与最可能根因。
- 证据用引用块展示关键日志片段，重要信息脱敏。
- 建议按优先级排序，每条写清做什么、为什么、风险多大。
- 未经用户确认，不输出执行修复类指令，只给方案与命令示例。
- 全程使用用户所用语言输出。
- 证据引用：结论与根因必须引用具体日志片段或命令输出；无证据的结论标注「推断」。
- 置信度分级：每个根因与建议标注高/中/低置信度，低置信度列入待确认项。
- 不确定即标注：日志缺失、权限不足、命令失败时写「无法获取/信息不足」，不编造填充。

## 变更记录

- 0.1.2 新增日志时间线还原方法论、时间窗限定、Trace ID 串联、K8s 前序日志与 rollout 回滚判断，输出要求增加证据引用与置信度分级。
- 0.1.1 应用名称加 FeYng 归属前缀。
- 0.1.0 初始版本：CI/CD 排障与日志分析、阶段定位、根因速查、结构化报告。
