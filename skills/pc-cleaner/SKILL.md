---
name: pc-cleaner
display_name: 磁盘清理
display_name_en: PC Cleaner
description: Finds where disk space actually went and gives a ranked, reversible cleanup plan instead of a generic list of tips. It measures before deleting, classifies every candidate as safe / needs-review / do-not-touch, names the exact command or path for each item, and refuses to mass-delete anything it cannot explain. Use when the user asks 磁盘满了、空间不够、清理磁盘、C盘满了、什么占空间、磁盘空间分析、大文件、重复文件、缓存清理、日志清理、卸载残留、磁盘清理、瘦身、释放空间、磁盘占用、磁盘分析、文件夹大小、占空间的目录、系统垃圾、临时文件、清理缓存、清理日志、Docker占空间、node_modules、brew缓存、pip缓存、Xcode占空间、iOS备份、微信缓存、下载文件夹、废纸篓、磁盘映像、 Time Machine 快照、清理建议、能不能删、删了有影响吗、磁盘不够装、存储管理, or says disk full, out of space, clean up disk, free up space, what is taking up space, disk usage analysis, large files, duplicate files, clear cache, clear logs, leftover files after uninstall, disk cleanup, storage analysis, folder size, temp files, docker taking space, node_modules, brew cache, pip cache, Xcode占用, time machine snapshots, is it safe to delete, disk space analyzer, storage management.
description_zh: 先量化空间占用再动手，按安全/需确认/不要动三级分类，给出分平台安全命令与回滚办法，避免误删系统文件。
description_en: Measures disk usage before acting, classifies items into safe / needs-confirmation / do-not-touch tiers, provides platform-specific safe commands and rollback methods, and avoids deleting system files.
category: tools
version: 1.0.0
author: 昆仑增长
allowed-tools: Read, Write, Bash
---

# Disk Space Cleanup

先**量清楚空间去哪了**，再给一个**分级、可回滚**的清理方案 —— 不是一份通用技巧清单。

这类任务的头号风险不是清得不干净，是**删错了**。所以流程把「先量后删」和
「三级分类」做成硬约束：**没有量过就不给删除建议，说不清是什么就不建议删**。

## 何时使用

- 磁盘告急，要立刻腾空间 → 走「快速腾挪」分支，只给最高置信的安全项
- 想知道空间去哪了 → 走「体检」分支，输出占用排名
- 某个目录特别大 → 走「单目录下钻」分支

## 工作流

### 第 1 步：先量，不要猜（必做）

在提任何建议之前，必须先拿到真实的占用数据。

**macOS**：

```bash
# 整体
df -h
# 当前目录下各条目大小（只看一层）
du -h -d 1 . | sort -hr | head -20
# 全盘找大文件（>500MB）
find ~ -type f -size +500M -not -path "*/Library/*" 2>/dev/null
```

**Linux**：

```bash
df -h
du -h --max-depth=1 . | sort -hr | head -20
```

⚠️ 用 `du` 不用 `ls` —— `ls` 显示的是文件逻辑大小，稀疏文件和压缩文件会严重误导。

### 第 2 步：占用排名（Top 10）

把结果整理成一张表，必须包含：

```
路径 | 大小 | 类型（缓存/日志/媒体/依赖/项目/系统） | 处置建议
```

只列前 10 —— 前 10 通常占了 80% 以上，列 50 条没人会看。

### 第 3 步：三级分类（核心）

| 级别 | 判据 | 例子 |
|---|---|---|
| **可安全删** | 删了能自动重建，或明确是临时产物 | 构建缓存、包管理器缓存、已卸载应用的残留、废纸篓 |
| **需确认** | 删了可能影响功能，但通常不影响数据 | 日志、旧版本备份、下载目录、Docker 镜像 |
| **不要动** | 删了会丢数据或破坏系统 | `~/Library` 内部结构、系统目录、未备份的原件、他人的文件 |

🔴 **硬规则**：任何我**说不清用途**的条目，一律归入「需确认」或「不要动」，
**绝不因为它大就建议删**。

### 第 4 步：给具体命令，并给出回滚办法

每条建议都要有：

1. **具体命令或路径**（不是「清理缓存」这种说法）
2. **预期释放多少**（来自第 1 步的实测）
3. **回滚办法** —— 能移就别删：先移到临时目录，确认无影响再删

```bash
# 推荐：先挪走（可回滚），不直接删
mkdir -p /tmp/to-review && mv ~/Library/Caches/SomeApp /tmp/to-review/
```

### 第 5 步：只删能解释的东西

交付前过一遍：每一条删除建议，我能不能用一句话说清「这是什么、谁生成的、删了会怎样」？
说不清就降级为「需确认」。

## 硬约束

- 🔴 **先量后删**：没有 `du`/`df` 实测数据就不给删除建议
- 🔴 **不批量删**：不用 `rm -rf` 通配删除；优先移到临时目录
- 🔴 **不碰说不清的目录**：`~/Library`、系统目录一律不主动建议删
- 涉及他人文件、工作项目目录 → 只提示，不代删
- 不给「清理软件推荐」这类无效建议
- 涉及 Time Machine / 系统快照时说明代价（删了失去恢复能力）

## References

- `references/where-space-goes.md` —— 常见占用大户清单与判据
- `references/safe-commands.md` —— 分平台的安全命令与回滚办法
