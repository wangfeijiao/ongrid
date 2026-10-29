# 服务器安全加固与入侵排查

目标是「把暴力破解挡在门外 + 真被打了能发现 + 发现后能处理」。所有改配置的操作先备份。

## 0. 改 SSH 配置前的一条铁律

**开一个不关的 SSH 窗口，然后才动手改端口 / 关密码登录 / 禁 root。** 否则配置一写错就被关在门外，只能去云控制台用 serial console 或重置密码。

顺序永远是：备份 → 改一处 → `sshd -t` 校验 → `reload` → **另开窗口验证能登进去** → 再关掉原来那个窗口。

## 1. SSH 加固完整流程

```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak      # ⚠️ 备份，别省
sudo cp /etc/ssh/sshd_config.d/*.conf /root/ 2>/dev/null   # ⚠️ Ubuntu 22.04 起还有.d 片段
sudo vim /etc/ssh/sshd_config
```

改这几项：

```
Port 2202                   # 换非 22 端口，能挡掉 90% 的扫描
PasswordAuthentication no   # 关密码，只留密钥
PubkeyAuthentication yes    # 显式打开密钥登录
PermitRootLogin no          # 禁止 root 直登
PermitEmptyPasswords no
MaxAuthTries 3
AllowUsers deploy           # 只允许指定用户，比 DenyUsers 更安全
```

校验与生效：

```bash
sudo sshd -t                # ✅ 只读，语法校验，报错指出文件行号
sudo systemctl reload ssh   # ⚠️ Ubuntu/Debian 22.10+ 是 ssh.socket；CentOS 用 sshd
sudo systemctl reload ssh.socket   # ⚠️ 报 "Job for ssh.service failed" 时的新版写法
```

为什么必须 `sshd -t`：配置写错（如 `PermitRootLogin` 拼错）reload 后 sshd 直接拒绝启动，未校验等于自断退路；改端口用 `reload` 而非 `restart`，是为了不断开现有连接、留着那个救命窗口。

坑点：Ubuntu 24.04 起 ssh 走 socket 激活，`systemctl reload ssh` 会报失败，要 reload `ssh.socket`。`AllowUsers` 写漏自己等于踢出自己，先用 `getent passwd 用户名` 确认账号存在。关密码前**先验证密钥能用**：`ssh -i ~/.ssh/id_ed25519 -p 2202 deploy@服务器IP` 能登再关。SELinux 下换端口还要 `semanage port -a -t ssh_port_t -p tcp 2202`。

## 2. 禁用 root 登录后如何保留 sudo

```bash
adduser deploy                                          # ⚠️ 有副作用
usermod -aG sudo deploy                                 # ⚠️ Ubuntu/Debian
usermod -aG wheel deploy                                # ⚠️ CentOS/RHEL 系（wheel 组）
sudo visudo                                             # ⚠️ 推荐用 visudo，自带语法校验
```

`visudo` 里加一行（或把已有的 `%sudo`/`%wheel` ALL 行放开注释）：

```
deploy ALL=(ALL:ALL) ALL
```

为什么这么配：`PermitRootLogin no` 只是不让 root 名字直登，密钥仍可 `ssh root@x`；真正防的是「先登普通用户再 `su`」。坑点：`visudo` 输错语法会连 sudo 都用不了，用 `sudo visudo -c` 校验。

## 3. fail2ban：自动封暴力破解

```bash
sudo apt install fail2ban                                 # ⚠️ Ubuntu/Debian（CentOS: dnf install fail2ban）
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local  # ⚠️ 永远改 .local，别直接改 .conf
sudo vim /etc/fail2ban/jail.local
```

配置示例：

```ini
[DEFAULT]
bantime  = 3600          # 封禁时长，秒
findtime = 600           # 统计窗口
maxretry = 5             # 窗口内失败 5 次就封
backend  = systemd       # ✅ 有 journald 时用它，避免读日志权限问题
ignoreip = 127.0.0.1 ::1

[sshd]
enabled  = true
port     = 2202
logpath  = /var/log/auth.log      # CentOS 改成 /var/log/secure
maxretry = 3
```

启动与常用操作：

```bash
sudo systemctl enable --now fail2ban    # ⚠️ 有副作用
sudo fail2ban-client reload             # ⚠️ 使新配置生效
sudo fail2ban-client status             # ✅ 只读，全局状态
sudo fail2ban-client status sshd        # ✅ 只读，看封了哪些 IP
sudo fail2ban-client set sshd unbanip 1.2.3.4   # ⚠️ 解封某个 IP
sudo fail2ban-client set sshd banip 1.2.3.4     # ⚠️ 手动封禁
```

为什么要用 `jail.local`：升级 fail2ban 时 `.conf` 会被包管理器覆盖，`.local` 不会。坑点：`backend = systemd` 与 `logpath` 同时写会冲突报错；`[sshd]` 里的 `port` 要和实际 SSH 端口一致，否则「日志在记但 ban 不掉」，看 `fail2ban-client status sshd` 显示的端口确认。

## 4. NTP 时间同步

时间不准的后果很具体：**证书签发失败**（time skew 超过容差直接 reject）、**日志时间错乱**（排查时无法按时间线串起来）、某些分布式锁和 token 直接失效。

