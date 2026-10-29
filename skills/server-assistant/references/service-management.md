# 进程与服务管理：systemd 完整实战

覆盖「服务起不起来了 / 重启后服务没了 / 进程占资源怎么办」。systemd 适用于 **CentOS 7+、Ubuntu 16.04+、Debian 8+**；**老系统（CentOS 6 / Ubuntu 14.04）用 SysV 脚本（`/etc/init.d/xxx` + `chkconfig`），没有 systemctl**；macOS 完全不适用，mac 看进程用 `ps aux`。

## 一、看进程：ps / top / htop

```bash
✅ ps aux                       # 快照列表，STAT 看状态（R 运行/S 睡眠/Z 僵尸）
✅ ps aux --sort=-%mem | head   # 按内存倒序，找吃内存大户
✅ ps -ef | grep nginx           # 看某服务起了几个进程
✅ top                           # 实时；htop 更好用（apt install htop）
```

`top` 关键列：`%CPU` 占**单核**比例（4 核跑满约 400%）、`%MEM` 占物理内存比例、`RES` 常驻内存（**看内存泄漏看这列**）、`VIRT` 虚拟内存（大不代表有问题）、`STAT` 状态（`Z` 僵尸进程，父进程没回收它，得重启父进程）。交互式键：`P` 按 CPU 排、`M` 按内存排、`1` 展开每个核、`k` 杀进程、`q` 退出。

## 二、systemd 单元：放哪、怎么写

### 存放位置与优先级

同名文件**高优先级覆盖低优先级**：

| 目录 | 用途 |
|---|---|
| `/etc/systemd/system/` | **管理员自定义，优先级最高**，自己的服务放这里 |
| `/run/systemd/system/` | 运行时生成（容器常见），重启即失 |
| `/usr/lib/systemd/system/` | 包管理器安装的服务，**别改，升级会被覆盖** |

**坑点**：改 `/usr/lib/systemd/system/nginx.service` 没生效，八成是 `/etc/systemd/system/nginx.service` 同名文件覆盖了你改的；改前用 `✅ systemctl cat nginx` 看实际生效的是哪一份。

### 一个可复制的完整 unit 示例

```ini
[Unit]
Description=My Node App
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/opt/myapp
Environment="NODE_ENV=production" "PORT=3000"
ExecStart=/usr/local/bin/node /opt/myapp/bin/www.js
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=5
StandardOutput=append:/var/log/myapp.log
StandardError=append:/var/log/myapp.error.log

[Install]
WantedBy=multi-user.target
```

```bash
⚠️ cp ./myapp.service /etc/systemd/system/myapp.service
⚠️ systemctl daemon-reload
⚠️ systemctl enable --now myapp
```

**为什么这么写**：`After`/`Wants` 保证网络就绪后再起；`Type=simple` 表示 `ExecStart` 就是主进程（**必须前台运行、不能加 `&`**，否则 systemd 认为它立刻结束）；`WorkingDirectory` 定相对路径基准；`ExecReload` 用 HUP 平滑重载；`RestartSec=5` 与 `Restart=always` 搭配防贴脸重启；`$MAINPID` 是 systemd 注入的主进程号。

**坑点**：用了 `User=deploy` 就要保证 deploy 能读代码、能写日志目录——`StandardError` 的日志若属主是 root，deploy 写不进去，服务起不来且日志为空。

## 三、常用命令对照表

```bash
✅ systemctl start nginx                                  # 启动（本次生效，不动开机自启）
✅ systemctl stop nginx / systemctl restart nginx         # 停止 / 先停后启（会断连接）
✅ systemctl reload nginx                                 # 平滑重载，不断开现有连接
✅ systemctl status nginx -l / systemctl is-active nginx  # 看状态 / 只输出 active|inactive
✅ systemctl is-enabled nginx                             # 只输出 enabled|disabled
⚠️ systemctl enable nginx / systemctl disable nginx       # 设 / 取消开机自启（建删软链）
⚠️ systemctl mask nginx / systemctl unmask nginx          # 彻底禁用（连 start 也拒）/ 解除
✅ systemctl cat nginx                                    # 实际生效的 unit（含被覆盖片段）
```

`status` 关键行：`Active:` 显示 `active (running)`；`Main PID:` 主进程号；`CGroup:` 进程树。出现 `active (exited)` 是进程已退出却标为激活，通常是 `Type` 配错。
## 四、`enable` 与 `start` 的区别（头号困惑）

`enable` 只做一件事——在 `/etc/systemd/system/multi-user.target.wants/` 建一条软链指向 unit 文件，**不会启动服务**，它回答「下次开机要不要自动拉起」；`start` 才真的现在跑起来。

```bash
✅ systemctl enable nginx        # 只建软链，现在没起来
⚠️ systemctl enable --now nginx  # 开机自启 + 立即启动，一步到位
```

**坑点**：「重启后服务没了」几乎都是只 `start` 没 `enable`；反过来迁移机器拷了 unit 却忘了 `daemon-reload`，`start` 会报 not found（文案随版本不同，**以当前 `systemctl` 输出为准**）。

## 五、什么时候必须 `daemon-reload`

改完 **unit 文件本身**（新增/修改/删除 `.service`）后必须执行 `⚠️ systemctl daemon-reload`——systemd 启动时把 unit 读进内存做缓存，不重载就不知道文件变了，改了等于没改。只改 unit 里 `ExecStart` **调用的那个程序的配置**（nginx.conf、my.cnf）则不需要，那属于程序自己读配置，该做 `✅ systemctl reload nginx`。

## 六、服务起不来：三步排查

