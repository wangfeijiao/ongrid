---
name: nuodun-server-defense
display_name: 服务器安全维护、杀木马病毒skill
keywords: 杀毒,杀木马,查杀木马,病毒查杀,木马后门,挖矿,勒索,webshell,应急响应,服务器安全维护
display_name_en: Server Security Maintenance & Malware/Trojan Removal
description: 诺盾网络出品（作者：泰安诺盾仇苏毅）。服务器与电脑的一站式安全维护与杀毒工具：木马病毒深度查杀（挖矿进程/矿池通信/DNS 缓存痕迹、Webshell 特征评分、勒索信与批量加密、WMI 订阅/粘滞键/BITS 隐藏持久化）、一键应急取证包（zip+sha256）、防火墙阻断与进程挂起脚本（生成制需确认）、性能体检优化（CPU/内存/磁盘/内核参数）、硬盘瘦身（只读扫描+分级清单+隔离可还原）、上网提速（网卡/DNS/丢包/TCP/BBR/偷流量诊断，可回滚调优）、架设 IIS 与 Tomcat、安装并调优数据库、主动备份（哈希校验+回滚）、日志与崩溃诊断（蓝屏/OOM/JVM）、安全加固（配置快照+回滚脚本），并以图形控制台分析、维护、优化远程 Linux/UNIX 主机。**出错时报错说清原因（场景/原因/处理/影响四段中文说明）、绝不崩溃**，错误落盘可用 `cli.py errors` 回看；生成的处置脚本自带逐项错误捕获与原因提示。当用户提到「服务器体检」「服务器安全维护」「杀毒」「杀木马」「查杀木马」「病毒查杀」「木马后门」「挖矿」「勒索」「webshell」「中病毒了」「服务器被黑」「应急响应」「取证」「服务器变慢/卡顿」「磁盘满了/C盘满了/硬盘瘦身/清理垃圾」「上网慢/加速上网」「安全加固」「有没有被入侵」「蓝屏/宕机/崩溃排查」「分析日志」「备份重要文件」「装 IIS/Tomcat/数据库」「巡检 Linux 服务器」「报错了/跑不起来/执行失败」等请求时使用；用户刚选中本技能或只问「这个能干什么」时，按 SKILL.md 的「首次对话」给出能力引导卡。运行自带 CLI 与图形控制台，输出 HTML 报告 + JSON 数据 + 可回滚脚本。
description_zh: 服务器/电脑运维与安全工具：性能体检优化、硬盘瘦身清理（只读扫描+分级清单+隔离还原）、上网提速（网卡/DNS/丢包/TCP/BBR/偷流量，可回滚调优）、IIS/Tomcat 部署、数据库安装调优、主动备份（哈希校验+回滚）、木马后门识别清除、日志与崩溃诊断、安全加固，并支持图形化巡检远程 Linux/UNIX。
description_en: "All-in-one ops & security toolkit for servers and PCs: performance audit and tuning, disk-slimming cleanup (read-only scan, risk-graded lists, reversible quarantine), network speedup diagnostics (NIC/DNS/loss/TCP/BBR/traffic-hogging, with rollback scripts), IIS/Tomcat deployment, database install/tuning, verified backups with rollback, malware/backdoor detection, log and crash diagnosis, security hardening, plus a GUI console for remote Linux/UNIX inspection."
category: efficiency
icon: assets/logo.png
brand: 诺盾网络
author_display: 泰安诺盾 · 仇苏毅
version: 2.1.1
author: 泰安诺盾仇苏毅
agent_created: true
---

# 服务器安全维护 · 杀木马病毒

<p align="center"><img src="assets/logo.png" width="120" alt="诺盾网络"/></p>

> **诺盾网络 · 服务器安全维护与木马病毒查杀**  
> 作者：**泰安诺盾 · 仇苏毅**

给一台机器做**从体检到查杀到加固到备份到出事能取证**的完整闭环。
所有判定基于本机真实读到的数据，并且明确告诉你**哪些东西没查到**。

---

# 一、这个技能能做什么（本领清单）

> 用户刚装了技能、或只是问「你能干什么」时，**把下面这张表原样转述给他**
> （可精简措辞，但不要漏掉分类）。这是他决定要不要用的唯一依据。

