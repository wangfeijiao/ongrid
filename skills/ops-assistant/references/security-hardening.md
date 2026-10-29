# 安全加固与入侵排查

> 安全分两层：**平时加固**（缩小攻击面）和**出事排查**（找到入侵痕迹）。

## SSH 加固（第一道门，也是扫描器攻击最多的）

```bash
# /etc/ssh/sshd_config 基线
PermitRootLogin no                # 禁 root 直登
PasswordAuthentication no         # 只允许密钥（先确认密钥能登录再改！）
Port 2222                         # 改默认端口（挡 90% 的自动化扫描）
AllowUsers ops deploy             # 白名单用户
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2             # 15 分钟无活动断连

# 改完必做（⚠️ 配置错误会把自己锁外面）
sshd -t                           # 语法检查
systemctl reload sshd
# 🚫 验证新配置能登录之前，保留当前会话不断开！
```

```bash
# 密钥登录设置
ssh-keygen -t ed25519 -C "ops@laptop"     # 本地生成（比 rsa 更短更安全）
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@server
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys   # 权限不对 sshd 拒绝
```

## 防火墙

```bash
# firewalld（CentOS/RHEL）
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=10.0.0.0/8 port port=22 protocol=tcp accept'
firewall-cmd --reload
firewall-cmd --list-all

# ufw（Ubuntu，更简单）
ufw default deny incoming
ufw default allow outgoing
ufw allow from 10.0.0.0/8 to any port 22
ufw allow 80,443/tcp
ufw enable                        # ⚠️ 先放行 SSH 再 enable！

# 云服务器别忘安全组——防火墙和安组是两层，都要放
```

**暴露面审计**（定期跑）：

```bash
# ✅ 看本机开了哪些端口，对照"应该开的清单"
ss -tlnp
# 发现不认识的就查：这是什么进程？该对外吗？→ 绑 127.0.0.1 或加防火墙
```

## 入侵排查（发现异常后的标准流程）

### 第一步：别急着重启/清进程——先取证

```bash
# ✅ 全部只读，把现场保存下来
ps auxf > /tmp/ps-snapshot.txt           # 进程树（找可疑父子关系）
ss -antp > /tmp/net-snapshot.txt         # 网络连接（找外连 C2）
last -20                                  # 最近登录
history > /tmp/history-snapshot.txt
```

### 第二步：找可疑痕迹

```bash
# ① 异常进程（✅）
ps aux --sort=-%cpu | head                 # 挖矿特征：CPU 100% + 陌生进程名
ps auxf | grep -v "^\S*\s*\S* \["         # 看树状关系，异常父进程
ls -l /proc/<pid>/exe                      # 进程真实路径（名字可伪装，路径难伪装）

# ② 外联连接（挖矿/木马的命门）
ss -antp | grep ESTAB | grep -v "127.0.0.1"
# 可疑特征：连陌生 IP 的高位端口、连接国外矿池地址（3333/5555/7777 端口常见）

# ③ 定时任务（最常见的持久化手段）
crontab -l
ls -la /etc/cron.d/ /var/spool/cron/
cat /etc/crontab

# ④ 启动项
systemctl list-unit-files --state=enabled | grep -v -E "ssh|cron|network|systemd"
ls -la /etc/rc.local ~/.bashrc ~/.bash_profile

# ⑤ 最近被修改的系统文件
find /etc /usr/bin /usr/sbin -mtime -3 -ls 2>/dev/null    # 3 天内被改的
rpm -Va 2>/dev/null | head     # 对比安装包校验和（CentOS）
debsums -c 2>/dev/null | head  # Debian 系

# ⑥ SSH 登录痕迹
grep "Accepted" /var/log/secure | tail -20       # CentOS
grep "Accepted" /var/log/auth.log | tail -20     # Ubuntu
lastb | head                                      # 失败的登录尝试
```

### 第三步：处置

```
1. 断网不断电（iptables 切断外联，保留内存取证）
2. 确认入侵途径（弱密码？Web 漏洞？泄露的密钥？）——不找到根源，重装也会被再次入侵
3. 业务允许的话：重装系统是最干净的（"once compromised, never trusted"）
4. 轮换所有该机器接触过的凭证（SSH 密钥、API Token、数据库密码）
```

## 防挖矿快速处置（云上最高发的入侵）

```bash
# 特征确认
top                                    # 陌生进程 CPU 近 100%
ls -l /proc/<pid>/exe                  # 常在 /tmp、/dev/shm、隐藏目录

# 处置顺序（⚠️ 有副作用，逐条确认）
kill -STOP <pid>                       # 先冻结（不是 kill！kill 可能触发 watchdog 重启它）
# 找它的启动来源：crontab/启动项/父进程——不除根杀了也白杀
crontab -l -u <user>
ls -l /proc/<pid>/cwd
# 除根后
kill -9 <pid>
rm -f <恶意文件路径>                     # 🚫 确认路径无误再删
# 最后：补漏洞（多半是 Redis 未授权/Hadoop 未授权/SSH 弱密码/Web 漏洞）
```

## 权限与审计基线

```bash
# 最小权限：应用不用 root 跑
useradd -r -s /sbin/nologin appuser     # 系统用户 + 禁登录
# systemd 服务里：User=appuser

# sudo 精细授权（而不是一把梭 ALL）
# /etc/sudoers.d/deploy
deploy ALL=(root) NOPASSWD: /bin/systemctl restart myapp, /bin/systemctl status myapp

# 关键目录权限
chmod 600 /etc/ssh/sshd_config
chmod 700 /root
# 找全局可写的危险文件
find / -perm -0002 -type f ! -path "/proc/*" 2>/dev/null | head
```

## 漏洞应急流程

```
出现新漏洞（如 Log4Shell 级别）→
1. 影响面盘点：哪些服务用了受影响组件？（资产清单此时无价）
2. 临时缓解：WAF 规则/网络隔离/功能开关
3. 升级修复：按暴露面排序（先修公网可达的）
4. 验证：扫描器复扫 + 检查有无被利用痕迹（日志回溯）
```

**日常动作**：
- `unattended-upgrades`（Ubuntu）/ `yum-cron`（CentOS）自动装安全补丁——内核除外（内核升级要重启，走变更流程）
- 订阅发行版安全公告；`yum updateinfo list security` / `apt list --upgradable 2>/dev/null | grep -i secur`
