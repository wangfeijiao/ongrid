# 破坏性操作告警与确认步骤（dangerous）

本文件为离线参考。凡生成下列命令，必须主动在输出中标注 **⚠️ 破坏性** 风险，
并给出确认步骤，禁止静默执行。

---

## 1. `docker rm -f` —— 强制删除运行中的容器

```bash
docker rm -f <container>
docker rm -f $(docker ps -q)      # 强删所有运行中容器
```
**风险**：
- 立即终止运行中进程，未保存的数据（内存态、未落盘写入）直接丢失。
- 若容器挂载了命名卷，卷本身不会被删，但容器内运行态数据无法恢复。

**确认步骤**：
1. 先 `docker ps` 确认目标容器，避免误删生产容器。
2. 优先使用 `docker stop <container>` 优雅停止；确有需要再 `rm -f`。
3. 批量删除前用 `docker ps -a` 人工核对清单。

---

## 2. `docker system prune -a` —— 清理镜像与构建缓存

```bash
docker system prune -a
docker system prune -a --volumes   # 额外删除未使用卷（更危险）
```
**风险**：
- 删除所有已停止容器、未使用网络、构建缓存（build cache）。
- 加 `-a` 后删除**所有未被任何容器引用**的镜像，不仅是悬空镜像。
- 加 `--volumes` 会删除未使用的命名卷，造成持久化数据丢失。

**确认步骤**：
1. 先 `docker system df` 查看占用，确认清理收益。
2. 确认没有需要保留但未运行容器的镜像（如待发布版本）。
3. 除非明确知晓后果，否则不要加 `--volumes`。
4. 生产环境建议先在测试机验证。

---

## 3. `docker rmi` —— 删除镜像

```bash
docker rmi <image>
docker rmi -f <image>
docker rmi $(docker images -aq)    # 删除所有镜像
```
**风险**：
- 镜像一旦删除需重新从仓库拉取或重新构建，耗费时间与带宽。
- `-f` 强制删除被引用镜像可能导致依赖它的容器无法重建。
- 删除所有镜像会让本机失去离线运行能力。

**确认步骤**：
1. `docker images` 核对镜像 ID / 标签。
2. 确认该镜像不再被任何需保留的容器引用（`docker ps -a`）。
3. 优先用 `docker image prune` 只删悬空镜像。

---

## 4. `docker volume rm` —— 删除卷（数据丢失）

```bash
docker volume rm <volume>
docker volume rm $(docker volume ls -q)   # 删除所有卷
```
**风险**：
- 卷中持久化数据（数据库文件、 uploads 等）**不可恢复**。
- 若仍有容器挂载该卷，删除会失败；但已卸载的卷数据直接消失。

**确认步骤**：
1. `docker volume ls` + `docker volume inspect <volume>` 确认内容与挂载者。
2. 备份卷数据（如先 `docker run --rm -v <volume>:/data -v $(pwd):/backup alpine tar czf /backup/vol.tar.gz /data`）。
3. 确认无任何容器依赖后再删除。

---

## 5. `docker-compose down -v` —— 停止并删除卷

```bash
docker compose down -v
docker-compose down -v
```
**风险**：
- `-v` 会删除 compose 文件中声明且未被其他容器使用的**命名卷**与服务网络。
- 数据库类服务（postgres/redis/mysql）数据将全部丢失。

**确认步骤**：
1. 先 `docker compose down`（不带 `-v`）仅停止并移除容器/网络，保留卷。
2. 确需重置数据时才加 `-v`，并确认已备份或数据可重建。
3. 生产环境严禁对含数据库服务的 compose 执行 `down -v`。

---

## 通用兜底规则

- 任何破坏性命令输出末尾必须附 **⚠️ 破坏性** 标识与"执行前请确认"提示。
- 涉及数据（卷/镜像/构建缓存）删除时，先建议用户运行查看类命令（`ps`/`images`/`volume ls`/`system df`）核对。
- 若用户意图模糊（如"清一下垃圾"），优先推荐最温和的清理（`docker image prune`、`docker container prune`），而非 `prune -a`。
- 生产环境操作一律提示二次确认与备份。