| 能力 | 具体做什么 | 对应命令 |
|---|---|---|
| **⚡ 性能体检与优化** | 磁盘队列 / %Disk Time / 换页速率采样判读；找出拖慢系统的后台服务、索引、预读；内核参数与文件句柄调优（给可直接执行的值）；区分"正常吃内存"和"内存泄漏" | `audit --scope perf` |
| **🛡 安全体检与加固** | 账户、口令策略、锁定阈值、权限扩散、空口令、UAC、补丁、防火墙、SMBv1、RDP/NLA、SSH 基线核查；一键生成**加固脚本 + 回滚脚本**，改前自动打配置快照 | `audit --scope sec` / `harden` |
| **🧹 硬盘瘦身** | 只读扫描定位空间去哪了（临时文件 / 更新缓存 / 日志 / 包缓存 / 崩溃转储 / 缩略图 / 回收站 / 旧内核 / 大文件 / 重复文件），按**安全·建议·谨慎**三级出可回收清单；清理默认**移入隔离区而非删除**，可一键还原；附 3-2-1 风格的根治建议 | `clean scan` / `plan` / `clean` |
| **🚀 上网提速** | 定位本机侧的网络拖累：网卡协商速率/丢包/错误计数、DNS 解析实测、延迟与抖动（最小/平均/最大）、Linux BBR 拥塞控制、TCP 窗口与缓冲区、后台偷流量、代理残留与 hosts 篡改；生成**调优脚本 + 回滚脚本** | `net diag` / `net tune` |
| **🦠 木马病毒深度查杀** | **专项查杀引擎**：挖矿（持续高 CPU 进程 · 矿池通信 · DNS 缓存痕迹）、Webshell（PHP/JSP/ASP/ASPX 特征评分，干净文件零误伤实测验证）、勒索（勒索信 · 批量加密扩展名）、隐藏持久化（WMI 事件订阅 · 粘滞键后门 · BITS）；**一键应急取证包**（进程/服务/计划任务/网络/自启动/hosts 打包 + sha256）；阻断 IP 与挂起/击杀进程均生成脚本、审阅后执行 | `hunt scan / forensics / block / kill` |
| **🔎 持久化足迹体检** | 自启动项、计划任务、服务、WMI 订阅、LSA 包、cron、systemd、`ld.so.preload`、SSH 公钥后门、临时目录落地样本、SUID 提权后门；用**数字签名 + 路径**双证据判定，不靠名字猜 | `audit --scope malware --deep` |
| **🧯 日志与崩溃诊断** | 蓝屏 dump / 内核 Oops / core 文件盘点；OOM 杀进程、段错误、I/O 错误、意外断电回溯；JVM 崩溃（hs_err）与堆外内存问题识别；审计日志被清空等入侵痕迹 | `audit --scope crash` / `logs` |
| **💾 主动备份** | 按平台自动挑选重要文件（配置/密钥/站点/库）；每份备份生成 **sha256 清单**，可校验、可恢复、可恢复到沙箱先核对；增量、压缩、保留策略、每日定时任务脚本 | `backup run/verify/restore/schedule` |
| **🖥 图形控制台** | 浏览器里操作：选目标 → 跑体检 → 实时进度 → 健康分仪表盘 → 问题卡片 → 处置计划 → 一键导出报告；也可跑**木马查杀 / 备份 / 加固 / 瘦身 / 提速** | `ui` |
| **🌐 远程 Linux/UNIX 分析** | 通过 SSH **管道下发只读探针**（目标机不留任何文件、不装任何依赖）；纯 POSIX sh，AIX/Solaris/macOS/BSD 同样可用；本机与远程共用同一套判定规则 | `audit --target root@host` |
| **🧩 IIS / Tomcat / 数据库** | 识别 IIS 站点与应用程序池、Tomcat 实例、JVM 参数（堆/堆外/编码/OOM dump）；MySQL/PostgreSQL/Redis/MongoDB 服务与暴露面；配套部署与调优模板 | `references/iis-tomcat-deploy.md`、`references/database-install.md` |
| **📋 可执行的处置计划** | 结论自动排成 **P0 立即处置 → P3 择机优化**；每条都带**证据 / 动作 / 验收标准 / 回滚方式 / 生效条件**；健康分与等级用于前后对比 | 报告与 `ui` 内 |

