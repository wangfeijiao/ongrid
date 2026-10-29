# 报错速查：按报错原文索引的排障手册

适用发行版：Ubuntu / Debian / CentOS / RHEL / Rocky / AlmaLinux。命令标 ✅ 只读 / ⚠️ 有副作用 / 🚫 高危，排查一律先只读。macOS 没有 `systemctl`/`journalctl`/`free`/`ss`。用法：按「症状 → 篇目」找到报错原文 → 按「根因 → 验证 → 解决 → 避免」四步走，90% 的答案就在原文里。

| 症状 | 去哪一篇 |
|---|---|
| 连不上服务器、SSH 超时/拒绝/登不进去 | [SSH 连接类](#ssh-连接类) |
| 网页打不开、curl 报错、域名解析不了 | [网络类](#网络类) |
| 文件操作被拒、sudo 用不了 | [权限类](#权限类) |
| 磁盘满、写不进去、分区异常 | [磁盘类](#磁盘类) |
| 服务起不来、网站 502 / 504 | [服务类](#服务类) |
| 证书无效、证书过期 | [证书类](#证书类) |

## SSH 连接类

### `ssh: connect to host 47.x.x.x port 22: Connection timed out`

根因：云安全组/防火墙没放行 22；服务器没开机；IP 或端口写错。避免：新机器第一次登录前就把安全组配好。

```bash
ping -c 3 47.x.x.x             # ✅ 通 = 机器活着，问题在端口/安全组
curl -v telnet://47.x.x.x:22   # ✅ 卡住 = 包被丢，立刻拒绝 = 端口没开
```

### `ssh: connect to host 47.x.x.x port 22: Connection refused`

根因：sshd 没运行；SSH 端口改过但连接时没带 `-p`。避免：改端口后连接一律写清 `-p`。

```bash
systemctl status ssh && ss -tulnp | grep ssh    # ✅ Ubuntu/Debian 是 ssh，CentOS 是 sshd
systemctl restart ssh                           # ⚠️ 确认端口后重启
```

### `root@47.x.x.x: Permission denied (publickey)` + `socket: Permission denied`

根因：公钥没写进 `authorized_keys`、`.ssh` 权限不对、sshd 关了公钥登录；私钥权限不是 600 时报 `socket: Permission denied`。

实测校正（本机 OpenSSH 9.4p1 复现）：括号里的列表是**服务端放行的认证方式**（详见 `ssh-login.md`第三节）。另外私钥 644 与 600 **报错一字不差**，都不会报 `too open`，所以老话"改权限就好了"在 `ssh -i` 路径下不成立，别在这上面浪费时间。

```bash
ssh-copy-id root@47.x.x.x                      # ⚠️ 本机执行，把公钥推上去
chmod 600 ~/.ssh/id_rsa && chmod 700 ~/.ssh     # ⚠️ 私钥必须 600
```

避免：配完免密先在另一个窗口验证，别关掉当前连接再改配置。

### `Host key verification failed` / `REMOTE HOST IDENTIFICATION HAS CHANGED`

根因：服务器重装、端口改过，`known_hosts` 里是旧指纹；后者还可能是中间人。

```bash
ssh-keygen -R 47.x.x.x      # ⚠️ 删旧指纹，确认身份后才执行
ssh-keyscan -t rsa 47.x.x.x # ✅ 取新指纹并与提示中的值人工核对
```

## 网络类

### `curl: (6) Could not resolve host: www.example.com`

根因：域名拼错；`resolv.conf` 里的 DNS 不对；容器内没配 DNS。

```bash
cat /etc/resolv.conf        # ✅ Linux；macOS 用 scutil --dns
ping -c 1 www.baidu.com     # ✅ 通 = DNS 正常，问题在域名本身
```

`No route to host` 多为默认路由没配（`ip route` 看有没有 `default via`），`ping: cannot resolve xxx: Unknown name` 则是 DNS 解析不了，跑 `dig +short www.baidu.com` 确认。

### `curl: (7) Failed to connect to host port 80: Connection refused`

根因：服务没起来；服务其实监听在别的端口；防火墙拦了。

```bash
ss -tulnp | head            # ✅ 服务到底监听在哪个端口（macOS 用 lsof -i | head）
curl -I http://127.0.0.1:8080   # ✅ 本地能通 = 服务正常，问题在外部访问链路
```

## 权限类

### `Permission denied`（普通文件/目录操作）

根因：目录缺 `x` 权限或属主不对；父目录无权；SELinux 拦截（CentOS/RHEL）。

```bash
ls -ld /var/www/html /var/www                  # ✅ 看 owner 与权限位
chown -R www-data:www-data /var/www/html       # ⚠️ Ubuntu/Debian 的 Nginx 运行用户
```

SELinux 场景用 `restorecon -Rv /var/www/html`，排查可 `sudo setenforce 0` 临时验证后改回 `setenforce 1`。

### `deploy is not in the sudoers file. This incident will be reported.`

根因：用户没加入 sudo 组；发行版组名不同（Ubuntu 是 `sudo`，CentOS 是 `wheel`）。

```bash
usermod -aG sudo deploy      # ⚠️ Ubuntu/Debian 系
usermod -aG wheel deploy     # ⚠️ CentOS / RHEL / Rocky / AlmaLinux
```

### `mkdir: cannot create directory ‘/xxx’: Read-only file system` / `Operation not permitted`

根因：文件系统被强制挂成只读（云盘异常、内核报错后自动 remount ro）；没有写权限。

```bash
mount | grep " on / "            # ✅ 看有没有 ro 标记
sudo mount -o remount,rw /       # ⚠️ 有副作用，需 root；数据盘异常要先卸载再 fsck
```

云盘报 hardware fault 只能换盘并从快照恢复。

## 磁盘类

### `No space left on device`

根因：磁盘写满；inode 用尽；日志没轮转。`df: /dev/mapper/x: unexpected unprivileged: No such file or directory` 属同类——普通用户读不到挂载点，`sudo df -h` 即可。

```bash
df -h && df -i                                     # ✅ 空间 + inode 一起看
du -sh /var/log/* 2>/dev/null | sort -h | tail -5   # ✅ 谁在吃磁盘
```

补一句 `Input/output error`：云盘/磁盘异常、坏块或 NFS 断连的典型表现，`dmesg -T | grep -iE "error|fault" | tail -20` 定位；确认真坏道再 `badblocks -sv /dev/sdb`（🚫 会写盘，必须先卸载）。

## 服务类

### `Job for nginx.service failed. See "journalctl -xe" for details.`

根因：配置语法错；80/443 端口被占用；网站目录权限不对。

```bash
nginx -t                                       # ✅ 直接告诉你哪一行错了
ss -tulnp | grep 80                            # ✅ 端口是否被别的进程占用
systemctl reload nginx                         # ⚠️ 校验通过再平滑重载，别一上来就 restart
```

配套：`Unit nginx.service not found` 说明软件没装或发行版服务名不同（CentOS 的 Apache 叫 `httpd`），先 `apt-get install -y nginx`（Ubuntu/Debian）/`yum install -y nginx`（CentOS/RHEL），再用 `systemctl list-unit-files | grep -i nginx` 看真实服务名。

### 浏览器报 `502 Bad Gateway` / `504 Gateway Timeout`

根因：502 = 上游没监听或应用挂了；504 = 上游响应超时（应用慢、数据库卡、`proxy_read_timeout` 太短）。

```bash
curl -I http://127.0.0.1:3000                    # ✅ 后端本身能不能应答
tail -50 /var/log/nginx/error.log                # ✅ 找 upstream 相关行
systemctl restart app                            # ⚠️ 502 先重启上游应用
```

504 再调 `proxy_read_timeout 60s;` 并排查后端慢查询。

## 证书类

### `curl: (60) SSL certificate problem: self signed certificate`

根因：访问的是自签证书的内网服务，curl 默认不信任自签 CA。

```bash
curl -k -I https://127.0.0.1     # ✅ 能通就说明是证书信任问题
openssl s_client -connect 127.0.0.1:443 -showcerts | openssl x509 -noout -subject   # ✅
```

把自签 CA 拷到 `/usr/local/share/ca-certificates/` 后 `update-ca-certificates`（Ubuntu/Debian）；仅测试时临时用 `curl -k`。

### `net::ERR_CERT_HAS_EXPIRED` / `certificate has expired`

根因：Let's Encrypt 到期未续；certbot 的定时器/cron 没生效。

```bash
certbot certificates                                  # ✅ 看到期日
systemctl list-timers | grep -E "certbot|letsencrypt"   # ✅ 续期定时器在不在
certbot renew --dry-run && certbot renew              # ⚠️ 先试跑再实跑
```

`ssl_certificate` 必须指向 `fullchain.pem`、`ssl_certificate_key` 指向 `privkey.pem`，续期后 `systemctl reload nginx`。

## 提问题时怎么问才最快得到答案

1. **贴报错原文**，不要翻译——「Permission denied (publickey)」和「登不进去」差着一万公里。
2. **说发行版**：先跑 `cat /etc/os-release` 贴输出，Ubuntu 与 CentOS 的服务名、`sudo` 组名、包管理器都不一样。
3. **说时间点**：从什么时候开始（几点、改了什么、重启过没有），「以前好好的」没有信息量。
4. **说最近改动与已试过的操作**。云平台控制台菜单位置以当前官方文档为准。

## 服务器体检自查清单（10 条，每周巡检）

```bash
uptime                                       # ✅ 负载是否超过 CPU 核数
df -h && df -i                               # ✅ 磁盘空间 + inode
free -h                                      # ✅ 内存（看 available 不是 free）
ss -tulnp | head -20                         # ✅ 端口监听（macOS 用 lsof -i | head）
systemctl list-units --type=service --state=running | head -20   # ✅ 关键服务在不在
journalctl -u nginx --since "1 hour ago" | grep -i error | tail -10  # ✅ 关键服务有无报错
du -sh /var/log/* 2>/dev/null | sort -h | tail -5   # ✅ 谁在吃磁盘
last -20                                     # ✅ 谁登录过，有无异常 IP
```

macOS 本机替代：`top -o mem` 看内存、`lsof -i` 看端口、`diskutil list` 看磁盘、日志在 `/var/log/system.log`。

## 下一步

- 日志与监控的更多玩法 → @references/logs-monitoring.md
- 服务起不来的根因在服务管理里 → @references/service-management.md
- 502/504 与 Nginx 站点配置 → @references/web-deploy.md
- 连不上服务器的根治办法（密钥、安全组）→ @references/ssh-login.md
- 误删、磁盘满之后怎么办 → @references/backup-restore.md
