---
name: network-probe
version: 1.0.0
display_name: 网络探针
display_name_en: Network Probe
description_zh: "网络探针运维技能，覆盖三大场景——(1) 网络状态自动识别（自动 ping，分层定位断网层级：本地/网关/DNS/外网）；(2) 内网私接无线路由器发现、定位其所在交换机端口并断网处置；(3) 内网设备“无网络”原因自动诊断并出报告。当用户要求“自动 ping 测网络”“查私接路由/私接 AP/非法 DHCP”“找出谁接了无线路由器并断掉”“某台设备上不了网/无网络怎么排查”“生成网络诊断报告”时使用。支持跨网段、纯标准库、可对接可网管交换机 SNMP。"
description_en: "Network probe operations skill covering three scenarios: (1) automatic network status detection via layered ping (local stack/gateway/DNS/Internet); (2) rogue wireless router discovery on internal networks, locating its switch port, and cutting off its access; (3) automatic root-cause diagnosis for devices with no network, with report generation. Pure standard library; optional SNMP switch integration."
description: "网络探针运维技能，覆盖三大场景——(1) 网络状态自动识别（自动 ping，分层定位断网层级：本地/网关/DNS/外网）；(2) 内网私接无线路由器发现、定位其所在交换机端口并断网处置；(3) 内网设备“无网络”原因自动诊断并出报告。当用户要求“自动 ping 测网络”“查私接路由/私接 AP/非法 DHCP”“找出谁接了无线路由器并断掉”“某台设备上不了网/无网络怎么排查”“生成网络诊断报告”时使用。支持跨网段、纯标准库、可对接可网管交换机 SNMP。"
---

# Network Probe — 网络探针

## 概述

面向桌面运维/网络管理的一体化探针技能。三大功能：

1. **网络状态自动识别**：自动读取本机网关/DNS 并分层 ping（本地协议栈 → 网关 → DNS → 外网），逐层判定断网位置，给出结论。
2. **私接无线路由器发现与处置**：ARP 扫描内网，结合家用路由/AP 厂商 OUI 白名单与**交换机 MAC 表多 MAC 端口法**定位私接路由，给出其 IP、MAC、厂商、**所在交换机端口号**，并在授权确认后关闭该端口断网（或输出 MAC 供网关/网康 ICG 黑名单阻断）。
3. **设备无网络原因诊断**：对指定设备（或批量网段）做分层推理（在线/ARP/交换机端口状态/错包），定位 L2 物理故障、端口 shutdown/err-disable、IP 冲突、终端 DNS/DHCP 错配等根因，输出结构化报告。

## 脚本清单

| 脚本 | 作用 | 依赖 |
|---|---|---|
| `scripts/net_status.py` | 自动 ping 分层探测网络状态 | 纯标准库 |
| `scripts/rogue_scan.py` | ARP 扫描 + 私接路由发现 + 端口定位 + 断网处置 | 纯标准库；交换机定位需 pysnmp |
| `scripts/switch_ops.py` | 交换机 SNMP 操作（MAC→端口映射、端口状态、关/开端口） | pysnmp（可选） |
| `scripts/diagnose.py` | 设备无网络分层诊断 | 纯标准库；端口级诊断需 pysnmp |
| `scripts/make_report.py` | 汇总三类产物生成《网络探针诊断报告》 | 纯标准库 |
| `scripts/env_setup.py` | 依赖自检/一键安装（托管 venv） | — |
| `scripts/config.example.json` | 配置示例（网段/网关/交换机/社区，按环境改） | — |

> 仅"交换机 SNMP 集成"需要 `pysnmp`：先跑 `python scripts/env_setup.py`。其余功能零依赖。

## 工作流程