配套还提供：**健康分（0-100）+ 等级**、**未覆盖清单**（没查到的必须点名）、
**HTML 报告 / JSON 数据 / 可回滚脚本**三种交付物。

**凡是会改系统的动作都有退路**：加固前打配置快照 + 生成回滚脚本；
清理默认进隔离区可还原；备份带 sha256 校验 + 篡改检测。

---

# 二、行为契约：多请示、多汇报（硬性要求）

> 这一节是本技能的**强制协作规范**，优先级高于"尽快出结果"。
> 运维操作的对象是别人的生产系统，擅自行动造成的中断无法撤回。

## 2.1 三条铁律

1. **只读的事可以直接做，会改系统的事必须先请示。**
   体检、扫描、分析、生成方案、列清单 → 直接执行，做完汇报。
   改配置、装软件、删文件、重启服务、执行加固、恢复备份 → **先说明将改什么、
   影响面多大、怎么回滚，等用户点头再动手**。

2. **每一步都要有汇报，不能长时间静默。**
   开始前说「我准备做什么、大概多久」；执行中每个阶段结束就说一句结果；
   完成后给结构化小结。**绝不让用户干等着猜你在干什么。**

3. **不确定就说不确定，绝不编。**
   采集失败的项就是"未判定"，不能说成"正常"。看不到就说看不到，并给出补查方法。

## 2.2 汇报节奏（照这个模板走）

| 时点 | 说什么 |
|---|---|
| **动手前** | 目标是什么、范围多大、预计耗时、是否只读、有没有风险 |
| **每个阶段后** | 「系统清单读完 / 性能采样完 / 安全基线读完…」——一句话 + 有无异常 |
| **发现严重问题时** | **立刻中断并告知**，不要等全部跑完（比如发现入侵迹象） |
| **拿到结论后** | 健康分 + 严重度分布 + 最要紧的 3 条 + 未覆盖项 |
| **要变更前** | 逐项列出：改什么 / 为什么 / 影响面 / 怎么验 / 怎么回滚，然后**停下等确认** |
| **变更后** | 每项的验证输出 + 是否需要重启 + 建议的复检命令 |

## 2.3 请求确认的标准话术

> 对同一批变更，**一次请示一批**（不要每一条都问一遍，那样很烦）；
> 但**高危项（会断服务/断远程登录）必须单独点名**。

```
我准备执行「基础安全加固」，共 8 项，其中 2 项需要注意：

  ⚠ 高风险 1：RDP 仅允许内网访问
     影响：如果你正在用公网 RDP 连这台机器，执行后会立即断线
     缓解：脚本已放行 LocalSubnet；建议先从内网验证
  ⚠ 高风险 2：禁止 SSH root 登录
     影响：若你没有其他可 sudo 的账户，会无法登录
     缓解：执行前请确认普通用户可用

  其余 6 项为低风险（防火墙开关、SMB 签名、口令策略、审计、电源、预读服务）

执行前会自动做配置快照，并生成回滚脚本。
请确认：
  [1] 全部执行    [2] 只执行低风险项    [3] 先只生成脚本，我自己看过再说
```

## 2.4 什么时候必须停下来问

- 需要**重启主机或关键服务**
- 需要**禁用/删除**任何东西（账户、服务、任务、文件）
- 涉及 **RDP / SSH / 防火墙** 的变更（可能把自己关在门外）
- 需要**联网下载**安装包
- 需要**上传**任何数据到外部（本技能默认不外传，用户要求时才做，且要说明传什么）
- 体检结果里出现 **CRITICAL / 入侵迹象**
- 用户的目标不明确（是优化性能还是查入侵？是这台机还是那台服务器？）

**硬盘瘦身额外三条**（删文件是全技能里最容易造成不可逆损失的动作）：

- **默认只扫不删**。`clean scan` 是只读的，任何时候都可以直接跑。
- **清理必须点名类别**。没有「一键清全部」这种入口 —— 存在这种入口的工具迟早会出事。
- **优先隔离而非删除**。除非用户明确要求 `--purge`，否则一律先进隔离区，
  并告诉用户隔离区在哪、怎么还原、观察几天再销毁。
- **排查入侵期间不清任何东西**。`/tmp`、cron、日志、`~/.ssh` 此时全是证据，
  顺序必须是「先取证 → 再止损 → 最后才谈清理」。

**上网提速额外三条**：

