---
name: perf-stress-bottleneck
version: 1.1.0
display_name: 性能压测与瓶颈定位法
display_name_en: Performance Stress Test & Bottleneck Location
description: "目标系统后端 API 的主动性能压测与瓶颈定位——API 端到端延迟(speed_test)、边界/性能基线(edge_perf_test)、并发写(concurrency_test) 三件套的口径、运行方式与判读，发版前主动定位瓶颈（与发版检查被动跑互补）。"
description_zh: "目标系统后端 API 的主动性能压测与瓶颈定位——API 端到端延迟(speed_test)、边界/性能基线(edge_perf_test)、并发写(concurrency_test) 三件套的口径、运行方式与判读，发版前主动定位瓶颈（与发版检查被动跑互补）。"
description_en: "Run a three-part perf suite (speed, edge cases, concurrency) against a web backend, read the numbers and locate the real bottleneck."
---

# 性能压测与瓶颈定位法



## 适用场景

发版前或性能投诉时，主动对后端 API 做延迟/边界/并发三维度压测，定位瓶颈。与 `桌面应用发版检查法` 中被动跑的并发/速度检查**互补**（本技能更聚焦“怎么压、怎么读、怎么定位”）。



## 触发词

“接口慢”、“压测”、“并发写会不会丢”、“性能基线”、“瓶颈在哪”、“speed_test”、“edge_perf_test”、“concurrency_test”



## 三件套（均在 scripts/，需后端已在 <API_BASE> 监听，如 http://127.0.0.1:8099）

| 脚本 | 维度 | 口径 |
| :-- | :-- | :-- |
| `speed_test.py` | API 端到端延迟 | 登录拿 token → 各业务端点采样 N 次取 min/avg/max；`trust_env=False` 绕过系统代理（避免 localhost 发往代理） |
| `edge_perf_test.py` | 边界/性能基线 | 独立进程跑（并发写失败会泄漏 DB 连接导致后续全 500，混跑污染结果）；测试数据加前缀 `_KL_` |
| `concurrency_test.py` | 并发写入 | 多客户端同时保存：全部成功、无重复、失败后服务仍健康；测试数据加前缀 `_CONC_` |



## 操作步骤

1. 起后端（源码或安装包版本），确认 `curl http://127.0.0.1:8099/api/health` 返回 200（端口按目标系统替换）。

2. 一键跑三件套（自动顺序执行、汇总）：

   ```bash

   python scripts/perf_suite.py

   ```

   或单独跑某一项：

   ```bash

   python scripts/speed_test.py

   python scripts/edge_perf_test.py

   python scripts/concurrency_test.py

   ```

   所有脚本均从顶部常量或环境变量读取 `API_BASE` / `API_USER` / `API_PASSWORD`，可直接套用。

3. **判读**：

   - speed_test：关注 avg/max，本地回环正常应 < 50ms；某端点异常高 → 查其 SQL/聚合。
   - edge_perf_test：关注异常是否被正确处理（不 500、不泄漏连接）。
   - concurrency_test：关注是否有重复记录、写失败后服务是否仍 200。



## 注意边界

- **edge_perf_test / concurrency_test 必须独立进程跑**，不要并到 e2e/一致性测试同一进程（连接泄漏会污染）。
- 压测会往库里写 `_KL_`/`_CONC_` 前缀的测试数据，跑完务必清理（或还原空库重导），否则污染测试库。
- 走系统代理会把这些 localhost 请求发到代理而超时——脚本已 `trust_env=False`，自己写脚本也要加。
- 只在本机回环压测（内网单机部署），不代表多客户端真实网络表现。



## 关联

- 发版关卡：`桌面应用发版检查法`（独立技能）
- 数据一致性：`数据一致性体检法`（独立技能，提供跨库口径体检）



### 配套脚本与快捷命令

本技能 `scripts/` 内含以下可独立运行的工具，按上方步骤调用即可：

- `scripts/perf_suite.py`：运行 `python scripts/perf_suite.py`（一键顺序跑三件套并汇总判读）
- `scripts/speed_test.py` / `scripts/edge_perf_test.py` / `scripts/concurrency_test.py`：单项压测脚本



## 版本记录

| 版本 | 日期 | 变更 |
| :--: | :--: | :-- |
| v1.1.0 | 2026-10-03 | 补齐 speed/edge/concurrency 三脚本（自包含、纯标准库、参数化），去敏修订 |
| v1.0.0 | 2026-09-30 | 首版实战固化：依据三脚本真实口径封装压测流程 |