```bash
✅ systemctl status myapp -l             # -l 不截断输出
✅ journalctl -u myapp -n 50 --no-pager  # 服务自己的日志
⚠️ grep -E 'ExecStart|User|WorkingDirectory' /etc/systemd/system/myapp.service
```

① **看 `ExecStart` 里的可执行文件是否真实存在**：最常见坑是写在 `/usr/sbin/nginx` 而实际装在 `/usr/local/nginx/sbin/nginx`，或 `node` 真身在 `/usr/local/bin/node` 而 unit 写了 `/usr/bin/node`（用 `✅ command -v node` / `ls -l` 确认绝对路径）。② **看用户与权限**：`User=deploy` 就要确认 deploy 能读代码、能写日志。③ **让程序自己校验配置**：`✅ nginx -t`（Nginx），其他以官方 `man`/`--help` 为准。`status` 里出现 `Start request repeated too quickly` 并进入 `failed`，说明触发了重启风暴，见下节。

## 七、进程守护与自恢复

```ini
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5
```

**为什么**：`Restart=always` 让 systemd 在进程退出后自动拉起，`RestartSec=5` 隔 5 秒再拉避免死循环；`on-failure` 只在非零退出时重启（注意 `always` 连被 `systemctl stop` 停掉后也可能立刻拉起，反直觉）。

**重启风暴**：程序一旦启动即崩（配置写错等），`Restart=always` 会在几秒内拉起又退出，日志刷屏、CPU 打满、磁盘被日志写爆；`StartLimitIntervalSec=60` + `StartLimitBurst=5` 表示「60 秒内失败超 5 次就进 failed、不再自动拉起」，留出人工介入窗口。**坑点**：`Restart` 不帮你改错只掩盖错误，反复重启失败时**先看 `✅ journalctl -u 服务名 -n 100` 找原因**，修好再恢复 `always`。

## 八、日志轮转 logrotate

```bash
✅ ls /etc/logrotate.d/        # 系统自带规则
✅ cat /etc/logrotate.conf     # 全局默认（weekly / rotate 4 等）
```

给自己的服务轮转，在 `/etc/logrotate.d/` 建文件，内容：

```
/var/log/myapp.log /var/log/myapp.error.log {
    daily
    rotate 14
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    create 0644 deploy deploy
}
```

**为什么这么配**：`daily`+`rotate 14` 留两周；`compress` 压缩旧日志、`delaycompress` 让最近一份不压缩方便实时看；`create 0644 deploy deploy` 指定轮转后新文件的权限属主——**关键**，多数应用不会主动 reopen 日志，属主不对就报 `Permission denied`；`copytruncate`（拷贝后清空原文件）是与 `create` 二选一的替代方案，代价是可能丢最后几条。

```bash
✅ logrotate -d /etc/logrotate.d/myapp   # 演练：只打印不真做
⚠️ logrotate -f /etc/logrotate.d/myapp   # 强制执行一次
```

**坑点**：logrotate 由 `cron` 或 `systemd timer` 驱动，确认 `✅ systemctl is-active logrotate.timer`；部分发行版 unit 名不同（如 `logrotate-cleanup.service`），**以当前 `systemctl list-timers` 输出为准**。

## 九、看端口监听

```bash
✅ ss -tulnp                                             # t=tcp u=udp l=监听 n=不解析服务名 p=进程
✅ ss -tulnp | grep :80
✅ ss -ant | awk 'NR>1{print $6}' | sort | uniq -c | sort -rn   # 统计各连接状态
✅ lsof -nP -iTCP -sTCP:LISTEN                            # macOS 替代（加 -nP 避免反解拖慢）
```

macOS 没有 `ss`（`brew install iproute2` 可补），也可用 `✅ netstat -an | grep LISTEN`（老版本 macOS / 部分 Linux 适用）。

**坑点**：服务「看着在跑」但外部不通，八成没在监听，或只监听在 `127.0.0.1`（容器常见）。`ss -tulnp` 同时给出地址、端口、进程名，判断「谁占了 80 端口」最快。

## 十、杀进程的安全姿势

```bash
✅ kill <PID>       # 默认 SIGTERM：进程可优雅退出、释放资源、存盘
✅ kill -TERM <PID> # 同上，写全更好读
⚠️ kill -HUP <PID>  # 平滑重载配置，不中断服务（Nginx、SSH 认这个）
🚫 kill -9 <PID>    # SIGKILL：强制收回，进程来不及清理
✅ pgrep -f "node /opt/myapp"   # 先精确定位 PID，别直接 grep `ps aux` 的输出
⚠️ pkill -f "node /opt/myapp"   # 批量 TERM
🚫 pkill -9 -f "node"           # 最后手段：会连 sshd 的 node 一起杀，把自己踢下线
```

**为什么默认就是温和的**：SIGTERM 是「请你退出」，进程能处理好手上的事、关掉数据库连接；`kill -9` 相当于直接拔电源，可能留下脏数据、锁文件、临时文件。**坑点**：`kill -9` 后残留锁文件（僵尸进程 STAT 为 `Z`）要清 —— `🚫 rm -f /var/run/nginx.pid`、`🚫 rm -f /tmp/*.lock`，**删之前确认这些锁确实属于你要杀的那个进程**。SIGKILL 唯一该用的场景：进程彻底卡死（D 状态、杀掉都收不到 TERM）且不丢数据时。

## 下一步

- 服务起来了但端口访问不通 → @references/web-deploy.md
- 看服务日志、查磁盘内存 → @references/logs-monitoring.md
- 定时备份、开机自启与定时任务 → @references/maintenance-tasks.md
- 重启后整个机器都起不来 → @references/server-troubleshooting.md