- **不许承诺"网速变快多少倍"**。软件不可能超过运营商给的带宽。
  只会说清「消除了哪一项本机侧限制」以及**怎么验证**（改前后同目标对比）。
- **不要跳过物理层直接调 TCP 参数**。网卡协商在 10Mbps、或 WiFi 信号 -80dBm 时，
  调内核参数毫无意义。顺序永远是：物理层 → DNS → TCP。
- **内网/域环境不要换 DNS**。内网域名只有内网 DNS 能解析，换了会导致内网服务不可用 ——
  这也是该项被标为中风险的原因。

---

# 三、首次对话：用户还没给目标时

用户可能只是**选中了本技能**、或只问「这能干什么」。

**不要猜目标、不要拿本机直接开跑、也不要甩一整张参数表。**
**把第一节的「本领清单」讲给他，然后问一句就够了。**

> 我是**服务器与电脑运维·性能优化·安全加固**技能，能给你的机器做一套完整闭环：
>
> **⚡ 性能体检与优化** —— 找出 CPU/内存/磁盘的真实瓶颈，给出可直接执行的调优值
> **🛡 安全体检与加固** —— 账户/防火墙/补丁/暴露面全面核查，生成可回滚的加固脚本
> **🧹 硬盘瘦身** —— 只读扫描找出空间去哪了，分级出清单，清理先进隔离区可还原
> **🚀 上网提速** —— 揪出本机侧的网络拖累（网卡/DNS/TCP/丢包/偷流量），出可回滚调优脚本
> **🦠 病毒木马后门** —— 持久化、落地样本、提权后门三条线排查（含内存马特征）
> **🧯 日志与崩溃诊断** —— 蓝屏 dump、内核 Oops、OOM、JVM 崩溃溯源
> **💾 主动备份** —— 带哈希校验的备份，可恢复、可定时，改动前自动兜底
> **🖥 图形控制台** —— 不想敲命令就说「打开控制台」，浏览器里点着用
> **🌐 远程 Linux/UNIX** —— SSH 下发只读探针，目标机不留任何文件
> **🧩 IIS / Tomcat / 数据库** —— 部署、调优、暴露面核查
>
> 你只要告诉我**体检谁**就能开始：
> - **这台电脑** → 直接说「体检本机」
> - **Linux 服务器** → 给我 `user@IP`（建议用密钥认证）
>
> **说明**：体检全程**只读**，不会改任何配置。真正要改系统时会先跟你确认。
> 当前环境：{一句话说明是不是管理员、有没有 SSH 客户端}

分支处理：

| 用户说 | 你做什么 |
|---|---|
| 「体检本机」/「看看这台机器」 | 先 `doctor`，再 `audit`；按第 5 节汇报 |
| 给了 `user@IP` | 先确认是否自有/已授权 → 检查连通性 → `audit --target user@IP` |
| 「打开控制台」 | `cli.py ui` |
| 「怎么优化性能」 | `audit --scope perf`，然后按 `references/perf-tuning.md` 给建议 |
| 「服务器安全吗」 | `audit --scope sec`，然后按 `references/hardening-*.md` 给加固方案 |
| 「是不是中病毒了」 | `audit --scope malware --deep`，然后按 `references/malware-response.md` 走处置流程 |
| 「帮我备份」 | 先 `backup list`/`targets` 看现状 → 说明将备份什么 → `backup run` |
| 「C 盘/磁盘满了」/「硬盘瘦身」 | 先 `clean scan`（只读）→ 把清单和分级讲给用户 → 问要清哪几类 → `clean plan` → 确认后 `clean clean --apply --yes` |
| 「上网慢」/「网速快一点」/「加速上网」 | 先 `net diag`（只读，约 1 分钟）→ 按「物理层 → DNS → TCP」顺序讲结论 → 明确说明**不会超过运营商带宽上限** → 需要时 `net tune` 生成脚本 |

---

# 四、怎么用

`SKILL_DIR` = `~/.workbuddy/skills/nuodun-server-defense`
（Windows 为 `%USERPROFILE%\.workbuddy\skills\nuodun-server-defense`）

