# 安全命令与回滚办法

原则：**能移就不删，能重建才删，说清是什么才删。**

---

## 一、通用安全规则

### 1. 先挪后删（唯一推荐的删除姿势）

```bash
mkdir -p /tmp/to-review
mv <目标> /tmp/to-review/
# 用几天，确认没影响，再删 /tmp/to-review
```

`mv` 在同卷内是**瞬间**完成的（只改元数据），比 `cp` 快几个数量级。
跨卷才会真拷贝。

### 2. 删之前先列出来看一遍

```bash
# 先看会命中什么，再决定删不删
find ~/Library/Caches -maxdepth 1 -type d
# 确认无误后再删，且限定深度
```

🔴 **绝不执行没有先看过清单的删除命令。**

### 3. 不用通配 rm -rf

```bash
❌ rm -rf ~/Library/Caches/*        # 通配，命中什么不可控
❌ find . -name "*.log" -delete     # 没先看清单就删
✅ 先 find 看清单 → 确认 → 再逐条 mv
```

---

## 二、macOS

### 查看

```bash
df -h                                    # 整体
du -h -d 1 ~ | sort -hr | head -20       # 主目录第一层
du -h -d 1 ~/Library | sort -hr | head   # 下钻
```

### 可安全清理（可重建类）

```bash
# 包管理器缓存
brew cleanup --dry-run                   # 先看会清多少
brew cleanup
pip cache purge
npm cache clean --force
yarn cache clean

# Docker（先看，再清）
docker system df                         # 看占用
docker image prune                       # 只清悬空镜像（安全）
docker container prune                   # 只清停止的容器

# Xcode 派生数据（会重新生成）
rm -rf ~/Library/Developer/Xcode/DerivedData

# 废纸篓（本就是要删的）
rm -rf ~/.Trash/*
```

⚠️ `docker system prune -a` 会删掉**所有未被容器引用的镜像**，
包括你精心拉取的基础镜像 —— 用之前先确认。

### 需确认类

```bash
# 本地 Time Machine 快照（删了失去本机恢复能力）
tmutil listlocalsnapshots /
# 删除某个快照（用上面列出的日期）
tmutil deletelocalsnapshots <日期>

# iOS 备份（删了无法恢复设备）
ls -la ~/Library/Application\ Support/MobileSync/Backup/
```

---

## 三、Linux

### 查看

```bash
df -h
du -h --max-depth=1 ~ | sort -hr | head -20
# 交互式分析器（若已安装）
ncdu ~
```

### 可安全清理

```bash
# 包缓存
apt-get clean          # Debian/Ubuntu
dnf clean all          # Fedora/RHEL
yum clean all          # CentOS
pacman -Scc            # Arch

# 日志（按时间而不是全删）
journalctl --disk-usage
journalctl --vacuum-time=7d      # 只保留 7 天

# 旧内核（确认当前内核不在列表里）
uname -r                         # 先看当前内核
dpkg --list 'linux-image-*'      # 再看装了哪些
```

🔴 **删旧内核前必须先确认当前运行的那个不在删除列表中** ——
删了当前内核会直接开不了机。

---

## 四、Windows（PowerShell）

```powershell
# 查看各盘
Get-PSDrive -PSProvider FileSystem

# 组件存储（需管理员）
Dism.exe /Online /Cleanup-Image /AnalyzeComponentStore
Dism.exe /Online /Cleanup-Image /StartComponentStoreCleanup

# 临时文件
cleanmgr /sagerun:1      # 磁盘清理
```

⚠️ 不要手动删 `C:\Windows\WinSxS` —— 用上面的官方命令。

---

## 五、删除后的验证

```bash
df -h              # 确认确实释放了
```

⚠️ **有时删了但空间没释放**，常见原因：

1. **文件还被进程占用** —— 删了 inode 但空间没回收。
   `lsof | grep deleted` 找出占用进程，重启它。
2. **在容器/虚拟机里** —— 宿主侧的磁盘映像不会自动缩小。
3. **快照占用** —— Time Machine / 虚拟机快照仍持有空间。

---

## 六、交付前的自检

- [ ] 有 `df`/`du` 的实测数据吗（不是凭感觉）？
- [ ] 每条删除建议都能说清「这是什么、谁生成的」吗？
- [ ] 用 `mv` 而不是直接 `rm` 吗？
- [ ] 有没有通配 `rm -rf`？
- [ ] Time Machine / 备份类是否标了代价？
- [ ] 媒体文件是不是都归到「需确认」了？
- [ ] 给了删除后的验证命令吗？
