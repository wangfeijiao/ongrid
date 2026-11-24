# 告警抑制、重复触发语义与 APM 服务命名修复：2026-11-24

> 对应提交 `3d20fd5f..262c2973`（6 笔）。台账编号 OPT-024 / OPT-028 / OPT-029 / OPT-030。
>
> **先看第四节**：本次改动在当前生产版本上**并未生效**，原因不是代码问题，而是发版基线被换掉了。

## 一、变更清单

| 提交 | 类型 | 范围 | 台账 |
|---|---|---|---|
| `3d20fd5f` | fix | 告警抑制规则按 `(rule, device_id)` 匹配 | OPT-030 |
| `e0113244` | fix | reopen 重置 `first_fired_at` / `event_count` | OPT-024 |
| `bfb82a7a` | feat | 服务名冲突时自动追加 `@<设备IP>` | OPT-029 |
| `273635e5` | feat | 大屏指标下钻、在线判定收敛、环形图改 SVG | — |
| `9bb7b659` | test | 技能执行断言 `edge_id` → `device_id` | — |
| `262c2973` | chore | 忽略 web 根目录临时验证脚本 | — |

前两笔共享 `IncidentFilter.ActiveOnly`，改动在同一文件交叉，按「抑制」与「重复触发语义」各自独立可编译的方式拆开。

## 二、告警：设备离线不再连带刷屏（OPT-030）

### 现象

一台设备掉线后，`device_offline` 告警之外，同一台机器的 CPU、内存、磁盘、APM 归零告警会同时触发并持续推送。运维要在几十条告警里自己找哪条是根因。

### 根因（两条，缺一都不成立）

1. **抑制器查的键不存在。** 内置抑制规则按固定文本探测根因：`host:<id>:edge_offline`、`pipeline:prom_ingest_fail`。但 pipeline 改造后，评估器产生的键已经是 `pipeline:<rule>:<labelSetKey>`（例如 `pipeline:device_offline:device_id=43,...`），两条规则查不到任何记录，等同死代码。
2. **即使键对上，也关联不到设备。** 评估器只在 `scope == host` 时解析 `device_id`，而 `device_offline` 是 global scope，所以它的 `incident.device_id` 恒为 `NULL`。

### 改动

- `IncidentFilter` 新增 `ActiveOnly`：按「未解决」查根因。只查 single status 会漏掉已 ack 的根因，而不论 open / acknowledged / silenced，只要没 resolved 就仍然覆盖该范围。
- 抑制器改为按 `(rule, device_id)` 和 `(rule)` 查询，不再依赖键文本。
- `pipeline.go` 的 `device_id` 解析放行 `device_offline` / `edge_offline`；`monitoring_pipeline` 保持 `NULL`（归属红线，有既有测试守护，不放宽）。
- 抑制查询失败一律 **fail-open**：宁多推一条，不误吞真告警。

### 验证

`inhibit` 10 项用例（含生产真实 pipeline 键形态回归）、`pipeline` 正反用例（`device_offline` 带 `device_id` / `scrape_down` 保持 `NULL`）。

## 三、告警：重复触发不再沿用首次时间（OPT-024）

### 现象

告警列表里 3 条 `device_offline` 显示「首次触发 2026-10-26」，但设备实际是 11-19 / 11-23 才掉线。看起来像故障挂了一个月。

### 根因

`ReopenIncident` 只更新 `last_fired_at` 并累加 `event_count`，`first_fired_at` 一直停在最初创建那一刻。设备断续离线（offline → resolved → offline）时，时间和次数会无限累积。

### 改动

reopen 代表一次新的故障周期，与首次创建对齐：

- `first_fired_at` 重置为本次触发时间；
- `event_count` 重置为 `1`。

未 resolved 的正常重复触发走 `BumpIncidentFiring`，仍是累加语义，行为不变。

### 数据修正

线上 3 条历史记录已按设备 `last_seen_at` 纠正首次时间：id 69 / 82 / 88。

## 四、APM：多机同名服务不再被合并（OPT-028 → OPT-029）

### 现象

自动探测建 target 时默认取可执行文件名做 `service_name`，于是不同机器上的 `java`、`nginx`，或同机 8080 端口起的多个 java 业务，全部撞成同一个名字。服务列表按 `service_name` 聚合，撞名即合并：一台机器的请求量被算进另一台的总量，归零告警和设备归属同时失真。

实测存量 734 个 target 中，跨设备同名正在发生：`sshd` 30 台、`redis_exporter` 12 台、`postgres` 7 台。

### 改动

从「重名给提示」升级为「冲突时默认安全」：

- 后端 `AutoAPMOptions` 回传按设备去重的 `service_names`，前端据此判断某个名字是否已被其它主机占用；
- 建 target 时若 base name 已被其它设备使用，自动改为 `base@<设备标识>`（有 IP 用 IP，没有则用 `dev<id>`）；不冲突则保持原名，不做无谓改名；
- 输入框下给中性说明，讲清名字为何被改写；options 拉取失败时提示降级风险（无法判重，可能再次撞名）。

### 命名约束（实现时踩过的坑）