```bash
timedatectl status                                    # ✅ 只读，看 NTP enabled / RTC 同步
sudo apt install chrony && sudo systemctl enable --now chronyd   # ⚠️ Ubuntu/Debian
sudo dnf install chrony && sudo systemctl enable --now chronyd   # ⚠️ CentOS/RHEL，服务名 chronyd
chronyc sources                                       # ✅ 只读，看上游时间源
date -u                                               # ✅ 只读，核对 UTC 时间
```

坑点：容器里 `timedatectl` 常显示 NTP disabled（宿主负责授时），这属正常，不必强改。国内服务器建议把 `/etc/chrony/chrony.conf` 里的 pool 换成阿里/腾讯 NTP 地址，改完 `sudo systemctl restart chronyd`。

## 5. sudo 安全：给白名单，不给 NOPASSWD: ALL

```bash
sudo visudo -f /etc/sudoers.d/deploy                  # ⚠️ 有副作用
```

只在 `/etc/sudoers.d/deploy` 里给具体命令白名单：

```
deploy ALL=(ALL) /usr/bin/systemctl restart nginx, /usr/bin/journalctl
deploy ALL=(root) /usr/bin/rsync
```

为什么别写 `deploy ALL=(ALL) NOPASSWD: ALL`：免密 root 等于把整台机器交给用户，`sudo -i` 之后审计全丢。给 `systemctl` 这种宽泛命令时，用户可用 `systemctl edit` 改 unit 拿到 root，所以只给具体子命令。审计看 `/var/log/auth.log`（CentOS 是 `/var/log/secure`）。

## 6. 入侵迹象排查

```bash
lastb                                                   # ✅ 失败登录尝试（暴力破解第一现场）
last -20                                                # ✅ 最近实际登录记录
grep "Failed password" /var/log/auth.log | tail -20     # ✅ Ubuntu；CentOS 换成 /var/log/secure
ps aux | grep -v grep | grep -E 'python|curl|/tmp/'     # ✅ 查异常进程
crontab -l; ls -la /etc/cron.d/ /var/spool/cron/crontabs/  # ✅ 查被植入的计划任务
cat ~/.ssh/authorized_keys                              # ✅ 有没有陌生公钥
find / -xdev -newermt "3 days ago" -type f 2>/dev/null | head -50  # ✅ 近期被改动的文件
```

为什么查 cron：入侵者最常用来留后门的方式就是写个定时任务每分钟反弹 shell。为什么 `find` 要加 `-xdev`：跳过 `/proc`、`/sys` 这类虚拟文件系统，出来的才是真实改动过的文件；`-newermt "3 days ago"` 是发现「刚改的密码当晚就被翻过」的关键线索。

补充：`ss -tulnp | grep ESTAB` 看有没有陌生出站连接、`journalctl -u ssh --since "1 day ago"` 看 SSH 全貌。发现异常**别急着删**（证据没了就查不清来源），先截图或打包 `/var/log`。

## 7. 密钥泄露应急处理

```bash
# 1 本地生成新密钥对
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_new          # ⚠️ 会覆盖同名文件的话先确认
ssh-copy-id -i ~/.ssh/id_ed25519_new.pub deploy@服务器公网IP   # ⚠️ 追加，旧的先留着
# 2 另开窗口验证新密钥能用，再回到旧窗口删掉泄露的那一行公钥
ssh -i ~/.ssh/id_ed25519_new deploy@服务器公网IP        # ✅ 验证
vim /root/.ssh/authorized_keys                          # ⚠️ 删掉泄露的那行（连同运维脚本一起换）
```

为什么顺序不能反：先把新公钥传上去再删旧的，中间任何一步失误还有旧钥匙兜底。轮换后必须把 **cron / CI / 备份脚本里的 private key 一起换掉**，否则改完半夜定时任务集体失败；换完再看 fail2ban 里旧 IP 是否还在被尝试。

## 8. 十分钟基础体检清单

```bash
whoami && id && lastb | head                                        # ✅ 1 身份与失败登录
sudo -l; grep -E "NOPASSWD" /etc/sudoers /etc/sudoers.d/*           # ✅ 2 提权面
ss -tulnp                                                           # ✅ 3 端口暴露面
awk -F: '$3==0 || ($3>=1000 && $3<65534)' /etc/passwd               # ✅ 4 可登录用户
crontab -l -u root; ls -la /etc/cron.d/ /etc/cron.hourly/            # ✅ 5 定时任务
find / -xdev -newermt "2 days ago" -type f 2>/dev/null | grep -vE '^/(proc|sys|dev|run)' | head -30  # ✅ 6 入侵痕迹
systemctl status fail2ban chronyd --no-pager                        # ✅ 7 加固组件在不在
grep -E "PasswordAuthentication|PermitRootLogin|Port" /etc/ssh/sshd_config  # ✅
apt list --upgradable | head      # Ubuntu；CentOS 用 dnf check-update        # ⚠️ 联网查询
```

## 说明

- 以上是通用加固基线，用于个人站、小团队服务器。
- 涉及 **CTF 比赛、等保测评、金融/政企合规**场景，系统的控制项、测评项与日志留存要求以**官方文件与测评结论为准**，本文不能替代合规要求。

## 下一步

→ @references/server-init.md
→ @references/web-deploy.md
→ @references/service-management.md