### 功能一：网络状态自动识别（自动 ping）
```bash
python scripts/net_status.py                  # 自动读取本机网关/DNS 分层 ping
python scripts/net_status.py --json --out status.json
```
- 分层模型：L0 本地协议栈 → L1 网关 → L2 DNS 解析 → L3 外网连通。
- 逐层判定，自动给出断网层级结论（如"网关不可达：检查物理连线/交换机端口/VLAN"）。
- 贵司环境运行时会自动识别真实网关（如 172.16.8.1），也可用 `--gw/--dns/--target` 显式指定。

### 功能二：私接无线路由器发现 + 定位端口 + 断网
```bash
# 第一步：扫描网段，发现候选（dry-run，仅输出建议）
python scripts/rogue_scan.py --subnet 172.16.8.0/24 --gw 172.16.8.1

# 有可网管交换机时：定位私接路由所在端口（需先 env_setup 装 pysnmp）
python scripts/rogue_scan.py --subnet 172.16.8.0/24 --switch 172.16.8.254 --ro public --rw private

# 确认处置：真正关闭其交换机端口（断网）。必须 --confirm 才执行，默认只建议
python scripts/rogue_scan.py --subnet 172.16.8.0/24 --switch 172.16.8.254 --rw private --cut-off --confirm
```
- **发现信号（按置信度）**：① 交换机某 access 端口下学习到 ≥5 个 MAC（后面接了私接路由/AP/小交换机）→ 高；② 设备 MAC 属于家用路由/AP 厂商 OUI 且非官方网关 → 中。
- **输出**：私接路由 IP、MAC、厂商、**交换机端口号**、下挂终端数。
- **断网处置**：
  - 优先：经授权关闭对应交换机端口（`--cut-off --confirm`，SNMP SET ifAdminStatus=2）。
  - 无网管交换机：输出其 MAC，在网关/网康 ICG 加黑名单阻断（网络层）。
- ⚠️ 关闭端口属变更操作，**必须** `--confirm` 且操作前确认影响范围（避开 VoIP/打印机/门禁等）。
- OUI 白名单在 `scripts/rogue_scan.py` 的 `ROUTER_OUIS`，可按自有设备清单扩充（精确 6 位前缀）。

### 功能三：设备无网络原因诊断
```bash
python scripts/diagnose.py --target 172.16.8.56           # 单设备
python scripts/diagnose.py --target 172.16.8.56 --mac aa:bb:cc:dd:ee:ff --switch 172.16.8.254 --ro public
python scripts/diagnose.py --subnet 172.16.8.0/24 --switch 172.16.8.254   # 批量：标出不可达设备并诊断
python scripts/diagnose.py --target 172.16.8.56 --json out.json
```
- 推理链：能 ping 通 → 在线（多为终端侧 DNS/DHCP 错配或代理）；不可达 → 查 ARP（无 MAC=L2 离线/VLAN/端口问题；有 MAC=防火墙拦截/IP 冲突）；有交换机时再查端口 admin/oper 状态、错包计数（err-disable/线缆故障）。
- 输出每设备：根因推断 + 处置建议。

### 汇总报告
```bash
python scripts/make_report.py --status status.json --rogue rogue.json --diag diag.json --out 网络探针诊断报告.md
```

## 安全与合规
- 探测与接入仅限用户**自有/授权**的网络与设备；扫描与关闭端口走变更审批。
- 关闭交换机端口（断网）是破坏性操作：`rogue_scan.py` 默认仅输出建议，**必须 `--confirm` 才真正执行**。
- SNMP 写社区属敏感凭据，用 `config.example.json` 模板，勿硬编码真实口令入仓库；上传公共平台前清除真实 IP/社区。
- 自本机向外的 ICMP/ARP 探测为只读扫描；不得用于未授权网络。

## 关键 MIB / 端口（详见 `references/switch-snmp`）
- 桥 MAC 表：1.3.6.1.2.1.17.4.3.1.1/.2；桥端口→ifIndex：.17.1.4.1.2
- 端口状态：ifAdminStatus .2.2.1.7（可写，2=down）、ifOperStatus .2.2.1.8、ifDescr .2.2.1.2
