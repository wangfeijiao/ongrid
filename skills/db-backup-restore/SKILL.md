---
name: db-backup-restore
version: 1.0.0
display_name: 数据库备份恢复法
display_name_en: Database Backup & Restore
description: "目标系统 SQLite 按年分库的备份与恢复：自动每日备份(data/backup/YYYY-MM-DD)、手动备份(backup_db.py/POST /api/backup)、保留策略(日常7天/rollback快照5份)、WAL安全的整库复制，以及闭环恢复(恢复前自动另存rollback快照、完整性校验、绝不半途覆盖)。当用户说\\\"备份数据库、恢复数据库、回滚、数据丢了、库坏了、rollback、data/backup、导入前先备份\\\"时调用。"
description_zh: "目标系统 SQLite 按年分库的备份与恢复：自动每日备份(data/backup/YYYY-MM-DD)、手动备份(backup_db.py/POST /api/backup)、保留策略(日常7天/rollback快照5份)、WAL安全的整库复制，以及闭环恢复(恢复前自动另存rollback快照、完整性校验、绝不半途覆盖)。当用户说\\\"备份数据库、恢复数据库、回滚、数据丢了、库坏了、rollback、data/backup、导入前先备份\\\"时调用。"
description_en: "Back up SQLite databases safely (WAL-aware copy, yearly partitioning), automate schedules, and close the loop with a tested restore including rollback snapshot."
---

# 技能｜数据库备份恢复法




> 实战固化于 2026-09-30 场馆目标系统运维。



## 适用场景

系统数据落在 **SQLite 按年分库**（`data/目标系统.db` 当年 + `data/目标系统_YYYY.db` 历史年）。数据一旦损坏/误导入，必须有可靠的备份与恢复手段。本技能覆盖「自动+手动备份 → 保留策略 → 安全恢复（闭环）」全链路，并给出不依赖后端包的独立运维脚本。



## 触发词

备份数据库、恢复数据库、回滚、数据丢了、库坏了、rollback、data/backup、导入前先备份、SQLite按年分库



---



## 一、库结构与红线

- 库位置：`<项目根目录>/程序源代码\data\`

  - `目标系统.db`：当前年

  - `目标系统_2024.db` / `目标系统_2025.db` / `目标系统_YYYY.db`：历史年（按年分库）

- **红线**：不要手动改名/移动 db 文件，库内存的是相对/绝对路径，改名会让索引失效。

- 备份根：`data/backup/`，日常备份在 `data/backup/YYYY-MM-DD/`，恢复前快照在 `data/backup/rollback_<时间戳>/`。



## 二、备份（三类方式）

1. **自动**：后端 `backend/backup.py` 启动后后台线程每日首次启动备份一次到 `data/backup/YYYY-MM-DD/`（当天已备则跳过）。

2. **手动（推荐运维用）**：`python scripts/backup_db.py` 或 管理员 `POST /api/backup`；强制备份用 `python -c "from backend import backup; backup.run_backup(force=True)"`。

3. **独立脚本**：本技能 `scripts/db_backup_restore.py backup` —— 不依赖后端包，直接复制三个库（WAL 安全）。



## 三、保留策略

- 日常备份：默认保留最近 **7 天**，超出自动清理最旧一天。

- `rollback_` 恢复前快照：单独保留最近 **5 份**，不被日常策略清掉（回滚点不丢）。



## 四、WAL 安全复制（关键）

- SQLite 开了 WAL，单纯 `shutil.copy2` 只复制主库会丢 `-wal` 里未落盘的写入。

- 备份用 **SQLite 官方 backup API**（`sqlite3` 的 `con.backup()`）整库复制，不可用时退回「先 `PRAGMA wal_checkpoint(TRUNCATE)` 再 copy2」。

- 复制完清掉目标旁旧的 `-wal`/`-shm` 残留，避免与新主库版本不一致。



## 五、恢复（闭环，安全设计）

调用 `restore_from(day)`（或 `db_backup_restore.py restore <YYYY-MM-DD>`）：

1. **恢复前先另存**：把当前库复制到 `data/backup/rollback_<时间戳>/`，随时可再退回。

2. **完整性校验**：备份缺任一库文件直接拒绝，绝不半途覆盖。

3. **安全复制回写**：用 backup API 把备份复制回 `data/`。

4. 恢复后**建议重启程序**生效。



> 回滚：`db_backup_restore.py restore rollback_20260930_xxxx` 即可退回恢复前的状态。



## 六、运维铁律

- **任何导入/升级前先备份**：`backup_db.py` 跑一次，或升级流程内置备份步骤。

- 升级失败需要回退：先从当天备份 `restore`，若已误写入则走 `rollback_` 快照。



## 注意边界

- 按年分库：每年一个 db 文件，恢复时三个库一起处理，不要只恢复一个。

- 恢复的库与当前程序版本要匹配（大版本结构变更需确认迁移）。

- 备份目录不要手动删，保留策略会自动清理。

- `data/backup/pre_*` 是导入前的整库预备份，也是一种回退点。



## 反例避雷

| 反例 | 为什么错 | 正确做法 |

| :-- | :-- | :-- |

| 升级/导入前不备份 | 失败无法回滚 | 先 `backup_db.py` |

| 用 `shutil.copy2` 只拷主库 | 丢 WAL 未落盘写入 | 用 backup API 整库复制 |

| 恢复不校验直接覆盖 | 半途覆盖导致坏库 | 先校验完整性、另存 rollback 快照 |

| 手动改 db 文件名 | 索引失效 | 永远不手动改名 |



## 关联

- 部署：`06-成品交付-服务端内网部署运维法`（含备份策略章节）

- 代码：`backend/backup.py`（`run_backup`/`restore_from`/`list_backups`/`auto_backup_async`）、`scripts/backup_db.py`

- 项目：`<项目根目录>/`（FastAPI + SQLite 按年分库）



### 配套脚本与快捷命令

本技能 `scripts/` 内含以下可独立运行的工具，按上方步骤调用即可：

- `scripts/db_backup_restore.py`：运行 `python scripts/db_backup_restore.py`（一般带 `--help` / `--dry-run` 先看用法；修复类默认 dry-run，加 `--apply` 才落库）



## 版本记录

| 版本 | 日期 | 变更 |

| :--: | :--: | :-- |

| v1.0.0 | 2026-09-30 | 首版实战固化：自动/手动备份、保留策略、WAL 安全复制、闭环恢复 + 独立运维脚本 |

