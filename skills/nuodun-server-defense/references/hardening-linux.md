# Linux / UNIX 安全加固手册

> 读这份文档的时机：报告出现 `SSH_*` / `FW_INACTIVE` / `SUDO_NOPASSWD` /
> `EMPTY_PASSWORD_ACCOUNT` / `MULTI_UID0` / `SSH_BRUTEFORCE` / `PORT_EXPOSED_*` /
> `SELINUX_PERMISSIVE` / `PKG_UPDATES_PENDING` / `REBOOT_REQUIRED` 等条目时。

## 加固前必读

**改 SSH 之前，先开第二个会话保底。** 这是唯一一条能让你把自己彻底锁在门外的操作。

```bash
# 1) 先开一个 SSH 会话保持连接
# 2) 在另一个会话里改配置
# 3) 改完执行语法检查，再 reload（不是 restart）
sshd -t && systemctl reload sshd
# 4) 全新开一个会话验证能登录
# 5) 确认无误后再关掉保底会话
```

**reload 而非 restart**：reload 不会断开已有连接，restart 会。写错了至少还有退路。

云主机额外保底：确认控制台 / VNC 可用。

---

## SSH

### 检查

```bash
# 主配置 + drop-in 目录（后者覆盖前者，本地判定按"最后出现者优先"）
grep -vE '^\s*#|^\s*$' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null

# 最可靠的是看生效值（sshd -T）
sshd -T 2>/dev/null | grep -iE 'permitrootlogin|passwordauthentication|permitemptypasswords|x11forwarding|maxauthtries|port'
```

### 加固

```bash
cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%s)

# 关键项（sed 改已存在的行，没有则追加）
sed -i -E 's|^[#[:space:]]*PermitRootLogin.*|PermitRootLogin no|'          /etc/ssh/sshd_config
sed -i -E 's|^[#[:space:]]*PermitEmptyPasswords.*|PermitEmptyPasswords no|' /etc/ssh/sshd_config
sed -i -E 's|^[#[:space:]]*X11Forwarding.*|X11Forwarding no|'              /etc/ssh/sshd_config
sed -i -E 's|^[#[:space:]]*MaxAuthTries.*|MaxAuthTries 4|'                 /etc/ssh/sshd_config
sed -i -E 's|^[#[:space:]]*ClientAliveInterval.*|ClientAliveInterval 300|' /etc/ssh/sshd_config
sed -i -E 's|^[#[:space:]]*ClientAliveCountMax.*|ClientAliveCountMax 2|'   /etc/ssh/sshd_config

# 确认公钥可用后，再关密码认证（顺序不能反！）
grep -q '^PasswordAuthentication' /etc/ssh/sshd_config \
  || echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config
sed -i -E 's|^[#[:space:]]*PasswordAuthentication.*|PasswordAuthentication no|' /etc/ssh/sshd_config

sshd -t && systemctl reload sshd
```

### 爆破防御

```bash
# 看被打了多少次
grep -c 'Failed password' /var/log/auth.log /var/log/secure 2>/dev/null
journalctl -u ssh --since '24 hours ago' | grep -c 'Failed password'

# fail2ban：自动封禁
apt install -y fail2ban        # Debian/Ubuntu
yum install -y fail2ban        # RHEL 需 EPEL

cat > /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
port    = ssh
backend = systemd
maxretry = 5
findtime = 600
bantime  = 3600
EOF
systemctl enable --now fail2ban
fail2ban-client status sshd
```

**更彻底的做法**：SSH 不暴露公网，只允许内网 / VPN / 堡垒机访问；
配合来源 IP 白名单（`sshd_config` 的 `AllowUsers user@10.0.0.*` 或防火墙）。

---

## 账户与提权

```bash
# 多个 UID=0 就是后门
awk -F: '$3==0 {print $1}' /etc/passwd

# 空口令
awk -F: '$2=="" {print $1}' /etc/shadow

# 可登录的账户（有 shell 的）
awk -F: '$7 !~ /(nologin|false)$/ {print $1, $3, $7}' /etc/passwd

# 免密提权（危险）
grep -rE 'NOPASSWD' /etc/sudoers /etc/sudoers.d/ 2>/dev/null

# 登录审计
last -n 30
lastb -n 30        # 失败登录
who
```

### 加固

```bash
# 锁定空口令账户
passwd -l <user>; passwd -S <user>       # 状态应为 L

# 删除后门账户（先确认不是业务账户！）
userdel -r <user>

# 收紧 sudo：删除 NOPASSWD ALL，改为具体命令
visudo -f /etc/sudoers.d/90-ops
# opsuser ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx
```

### 文件权限基线

```bash
chmod 000 /etc/shadow /etc/gshadow
chmod 644 /etc/passwd /etc/group
chmod 600 /etc/ssh/sshd_config
chmod 700 /root
chmod 750 /home/*
chmod 600 /root/.ssh/authorized_keys
chown root:root /etc/shadow /etc/passwd
```

---

## 防火墙

```bash
# 先看现在是什么状态
ufw status verbose 2>/dev/null
firewall-cmd --state 2>/dev/null
iptables -S 2>/dev/null | head -30
nft list ruleset 2>/dev/null | head -30
```

