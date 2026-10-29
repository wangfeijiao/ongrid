# 新机器初始化清单

拿到一台能登进去的服务器后，按顺序做完这份清单，再开始部署应用。示例地址 `203.0.113.10` 是文档保留地址。
发行版差异会逐处标注：Ubuntu/Debian 用 `apt` + `ufw`，CentOS/Rocky/AlmaLinux 用 `dnf`/`yum` + `firewalld`。

## 一、建用户与 sudo

```bash
# ⚠️ 有副作用：创建用户、同名用户组和 home 目录
adduser deploy                       # Ubuntu/Debian/Rocky/AlmaLinux 都有，交互式、问密码
# 非交互式版本（脚本里用这个）
# ⚠️ 有副作用
adduser --disabled-password --gecos "deploy" deploy

# 给 sudo 权限：Ubuntu/Debian 用 sudo 组，CentOS/Rocky/AlmaLinux 用 wheel 组
# ⚠️ 有副作用
usermod -aG sudo deploy              # ⚠️ 少了 -a 会把用户从其他附加组里踢出去！
usermod -aG wheel deploy             # CentOS/Rocky/AlmaLinux
# ✅ 只读：确认用户在组里，以及自己有没有 sudo
id deploy
sudo -l -U deploy
```

**为什么要建普通用户**：一直用 root 干所有事，一旦误敲命令或软件被提权，后果是整机级别的。
**坑点**：`usermod -aG docker deploy` 少了 `-a`，会把用户从 `sudo` 等**其他所有附加组**里踢出去，下一秒就 `sudo` 不了。要一次加多个组就写全：`usermod -aG sudo,docker deploy`。

## 二、设置强密码

```bash
# ⚠️ 有副作用：改当前用户密码，会踢掉其他会话
passwd deploy
# ⚠️ 有副作用：root 也换一个长密码
passwd root
# ✅ 只读：本地生成一个随机强密码（macOS/Linux 自带）
openssl rand -base64 24
```

**为什么**：云上会扫描 22 端口试弱密码（admin/123456/Passw0rd），被撞库的机器第二天就在挖矿。**坑点**：`passwd` 改的是当前用户的密码，root 要用 `sudo passwd root` 单独设；密钥登录配好后这条基本可以不管。

## 三、时区设置（日志时间对不上的元凶）

```bash
# ✅ 只读：先看当前时区与时间
timedatectl
date
```

```bash
# ⚠️ 有副作用：设置时区（带 systemd 的系统）
sudo timedatectl set-timezone Asia/Shanghai
# 无 systemd 的极简容器，用传统软链（⚠️ 有副作用）
sudo ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && dpkg-reconfigure -f noninteractive tzdata
# ✅ 只读：确认时区和时间，date -u 应是 UTC 时间
timedatectl | grep -i "time zone"; date; date -u
```

**为什么必须设**：系统默认 UTC，日志、crontab、应用日志会差 8 小时——这是最隐蔽的坑：你看 `journalctl` 和 `nginx` 错误日志时间对不上，会误判「服务半夜重启过」。
**坑点**：改时区只影响新写入的日志；已发生的旧日志不会变。crontab 用的是系统时区，改成 UTC 后定时任务时间点会偏移。`date` 与 `date -u` 都要看一眼确认。

## 四、主机名

```bash
# ⚠️ 有副作用：改主机名（带 systemd）
sudo hostnamectl set-hostname web-01
# 无 systemd 的老系统：直接写文件（🚫 高危：覆盖系统文件，写错主机名会导致本地解析失败）
echo "web-01" | sudo tee /etc/hostname
# ✅ 只读：确认，并确认 /etc/hosts 里有 127.0.0.1 的解析行
hostname; grep web-01 /etc/hosts
```

**为什么**：一台服务器是「IP 记不住、主机名好认」，备份脚本和日志里主机名也是定位线索。
**坑点**：改完要重新登录一次 shell 才看到新名字；同时确认 `/etc/hosts` 里有一行 `127.0.0.1 web-01`，否则本地解析会失败。

## 五、软件源与系统更新

```bash
# ✅ 只读：看看有多少待升级；⚠️ 有副作用：真正执行升级（Ubuntu/Debian）
apt list --upgradable
# ⚠️ 有副作用
sudo apt update && sudo apt upgrade -y
# Rocky/AlmaLinux/CentOS Stream 用 dnf，CentOS 7 用 yum（⚠️ 有副作用）
sudo dnf update -y
```

**为什么**：第一次更新会装上几百个包（含内核），把云镜像里过时的组件补齐。
**坑点**：
- `apt upgrade` **会升级内核**，装完后必须 `reboot` 才生效，不重启会一直跑旧内核（安全补丁没打上）。
- 只想升级某个包别整台升：`sudo apt install --only-upgrade nginx`。
- `apt update` 报 `404 Not Found` 或 `NO_PUBKEY`，通常是源的发行版代号写错了（22.04 的源用在 20.04 上），要换回对应版本的源。
- 生产环境建议先自己打快照再 update。

## 六、防火墙：ufw 与 firewalld

