# 三级清理判定与命令（cleanup-levels）

何时读：space_report 已给出磁盘占用，需要决定"删到哪一级、用什么命令、
过不过确认门"时读本文。

## 判定与命令

| 级别 | 目标 | 判定 | 命令 | 确认门 |
|------|------|------|------|--------|
| L1 低风险 | 悬空镜像、已停止容器 | `docker images -f dangling=true`；`docker ps -a --filter status=exited` | `docker image prune` / `docker container prune` | 复述范围后可执行 |
| L2 中风险 | 所有未被引用的镜像（含有标签） | space_report 的 unused_images 清单 | `docker image prune -a` | 确认门：列出将删镜像清单，用户明确同意 |
| L3 高风险 | 悬空卷（可能含数据库文件/状态） | `docker volume ls -f dangling=true` | `docker volume rm <NAME>` 逐个删 | 硬确认门：逐卷点名确认；不使用批量命令 |

## 绝对红线

- `docker system prune -a --volumes` 一律禁止直接执行（会同时清 L2+L3）。
- 卷里可能是数据库数据：L3 之前先让用户确认数据已有备份或确认无用。
- 清理后复核：`docker system df` 确认空间回收符合预期。

## 常见陷阱

- `docker image prune -a` 会删除"当前没有容器引用"的镜像——包括
  服务降级后暂时不用的版本；生产机慎用。
- 构建缓存占用看 `docker system df` 的 Build Cache 行，清理用
  `docker builder prune`（同样过确认门）。
- Windows Docker Desktop 的 WSL2 虚拟磁盘（ext4.vhdx）不会因 prune 自动缩容，
  需要另做 compact；如实告知用户这一限制，不要承诺空间立刻归还宿主机。