### ufw（Debian / Ubuntu）

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp             # ⚠ 改成你实际的 SSH 端口
ufw allow 80,443/tcp
ufw --force enable
ufw status numbered
```

### firewalld（RHEL / CentOS / Rocky）

```bash
systemctl enable --now firewalld
firewall-cmd --set-default-zone=public
firewall-cmd --permanent --add-service=ssh
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
firewall-cmd --list-all
```

### iptables 持久化

```bash
# 规则写好后必须持久化，否则重启就没了
apt install -y iptables-persistent && netfilter-persistent save
# 或
iptables-save > /etc/iptables/rules.v4
```

**云主机提醒**：主机防火墙只是第二层，**安全组才是第一层**。
先在安全组收口，再用主机防火墙做兜底。

---

## 数据库与缓存的暴露面

这是云主机被打得最多的点，比 SSH 爆破还致命（因为往往无认证）。

```bash
ss -tulnp | grep -E '3306|5432|6379|27017|9200|11211'
```

| 服务 | 危险配置 | 加固 |
|---|---|---|
| Redis | 无 `requirepass` + 绑 0.0.0.0 | `bind 127.0.0.1` + `requirepass <强口令>` + `rename-command CONFIG ""` |
| MongoDB | 默认无认证 | 建管理员账号 + `security.authorization: enabled` + bind 内网 |
| MySQL | `bind-address = 0.0.0.0` + 弱口令 | `bind-address = 127.0.0.1`；删除匿名用户与远程 root |
| PostgreSQL | `listen_addresses = '*'` + `trust` 认证 | 改 `scram-sha-256`；收紧 `pg_hba.conf` |
| Elasticsearch | 无认证暴露 9200 | 开启 X-Pack 安全 或 绑定内网 |
| Memcached | 无认证暴露 11211 | 绑定内网 + SASL |

```bash
# Redis 加固示例
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
sed -i 's/^# requirepass .*/requirepass <强口令>/' /etc/redis/redis.conf
# 禁止危险命令（防被写入 SSH key / 计划任务）
cat >> /etc/redis/redis.conf <<'EOF'
rename-command CONFIG ""
rename-command FLUSHALL ""
rename-command FLUSHDB ""
EOF
systemctl restart redis
```

```bash
# MySQL 清理匿名用户与远程 root
mysql -u root -p -e "
  DELETE FROM mysql.user WHERE User='';
  DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost','127.0.0.1','::1');
  DROP DATABASE IF EXISTS test;
  FLUSH PRIVILEGES;
  SELECT user,host FROM mysql.user;"
```

---

## 强制访问控制（MAC）

```bash
getenforce                       # RHEL 系
sestatus
aa-status                        # Debian 系
```

**不要因为业务报错就直接关掉 SELinux**。正确做法是看拒绝日志，加策略：

```bash
# 看被拒绝的操作
ausearch -m avc -ts recent
grep AVC /var/log/audit/audit.log | tail -20

# 生成并安装策略模块
ausearch -m avc -ts recent | audit2allow -M mypolicy
semodule -i mypolicy.pp
```

确认业务兼容后再切回 enforcing：`setenforce 1`，并写入 `/etc/selinux/config`。

---

## 补丁

```bash
# 只看安全更新
yum updateinfo list security        # RHEL7
dnf updateinfo list security        # RHEL8+
apt list --upgradable 2>/dev/null | grep -i security

# 应用
yum update --security -y
unattended-upgrade -d                # Debian/Ubuntu
```

**策略建议**：
- 关键安全补丁（内核、OpenSSL、glibc、sudo、Polkit）**24 小时内**打
- 其他补丁按周/月窗口批量打
- **内核更新需要重启**，安排维护窗口；重启前确认服务自启配置正确：
  `systemctl is-enabled <svc>`

```bash
systemctl is-enabled nginx mysql redis
# 输出应为 enabled，否则重启后服务不会起来
```

---

## 审计（auditd）

```bash
apt install -y auditd audispd-plugins    # 或 yum install audit

cat > /etc/audit/rules.d/99-ops.rules <<'EOF'
# 身份与提权
-w /etc/passwd -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/sudoers -p wa -k identity
-w /etc/sudoers.d -p wa -k identity
# 认证
-w /etc/ssh/sshd_config -p wa -k sshd
-w /root/.ssh -p wa -k sshkeys
# 持久化
-w /etc/crontab -p wa -k cron
-w /etc/cron.d -p wa -k cron
-w /var/spool/cron -p wa -k cron
-w /etc/systemd/system -p wa -k systemd
-w /etc/ld.so.preload -p wa -k rootkit
# 提权与关键命令
-a always,exit -F arch=b64 -S execve -F uid=0 -k rootcmd
-a always,exit -F arch=b64 -S setuid -k priv_change
EOF

augenrules --load
auditctl -l | head -20
systemctl enable --now auditd
```

⚠️ 规则越多日志越大，务必配置文件轮转与容量。

---

## 一条可用的最小加固清单

1. 防火墙默认拒绝入站 + 只放行业务端口（安全组同步收口）
2. SSH 禁 root、禁密码、禁空口令；部署 fail2ban
3. 数据库/缓存全部绑 127.0.0.1 + 强口令
4. 清理多余 UID=0 / 空口令账户，sudo 去 NOPASSWD
5. 关键文件权限收紧
6. 打安全补丁（含内核），并确认服务自启
7. 开启 auditd
8. `.ssh/authorized_keys` 逐条核对指纹