| 目的 | ufw（Ubuntu/Debian） | firewalld（CentOS/RHEL） |
|---|---|---|
| 查状态 | `ufw status numbered` | `firewall-cmd --list-all` |
| 放行端口 | `ufw allow 22/tcp` | `firewall-cmd --permanent --add-port=22/tcp` + `--reload` |
| 指定协议/端口范围 | `ufw allow 8000:9000/tcp` | `firewall-cmd --permanent --add-port=8000-9000/tcp` |
| 只放行某 IP | `ufw allow from 203.0.113.10 to any port 22` | `firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=203.0.113.10 port protocol=tcp port=22 accept'` |
| 删除规则 | `ufw delete allow 22/tcp` | `firewall-cmd --permanent --remove-port=22/tcp` + `--reload` |
| 默认策略 | `ufw default deny incoming` | `firewall-cmd --set-default-zone=drop` |
| 开机自启 | 一般默认启用 | `systemctl enable --now firewalld` |

```bash
# ⚠️ 有副作用：放行 SSH，否则改完规则自己就被关在门外
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
# ⚠️ 有副作用：重新加载规则（不断开现有连接）
sudo ufw reload
# ✅ 只读：看规则编号，删规则要用编号或原命令
sudo ufw status numbered
```

```bash
# ⚠️ 有副作用：CentOS/Rocky/AlmaLinux 侧要先 --permanent 再 --reload
sudo firewall-cmd --permanent --add-service=ssh --add-port={80/tcp,443/tcp} && sudo firewall-cmd --reload
# ✅ 只读：确认生效规则
sudo firewall-cmd --list-all
```

**为什么必须放行 22**：`ufw` 默认 `deny incoming`，一启用防火墙就把自己的 SSH 断了，只能靠控制台 VNC 解，非常折腾。
**坑点**：**服务器里开了防火墙 ≠ 云安全组也开了**——`ufw status` 显示 22 已放行，本机还是连不上，因为云侧没放行。两层都要过（详见 `server-basics.md`）。判断顺序：`ping` 通但连不上 → 查安全组；规则有但仍不通 → 查进程是否在监听。

## 七、安装常用工具集

```bash
# ⚠️ 有副作用：安装软件包；✅ 只读：确认装上了
sudo apt update && sudo apt install -y curl wget vim git tree htop lsof net-tools unzip
command -v curl wget vim git htop lsof
# ⚠️ 有副作用：Rocky/AlmaLinux 用 dnf，CentOS 7 用 yum
sudo dnf install -y curl wget vim git tree htop lsof net-tools unzip
```

**为什么**：这八个是排障的基础件——`curl` 测服务、`htop` 看负载、`lsof` 查端口占用、`ss`/`net-tools` 看监听。
**平台差异**：macOS 上 `free`/`ss`/`systemctl`/`journalctl` 不存在，用 `top` + `lsof -i` 替代；装包前若不确定命令是否存在，先 `command -v xxx` 探测。

## 八、关闭不必要的服务

```bash
# ✅ 只读：看哪些服务是开机自启的、有没有已失败的
systemctl list-unit-files --state=enabled --no-pager | head -30
systemctl --failed --no-pager
# ⚠️ 有副作用：关掉用不上的服务（按需，别照抄）
sudo systemctl disable --now bluetooth.service cups.service
# ✅ 只读：看云厂商的安全代理在不在——这类**不要**关
systemctl status aegis --no-pager 2>/dev/null || systemctl status cloudguard.service --no-pager
```

**为什么**：少一个自启服务就少一个攻击面和一份启动开销。
**坑点**：云厂商的主机安全代理（如阿里云 aegis、腾讯云相关服务）**不要动**，关掉会失去入侵告警甚至导致控制台异常；拿不准的服务先 `systemctl status` 看描述，别凭名字猜。

## 九、reboot 后验证清单

```bash
# ⚠️ 有副作用：重启！重启前务必确认 SSH 免密登录能用，否则重启后就进不去了
sudo reboot
```

```bash
# ✅ 只读：重启后逐条核对（每行后面的中文是这条看什么）
uptime; df -h; free -h          # 负载 / 磁盘 / 内存可用量
ss -tulnp | head; systemctl --failed --no-pager   # 端口监听 / 起不来的服务
timedatectl | grep -i "time zone"; hostname       # 时区 / 主机名是否被改回去
whoami && sudo -n true && echo "sudo ok"          # 用户与 sudo 权限还在不在
uname -r                                          # 内核版本，确认已换成新内核
```

**为什么按这个顺序**：重启是初始化里唯一不可逆的一步，先确认免密登录可用再重启，能避免「重启完进不去」。
**坑点**：内核被 `apt upgrade` 升级过却没重启，上面所有检查都显示旧内核行为；用 `uname -r` 对比重启前后的版本号。

## 下一步

- 连不上、要配免密登录或排查 SSH 失败 → @references/ssh-login.md
- 还没决定买什么配置、选哪个系统 → @references/server-basics.md
- 要装 Node/Python/Java/Docker 环境 → @references/runtime-env.md
- 要部署网站和 HTTPS 证书 → @references/web-deploy.md
