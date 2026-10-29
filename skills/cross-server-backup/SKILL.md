---
name: cross-server-backup
display_name: 跨服务器自动灾备
display_name_en: Cross-Server Automated Backup
description: 配置两台 Linux 服务器之间的跨地域自动备份，使用 rsync over SSH + 宝塔计划任务。适用于数据库/网站文件定时灾备到远端服务器。
description_zh: 配置两台 Linux 服务器之间的跨地域 rsync 自动备份。
description_en: Set up rsync-over-SSH cross-region automated backup between two Linux servers.
category: 运维
version: 1.0.0
author: 澳享家
keywords: ["备份", "rsync", "SSH", "跨服务器", "容灾", "宝塔"]
---

# 跨服务器自动灾备



## 何时使用



当用户需要将一台服务器（生产）的数据库和网站文件定时备份到另一台服务器（灾备）时触发此技能。典型场景：

- 国内服务器 → 海外服务器异地备份

- 宝塔面板 → 另一台宝塔/裸 Linux

- 数据库每小时/每天 + 网站每天/每月的保留策略



## 前置信息



开始之前确认：

1. 源服务器公网 IP、root 密码或已有 SSH 登录方式

2. 目标服务器公网 IP、root 密码

3. 数据库名、数据库用户名、数据库密码

4. 网站目录绝对路径（如 `/www/wwwroot/example.com`）

5. 目标服务器可用磁盘空间



## 核心流程



### 1. 目标服务器放行 SSH



- 腾讯云安全组/轻量云防火墙：入站 TCP 22 仅放行源服务器公网 IP

- 宝塔系统防火墙（如启用）：同样仅放行源服务器 IP



### 2. 目标服务器建目录



```bash

mkdir -p /www/backup/<source_tag>/{db_hourly,db_daily,web,web_monthly}

```



### 3. 源服务器配置 SSH 免密



```bash

ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519

ssh-copy-id -p 22 root@<目标IP>

ssh -p 22 root@<目标IP> 'echo OK'

```



若 `ssh-copy-id` 不存在：

```bash

cat ~/.ssh/id_ed25519.pub | ssh -p 22 root@<目标IP> 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

```



### 4. 源服务器创建数据库密码文件



```bash

cat > /root/.my.cnf <<'EOF'

[mysqldump]

user=<数据库用户>

password=<数据库密码>

EOF

chmod 600 /root/.my.cnf

```



### 5. 生成备份脚本



在本地生成 `backup_<source>_to_<target>.sh`：



- 使用 `tar` 打包网站时，`--exclude` 必须放在目标目录**之前**

- `mysqldump` 加 `--no-tablespaces` 避免 MySQL 8 PROCESS 权限错误

- 若宝塔无自定义 Cron，`db_hourly` 用「每小时」触发，脚本内部按小时过滤



示例 `tar` 正确写法：

```bash

tar czf "$OUT" -C /www/wwwroot \

  --exclude=example.com/runtime \

  --exclude=example.com/public/uploads/tmp \

  example.com

```



### 6. 宝塔计划任务



| 任务名 | 周期 | 脚本 |

|---|---|---|

| DB-Hourly | 每小时 | `bash /root/backup_xxx.sh db_hourly` |

| DB-Daily | 每天 00:00 | `bash /root/backup_xxx.sh db_daily` |

| WEB-Daily | 每天 02:00 | `bash /root/backup_xxx.sh web` |

| WEB-Monthly | 每月 1 号 03:00 | `bash /root/backup_xxx.sh monthly` |



### 7. 验证



每条任务添加后点击「执行」，确认日志输出 `xxx 备份完成`。

在目标服务器检查：

```bash

ls -lh /www/backup/<source_tag>/db_hourly

ls -lh /www/backup/<source_tag>/web

ls -lh /www/backup/<source_tag>/web_monthly

```



## 常见错误



| 错误 | 原因 | 修复 |

|---|---|---|

| `Host key verification failed` | 首次 SSH 未确认指纹 | 先手动 `ssh root@<目标IP>` 输入 `yes` |

| `Access denied; PROCESS privilege` | MySQL 8 表空间导出权限不足 | `mysqldump` 加 `--no-tablespaces` |

| `--exclude has no effect` | `tar --exclude` 位置错误 | 把 `--exclude` 放目标目录之前 |

| 宝塔显示成功但无文件 | 脚本异常被吞或 `set -e` 未生效 | 手动终端跑脚本看完整报错 |



## 安全提醒



- 禁止在源服务器跑 `yum update` / 宝塔系统更新（曾导致 ICU 升级把 PHP 搞挂）

- 数据库密码不要写进脚本，统一用 `/root/.my.cnf`

- 目标服务器 22 端口只对源服务器 IP 开放