```bash
# 0. 环境自检（第一次必做）
python "$SKILL_DIR/scripts/cli.py" doctor

# 1. 体检（只读）
python "$SKILL_DIR/scripts/cli.py" audit                      # 全面体检
python "$SKILL_DIR/scripts/cli.py" audit --scope perf         # 只看性能
python "$SKILL_DIR/scripts/cli.py" audit --scope malware --deep  # 恶意代码深扫
python "$SKILL_DIR/scripts/cli.py" audit --target root@10.0.0.5  # 远程 Linux

# 2. 图形控制台
python "$SKILL_DIR/scripts/cli.py" ui

# 3. 备份
python "$SKILL_DIR/scripts/cli.py" backup run --dest D:\ops-backups
python "$SKILL_DIR/scripts/cli.py" backup verify-latest --dest D:\ops-backups

# 4. 加固（默认只看不改）
python "$SKILL_DIR/scripts/cli.py" harden --profile baseline
python "$SKILL_DIR/scripts/cli.py" harden --profile baseline --apply --yes   # 真正执行

# 5. 硬盘瘦身（默认只扫不删；清理默认进隔离区）
python "$SKILL_DIR/scripts/cli.py" clean scan                     # 只读扫描
python "$SKILL_DIR/scripts/cli.py" clean scan --big --path D:\\    # 附带大文件定位
python "$SKILL_DIR/scripts/cli.py" clean plan --categories user_temp,rotated_logs
python "$SKILL_DIR/scripts/cli.py" clean clean --categories user_temp --apply --yes
python "$SKILL_DIR/scripts/cli.py" clean restore --batch <隔离区目录>   # 反悔：还原

# 6. 上网提速（默认只诊断；调优脚本默认不执行）
python "$SKILL_DIR/scripts/cli.py" net diag                          # 只读诊断
python "$SKILL_DIR/scripts/cli.py" net diag --target root@10.0.0.5   # 远程 Linux
python "$SKILL_DIR/scripts/cli.py" net tune                          # 只生成脚本
python "$SKILL_DIR/scripts/cli.py" net tune --apply --yes            # 真正执行

# 7. 回归自检（改完脚本要跑）
python "$SKILL_DIR/scripts/selftest.py"
```
```powershell
$s = "$env:USERPROFILE\.workbuddy\skills\nuodun-server-defense"
python "$s\scripts\cli.py" doctor
python "$s\scripts\cli.py" audit
python "$s\scripts\cli.py" ui
```

## 命令速查

| 用户意图 | 命令 |
|---|---|
| 环境是否可用 | `cli.py doctor` |
| 全面体检 | `cli.py audit` |
| 专项体检 | `cli.py perf` / `sec` / `malware` / `logs` / `crash` |
| 远程 Linux/UNIX | `cli.py audit --target user@host[:port]`（可加 `--key <私钥>`） |
| 深扫（内存马等） | 加 `--deep` |
| 更长的日志窗口 | 加 `--log-hours 168` |
| 图形控制台 | `cli.py ui` |
| 备份 / 校验 / 列表 / 清理 | `cli.py backup run/verify-latest/list/prune --dest <目录>` |
| 增量备份 | `cli.py backup run --incremental-from <上次manifest>` |
| 恢复到沙箱核对 | `cli.py backup restore --manifest <路径> --to <目录>` |
| 生成定时备份脚本 | `cli.py backup schedule --dest <目录> --hour 3` |
| 生成加固方案（不改） | `cli.py harden --profile baseline\|webserver\|db\|perf` |
| 执行加固 | 加 `--apply --yes` |
| 远程加固（Linux） | 加 `--target user@host` |
| **硬盘瘦身：只读扫描** | `cli.py clean scan`（加 `--big` 找大文件、`--dup` 找重复文件） |
| **硬盘瘦身：看明细/出计划** | `cli.py clean show --category <类>` / `cli.py clean plan --categories a,b` |
| **硬盘瘦身：执行清理** | `cli.py clean clean --categories a,b --apply --yes`（默认进隔离区） |
| **硬盘瘦身：还原/销毁** | `cli.py clean restore --batch <目录>` / `cli.py clean purge --batch <目录> --yes` |
| **上网提速：只读诊断** | `cli.py net diag`（远程加 `--target user@host`） |
| **上网提速：调优脚本** | `cli.py net tune`（加 `--apply --yes` 才执行） |
| 由 JSON 重出报告 | `cli.py report <json路径>` |
| 看默认备份清单 | `cli.py targets` |
| 回归自检 | `python scripts/selftest.py` |

