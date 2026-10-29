# 存储与数据卷

## 三种挂载方式对比

| 方式 | 位置 | 性能 | 备份迁移 | 适用 |
|---|---|---|---|---|
| 命名卷 Volume | `/var/lib/docker/volumes/` | 最好 | 容易（volume 命令） | **生产持久数据** |
| 绑定挂载 Bind Mount | 宿主机任意路径 | 中（受宿主 FS 影响） | 靠宿主备份 | 开发、配置文件 |
| tmpfs | 内存 | 最快 | 不需要 | 临时/敏感数据，不落盘 |

```bash
# 命名卷
docker volume create appdata
docker run -d -v appdata:/data myapp:1.0
docker volume ls && docker volume inspect appdata

# 绑定挂载
docker run -d -v /host/conf:/app/conf:ro myapp:1.0

# tmpfs（--tmpfs 或 --mount）
docker run -d --mount type=tmpfs,destination=/tmp,tmpfs-size=64m myapp:1.0
```

> 用 `--mount` 更好：语法明确，路径不存在时**直接报错**而不是静默建目录；`-v` 会把不存在的宿主路径当成空目录创建，是常见“配置怎么是空的”原因。

## 数据卷操作

```bash
docker volume create mydata
docker volume ls -f dangling=true          # 未被使用的卷
docker volume rm mydata                    # 需先无容器使用
docker volume prune                        # ⚠️ 删除所有未使用卷，数据不可恢复

# 备份卷（用临时容器打包，与镜像解耦）
docker run --rm -v mydata:/data -v $(pwd):/backup alpine \
  tar czf /backup/mydata-$(date +%F).tar.gz -C /data .

# 恢复
docker run --rm -v mydata:/data -v $(pwd):/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/mydata-2026-09-13.tar.gz -C /data"

# 卷内文件查看
docker run --rm -it -v mydata:/data alpine sh
```

## 存储驱动与 overlayfs

Linux 上默认 **overlay2**，把多个只读层（镜像层）与一个可写层（容器层）叠加成统一视图：

```
容器可写层 (upperdir)   ← 运行时所有修改（Copy-on-Write）
  ├─ 镜像层 N (lowerdir, 只读)
  ├─ ...
  └─ 镜像层 1 (只读)
```

**Copy-on-Write 关键影响**：
1. 修改只读层里的文件时，会先**整份复制**到可写层再改 —— 所以改大文件（如数据库文件）性能很差，必须用卷
2. 容器删除 → 可写层丢弃 → 数据丢失。**这就是必须挂卷的原因**
3. 容器可写层不宜大量写：`docker diff` 能看到改动，层数越多读写越慢

查看驱动与占用：

```bash
docker info | grep -i storage
ls /var/lib/docker/overlay2 | head
du -sh /var/lib/docker/overlay2         # 容器层占用
docker system df -v                     # 详细空间构成（镜像/容器/卷/构建缓存）
```

兜底方案：`vfs`（无 COW，性能差但兼容任何 FS，仅调试用）；`devicemapper`（已淘汰）。

## 磁盘爆满处理

```bash
df -h                                    # 第一步：确认是不是 /var/lib/docker 所在分区满
docker system df -v                      # 第二步：定位是镜像/容器层/卷/构建缓存哪一类
docker image prune -a                    # 删未被使用的镜像
docker builder prune                     # 删构建缓存（往往回收最多）
docker container prune                   # 删已停止容器
docker volume prune                      # ⚠️ 删未使用卷（先确认无数据依赖）
# 终极：日志文件过大时清空单个容器日志
truncate -s 0 $(docker inspect --format='{{.LogPath}}' <容器>)
```

预分配日志上限（避免再爆）：在 daemon.json 里设 `log-opts.max-size/max-file`，见 @references/concepts-history.md。

## 常见坑点

1. 宿主机路径写错不会报错，Docker 会静默创建空目录 → 用 `--mount` 或先 `ls` 确认
2. SELinux（RHEL 系）挂载要加 `:z`（共享）或 `:Z`（私有），否则容器内 Permission denied
3. 容器内写文件的用户 UID 与宿主机不一致 → 宿主上看文件属主是 root/异常 UID；用 `--user $(id -u):$(id -g)` 或统一镜像内 UID
4. `VOLUME` 指令之后的层对该目录写的内容不会进镜像，容易造成“改了 Dockerfile 但镜像里没变”
5. 不要把数据库数据目录放容器可写层（性能 + 数据双重风险），必须挂卷