- 不能用设备名做后缀，会重名；必须用 IP；
- 不能把设备塞进 `service_namespace`，否则服务详情页的日志过滤（前端 `eq` 匹配）会断掉；
- 名字不能含 `/`，OBI 会把它解析成 namespace。

## 五、体验改进：大屏下钻与在线口径

- 大屏指标格接到能复现该数字的地址：在线/离线设备 → `/devices?online=1|0`，告警闭环率 → `/alerts?status=resolved`，风险预警 → `/alerts`；设备列表增加「N 台在线 · 共 M 台」与「查看全部」；告警列表支持从 URL 预置 `status` / `severity`。
- 在线判定收敛到 `lib/deviceStatus`：SNMP 纳管的网络设备没有 agent 心跳、`online` 恒为 false，只能看 `reachability_status`；其余设备看 `online`。这套规则原先在大屏和设备列表各写一份，已出现过口径漂移。
- 环形图从 CSS `conic-gradient` 改为 SVG：原实现尺寸由宽度驱动（`aspect-ratio:1` 且不限高），在矮面板里会溢出，色带也偏粗；SVG 用 `viewBox` + `preserveAspectRatio` 跟随较短边缩放，并支持逐段 hover。

## 六、验证记录

| 项 | 结果 |
|---|---|
| `tsc -b --noEmit` | 0 错误 |
| 前端测试（改动相关 6 个文件） | 47 / 47 通过 |
| `eslint`（改动文件） | 0 error |
| `go build ./internal/manager/...` | 通过（服务器干净目录） |
| `go test biz/alert`、`biz/edge`、`data/alert/store` | 全绿 |
| 全量 `go test ./internal/manager/...` | 仅 4 个无关包失败，见下 |

`go build` 与 `go test` 在服务器上以「`git archive HEAD` 导出 + 叠加工作区改动」的干净目录执行，未污染任何构建目录，验证后已删除。

全量测试失败的 4 个包均与本次改动无关：`chatruntime` v1/v2/v3 因导出时测试用符号链接在 Windows 上无法创建（testdata 缺失），`externalmcp` 的 API catalog 一致性断言在 HEAD 上即失败。

**注意**：本地全量 `vitest`（103 个文件）会因并行抢占超时误报，单次跑 4~6 个文件全绿。判断真假失败前先看是否 timeout。

## 七、发版现状（重要）

**本次改动在当前生产上未生效。**

| 版本 | 基点 | 是否含本次改动 | 依据 |
|---|---|---|---|
| v0.21.0-opt4 | v0.21.0 | 是（抑制 + reopen） | 镜像内 `grep -ac ActiveOnly /ongrid` = 1 |
| v0.21.0-opt5 | v0.21.0 | 是（含 APM 命名） | 同上 = 1 |
| **v0.22.0-opt6（当前后端）** | **官方 v0.22.0** | **否** | 同上 = 0 |
| **v0.22.0-opt7（当前前端）** | **官方 v0.22.0** | **否** | v0.22 源码无 `web/src/lib/deviceStatus.ts` |

原因：11-23 21:17 生产升级到 `v0.22.0-opt6`，构建源是 `/home/ubuntu/ongrid-build`（`VERSION=v0.22.0`，官方基线），该目录里 `inhibit.go` 无 `ActiveOnly`、`AutoAPMCard.tsx` 无 `hostScopedName`、`autoapm_options.go` 无 `service_names`。也就是说 opt4/opt5 的本地优化在换到 v0.22 基线后**没有被带过去**，与 OPT-019 记录的情形相同。

### 要上线还需要

1. 把本次 6 笔提交移植到 v0.22.0 基线（`ongrid-build` 当前是官方源码，需要以它为底重放改动，而不是整体覆盖）；
2. 重新构建镜像并发版，发版后立刻用 `grep -ac ActiveOnly /ongrid` 自检，确认这次没被别的流程覆盖；
3. 前端同步构建，检查产物中存在大屏下钻与 `@IP` 后缀逻辑。

在此期间，**不要对外声称「设备离线不再刷屏」已经生效**。

## 八、回滚

- 代码：`git revert` 对应提交。
- 若已发版：切回上一版镜像（`.env` 改 `ONGRID_VERSION` + `docker compose up -d ongrid`），前端恢复上一版 `assets` 目录。

## 九、遗留

- **存量 target 仍是治理前的口径**：v0.22 基线上的 `host_plugin_configs` 未受本次代码影响，OPT-031 的 194 条清理只在数据库层面生效过，重新发版不会回滚数据，但也不代表配置已按新命名规则重写。
- **11 个高价值服务未改名**（`java-9300`、`nginx-8081` 等）：改 `service_name` 会改告警 `dedupe_key`，正在 firing 的会变成新 incident 刷屏，需要先压告警再改。
- **32 条 offline 设备的 target 未判定**，设备恢复后重跑 `scripts/governance/` 下的脚本增量治理。
- **`v0.22.0` 源码未回流仓库**：服务器上的构建源与 git 仓库已分叉（还包含仓库里没有的 `internal/pkg/llm/context_window.go`），后续发版前必须先比对，不能默认「线上 == 仓库」。