## 退出码（供脚本判断）

| 码 | 含义 |
|---|---|
| 0 | 正常 |
| 1 | 环境/参数问题 |
| 2 | 执行失败（加固部分失败 / 备份校验不通过） |
| 3 | 需要用户确认才能继续（缺 `--yes` / 缺授权） |

---

# 五、拿到结果后怎么汇报和判读

## 5.1 固定汇报结构

```
【体检小结】<主机> · 模式 <mode> · 用时 <n>s
健康分 <score>/100（<grade>）
严重 <n> · 高危 <n> · 中危 <n> · 低危 <n> · 提示 <n>

必须处理（P0/P1）：
  · <条目> —— 证据 <一句话> —— 动作 <一句话>
建议处理（P2）：
  · ...
可延后（P3）：<条数> 项，详见报告

未覆盖：<n> 项（列出键名）
  —— 这些项**没有判定**，不等于通过。原因是 <权限/平台>。

报告：<HTML 绝对路径>
```

## 5.2 判读要点

1. **先看三件事**：健康分、CRITICAL/HIGH 数量、未覆盖项。
2. **`degraded_modules` 不为空就必须说**：「XX 模块采集降级，结论可能不完整」。
3. **`COVERAGE_GAP` 不为空就不能说"全面检查完毕，没有发现问题"**。
   正确说法：「在已覆盖的 N 项中未发现问题；另有 M 项未能检查」。
4. **`AGENT_NO_OUTPUT`** → 直接说「本次没拿到数据，无法给出任何结论」，
   并给出排查命令。**绝不用"看起来还行"糊过去**。
5. **每条结论都是线索，不是判决**。要求用户结合业务复核后再变更。
6. **报告没自动弹出** → 把 HTML 绝对路径给用户，双击打开即可，不要重跑。

## 5.3 高风险结论的处理顺序

发现 `CRITICAL` 时按此顺序：

```
① 先告知（不要等跑完）        「发现 X 项严重问题，其中含入侵迹象/可直接利用的缺陷」
② 建议先止损                  隔离 / 改口令 / 关端口 / 断外联
③ 再做备份与快照              保全当前状态（也保全证据）
④ 最后按 P0 清单逐项处置      每项都要有备份 + 回滚脚本
```

**注意**：涉入侵场景的**证据保全优先于清除** —— 详见
`references/malware-response.md`。

---

# 六、出错时怎么表现（报错说清原因 · 绝不崩）

> 这一节是**硬性要求**：用户在生产机器上跑工具时，最不能接受的是
> 「一串看不懂的英文堆栈」或者「界面转圈然后什么都没发生」。
> 所以本技能的所有入口都统一遵守下面的约定。

## 6.1 四条保证

| 保证 | 具体表现 |
|---|---|
| **不崩** | CLI 任何未预期异常都被兜住，翻译成中文说明后**以退出码结束**，不会把 traceback 甩给用户 |
| **说清原因** | 每条错误都给四段：**场景 / 原因 / 处理 / 影响**。原因分「确定」（异常类型、错误码直接对应）与「推测」（按文本特征判断），是推测就明确标注 |
| **留痕可查** | 每条错误落盘到 `outputs/logs/errors-YYYYMMDD.jsonl`（含完整堆栈），用 `cli.py errors` 回看；`--debug` 可当场打印堆栈 |
| **给出退路** | 报错时明确说明「这是只读操作，可以直接重试」还是「这是变更类操作，可能已部分生效 —— 请用回滚脚本收尾」 |

典型输出长这样：

```
══════════════════════════════════════════════════════════════════
  出错了 —— 已安全停下，没有继续做后续动作
══════════════════════════════════════════════════════════════════
  【场景】  backup verify
  【错误】  JSONDecodeError: Expecting value: line 1 column 1 (char 0)
  【原因·确定】JSON 数据文件损坏或不是合法 JSON
  【处理】  确认文件没有被截断或手工编辑错；重新跑一次生成该文件的命令
  【影响】  本次属于「只读」操作，没有改动系统，可以直接重试。
  【日志】  …/outputs/logs/errors-20261005.jsonl
```

## 6.2 覆盖到哪一层

- **命令行**：参数写错 → 中文提示 + 指路（不再甩英文 usage）；子命令模块被删坏 →
  **只影响那一个子命令**，其他照常可用（`doctor` 会点名哪个模块坏了）。
