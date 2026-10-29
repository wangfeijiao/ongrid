# 空间去哪了：常见占用大户与判据

按「通常占用量 × 清理安全度」排序。每一类都给出**怎么确认**和**能不能删**。

---

## 一、开发类（通常是最大的一块）

| 项 | 典型大小 | 判据 | 能不能删 |
|---|---|---|---|
| `node_modules` | 每个项目 200MB~2GB | `du -sh node_modules` | **能**（`npm ci` 可重建），但先确认项目还在不在用 |
| Docker 镜像/卷 | 10~100GB | `docker system df` | **部分能**（悬空镜像/停止的容器安全；正在用的卷不行） |
| 包管理器缓存 | 1~10GB | `brew --cache` / `pip cache dir` | **能**（可重建） |
| 构建产物 | 1~20GB | `dist/` `build/` `.next/` | **能**（重新构建即可） |
| Xcode 派生数据 | 10~50GB | `~/Library/Developer/Xcode/DerivedData` | **能**（重新构建会生成） |
| iOS 设备备份 | 每个 5~20GB | `~/Library/Application Support/MobileSync/Backup` | **需确认**（删了无法恢复设备） |
| 虚拟环境 | 每个 500MB~2GB | `.venv` `venv` | **能**（可重建），但要确认 requirements 在 |

🔑 **判据**：「删掉后能不能用一条命令重建」—— 能重建的就是缓存，可安全删。
**不能重建的就是数据，一律不主动建议删。**

---

## 二、系统与日志类

| 项 | 典型大小 | 判据 | 能不能删 |
|---|---|---|---|
| 系统日志 | 几百 MB~几 GB | `/var/log`、`~/Library/Logs` | **需确认**（排障时可能要用） |
| Time Machine 本地快照 | 几 GB~几十 GB | `tmutil listlocalsnapshots /` | **需确认**（删了失去本机恢复能力） |
| 废纸篓 | 不定 | 各卷 `.Trash` | **能**（本就是要删的） |
| 下载文件夹 | 通常最大 | `~/Downloads` | **需确认**（用户自己才知道哪些还要） |

⚠️ **Time Machine 快照不要主动建议删** —— 它是唯一的本机恢复手段。
要说明代价：删了就失去「误删后找回」的能力。

---

## 三、应用残留

卸载应用后常留下的东西：

```
~/Library/Application Support/<AppName>/
~/Library/Caches/<AppName>/
~/Library/Preferences/<bundle-id>.plist
~/Library/Containers/<bundle-id>/
```

**判据**：先确认应用确实已卸载（`ls /Applications` 里没有了），再清残留。
应用还在却删了它的 Application Support，会丢数据。

---

## 四、媒体文件

| 项 | 判据 | 能不能删 |
|---|---|---|
| 视频/电影 | `find ~ -size +1G -name "*.mp4"` | **不要动**（多半是要留的） |
| 照片图库 | `~/Pictures/Photos Library.photoslibrary` | **不要动**（删了丢照片） |
| 屏幕录制 | `~/Movies` | 需确认 |

🔑 **媒体文件一律归入「需确认」** —— 它们通常是用户主动保存的，
体积大不代表可以删。**这是最容易误删的一类。**

---

## 五、怎么快速定位

```bash
# ① 先看整体
df -h

# ② 主目录下逐层找（第一层）
du -h -d 1 ~ | sort -hr | head -20

# ③ 对最大的那个目录继续下钻
du -h -d 1 ~/Library | sort -hr | head -20

# ④ 全盘大文件
find ~ -type f -size +500M 2>/dev/null | head -30
```

⚠️ **`du` 在外部卷或网络卷上会很慢**，先限定在 `~` 内。
⚠️ 用 `du` 不用 `ls -lh` —— `ls` 显示逻辑大小，稀疏文件会严重虚高。

---

## 六、输出格式

```
## 磁盘现状
总容量 / 已用 / 可用（来自 df -h）

## 占用排名（Top 10）
| 路径 | 大小 | 类型 | 分级 | 建议 |

## 可安全清理（预计释放 X GB）
1. <命令>   ← 释放 <实测值>

## 需要你确认（我不会自动删）
1. <路径> <大小> —— 这是什么、删了会怎样

## 不要动
1. <路径> —— 为什么

## 回滚办法
先 mv 到 /tmp/to-review，确认 N 天无影响再删
```

🔴 **「需要你确认」这一栏不能省** —— 它是「不删说不清的东西」这条纪律的落地形式。