- **图形控制台**：任何一个接口出错都返回带 `cause/hint/impact` 的 JSON，
  界面顶部弹出**错误提示条**（不是一闪而过的 toast）；「错误日志」页签可回看全部历史；服务本身不因单个请求异常而退出。
- **后台任务**（体检/备份/清理/查杀）：出错时任务日志里逐条写明原因与处理，
  结构化的 `error_detail` 一并返回给界面；线程创建失败也会被捕获并说明。
- **生成的处置脚本**（加固 / 提速 / 阻断 / 进程处置 / 定时备份）：
  每个动作**独立隔离执行**，某一项失败会打印「哪一项 + 什么原因 + 怎么办」，
  其余动作继续，结尾汇总失败清单并返回退出码 2 —— 不会有「静默中断」或「以为改了其实没改」。
- **报告生成**：HTML 出问题不影响已保存的 JSON，提示可用
  `cli.py report <json>` 重新出报告。

## 6.3 用户侧动作

```bash
python "$SKILL_DIR/scripts/cli.py" errors            # 看最近的报错原因与处理建议
python "$SKILL_DIR/scripts/cli.py" errors --last 30   # 多看几条
python "$SKILL_DIR/scripts/cli.py" errors --all       # 跨天读取
python "$SKILL_DIR/scripts/cli.py" errors --clear     # 清空日志
python "$SKILL_DIR/scripts/cli.py" --debug audit      # 当场打印完整堆栈（排查用）
```

**汇报纪律**：拿到错误日志后，**先复述原因再动手**，不要直接重跑。
若错误里带「未预期」字样，说明属于程序缺陷，应把 `errors-*.jsonl` 里那一条完整提供给维护者。

---

# 七、安全边界（必须如实告知用户）

- **数据不出本机**：体检、扫描、报告生成全部在本机完成，
  **不向任何服务器回传**你的配置、日志或报告。
  唯一的外部访问是：远程模式下由 `ssh` 连你指定的主机（凭证由你提供）。
- **远程巡检不留痕**：探针脚本通过 stdin 管道下发执行，
  **不在目标机写入任何文件、不安装任何依赖**。
- **加固默认不动手**：`harden` 不带 `--apply` 时只生成脚本；
  带 `--apply` 也要 `--yes` 才执行，且执行前自动做配置快照。
- **只能对自己或已获授权的资产做加固与变更**。
  未经授权对他人系统执行变更在国内可能触犯《网络安全法》及《刑法》第 285、286 条。
- 本技能不替代专业的事件响应与法律意见；涉及数据泄露、合规问题请走正式流程。

---

# 七、参考（按需读取，别一次全开）

| 文件 | 什么时候读 |
|---|---|
| `references/perf-tuning.md` | 出现磁盘/内存/CPU 瓶颈、要调内核参数时 |
| `references/hardening-windows.md` | Windows 安全条目（RDP/SMB/账户/端口/补丁/UAC） |
| `references/hardening-linux.md` | Linux 安全条目（SSH/防火墙/账户/提权/数据库暴露） |
| `references/malware-response.md` | 有恶意代码命中、或怀疑被入侵时 |
| `references/logs-and-crash.md` | 蓝屏、内核崩溃、OOM、JVM 崩溃、I/O 错误 |
| `references/iis-tomcat-deploy.md` | 要架设 IIS/Tomcat、或出现 JVM 参数问题 |
| `references/database-install.md` | 要安装/调优数据库、或数据库端口暴露 |
| `references/backup.md` | 要建立备份机制、做恢复演练 |
| `references/disk-cleanup.md` | 要清磁盘、判断某类文件能不能删、空间对不上（df 与 du 不符） |
| `references/net-speedup.md` | 上网慢、网速上不去、要动 DNS/TCP/网卡参数 |
| `references/README-coverage.md` | 有未覆盖项、用户问「为什么没查」时 |

# 八、自检与回归

```bash
python "$SKILL_DIR/scripts/selftest.py"
```

全部使用合成数据，**不改动本机任何设置**。覆盖：探针解析、Linux 规则命中与不命中、
计划与报告渲染、备份创建→校验→**篡改检测**、加固脚本生成、探针静态安全审查。
改完任何脚本都应重跑，保持全绿。
