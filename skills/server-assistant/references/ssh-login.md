# SSH 登录全景

`ssh` 参数、密钥与免密登录、`sshd_config` 关键项、五类连接失败报错对照、端口转发与跳板。`203.0.113.10` 是文档保留地址，换成你自己的公网 IP；命令都可直接复制。

## 一、ssh 命令与常用参数

```bash
# ✅ 只读：最简（默认 22 端口）
ssh root@203.0.113.10
# ✅ 只读：-p 非标端口（服务器改过端口时必须带）
ssh -p 2222 root@203.0.113.10
# ✅ 只读：-v 调试（排障第一命令，加 tail 防刷屏），-o 绕过 known_hosts 指纹确认（仅测试环境）
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no -v deploy@203.0.113.10 2>&1 | tail -20
```

**为什么先跑 `-v`**：九成答案在最后 20 行输出里。**坑点**：私钥路径写错时 ssh 不报错，只静默试默认密钥，看起来像「密码一直不对」。

## 二、生成密钥对

```bash
# ⚠️ 有副作用：覆盖同名文件，要保留就换 -f 文件名
ssh-keygen -t ed25519 -C "yourname@work" -f ~/.ssh/id_ed25519
# ⚠️ 有副作用：旧 Windows OpenSSH 不支持 ed25519 时改用 RSA
ssh-keygen -t rsa -b 4096 -C "yourname@work" -f ~/.ssh/id_rsa_4096
```

**为什么选 ed25519**：更短更快、安全性不输 RSA，OpenSSH 7.4+ 默认推荐。**坑点**：私钥权限必须 600 否则 ssh 直接拒绝使用（见第九类第 5 条，报 too open）；Git Bash 私钥在 `C:\Users\你\.ssh\`，WSL 侧看不到。以当前系统版本为准。

## 三、公钥放到服务器的三种方式

```bash
# 方式 1：ssh-copy-id 最省事（macOS/Linux 自带，Windows 新版 OpenSSH 也有），⚠️ 有副作用：写入公钥并重置远端权限；非标端口加 -p
ssh-copy-id -i ~/.ssh/id_ed25519.pub -p 22 deploy@203.0.113.10
# 方式 2：兜底手动追加，务必 >> 而不是 >（⚠️ 有副作用，> 会清空别人已授权的公钥）
cat ~/.ssh/id_ed25519.pub | ssh deploy@203.0.113.10 \    # ⚠️ 有副作用
  "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

**为什么**：三种结果相同，区别在手上有什么；控制台「导入密钥对」适合还没开 SSH 口的机器（⚠️ 有副作用，部分平台会重置 root 密码，以平台文档为准）。**坑点**：`-i` 要写 `.pub` 公钥文件，写成私钥会报 not a public key。

## 四、免密登录 step by step（含验证）

```bash
# 第 1 步 本地确认私钥权限（✅ 只读 / ⚠️ 有副作用）
ls -l ~/.ssh/id_ed25519 && chmod 600 ~/.ssh/id_ed25519   # 期望 -rw-------
# 第 2 步 传公钥（⚠️ 有副作用）；第 3 步 服务器核对（⚠️ 有副作用，chmod 改权限）
ssh-copy-id -i ~/.ssh/id_ed25519.pub deploy@203.0.113.10
ssh deploy@203.0.113.10 'ls -la ~/.ssh/ && wc -l ~/.ssh/authorized_keys'
# 第 4 步 先验证免密可用，必须成功才能做下一步（✅ 只读）
ssh -i ~/.ssh/id_ed25519 deploy@203.0.113.10 'echo OK && whoami'
# 第 5 步 确认无误后才禁密码登录（⚠️ 有副作用：改前先备份）
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak && sudo vim /etc/ssh/sshd_config
# ✅ 只读：语法不通别 reload；⚠️ 有副作用：平滑重载不断连接（CentOS/Rocky 服务名 sshd）
sudo sshd -t && sudo systemctl reload ssh
```

**为什么第 4 步必须做**：第 5 步做错就把自己关在门外，至少留一个控制台窗口保底。**坑点**：`reload` 不断开连接，`restart` 会踢掉所有人，改监听地址才需要 restart。

**实测补充（本机 sshd 复现）**

```bash
# ✅ 只读：故意写错一项，sshd -t 会告诉你在第几行、错在哪
# 实测输出（把 Port 2222 改成 Port not-a-port）：
#   /tmp/ssh-verify/bad_config line 1: Badly formatted port number.
# 合法配置下 sshd -t 完全静默无输出 —— 所以「有没有输出」就是判断标准
sudo sshd -t

# ✅ 只读：想知道这些设置真正生效成什么值（而不是文件里写的）
# 实测输出：
#   port 2222
#   permitrootlogin no
#   pubkeyauthentication yes
#   passwordauthentication no
sudo sshd -T | grep -E '^(port|permitrootlogin|pubkeyauthentication|passwordauthentication) '
```

为什么值得记：`sshd_config` 里被 `Include` 进来的片段（如 `/etc/ssh/sshd_config.d/*.conf`）会覆盖主文件里的同名项，**只看文件会以为改了、其实没生效**。`sshd -T` 给的是叠加后的真实结果，配合 `sshd -t` 一前一后，就是改配置的标准动作。

## 五、sshd_config 关键项

文件在 `/etc/ssh/sshd_config`，部分发行版主配置 `Include` 了 `/etc/ssh/sshd_config.d/*.conf`（新装 Ubuntu 尤其常见）。

| 指令 | 推荐值 | 含义与坑 |
|---|---|---|
| `Port 22` | 改非标端口 | 减少扫描；改了本地必须同步 `-p` |
| `PermitRootLogin no` | no | 禁 root 直登；改前确保有能 sudo 的用户 |
| `PubkeyAuthentication yes` | yes | 免密登录的前提 |
| `PasswordAuthentication no` | 确认免密后改 | 挡暴力破解，改错就进不去 |
| `AllowUsers deploy` | 按需 | 白名单，不在名单里一律拒绝（配错自己也进不去） |

```bash
# ✅ 只读：看真生效值（sshd_config.d 片段会覆盖主配置）；sshd 监听的端口
sudo sshd -T | grep -E '^(port|permitrootlogin|passwordauthentication|pubkeyauthentication)'
ss -tulnp | grep sshd
```

**坑点**：主配置改了没反应，八成是 `sshd_config.d/` 里某片段也写了同项——只有 `sshd -T` 的输出算数。

## 六、不同系统连 Linux 的差异

| 系统 | 终端 | 注意事项 |
|---|---|---|
| macOS | 系统「终端」/ iTerm2 | 自带 OpenSSH，`ssh-add -l` 看 agent 密钥 |
| Windows 11 | 「终端」/ PowerShell | 自带 OpenSSH，`ssh` 可直接用 |
| Windows 旧版 / WSL | 另装 OpenSSH 或 WSL | 旧版需启用「OpenSSH 客户端」功能 |

```bash
# ✅ 只读（macOS/Linux）：加入 agent 后可省略 -i，-l 列出 / -D 清空
ssh-add ~/.ssh/id_ed25519 && ssh-add -l
```

以当前系统版本为准（较老 OpenSSH 不支持 ed25519 时改用 RSA）。

## 七、端口转发与常连

```bash
# ⚠️ 有副作用：服务器本机监听 3307，转发到内网 127.0.0.1:3306
ssh -L 3307:127.0.0.1:3306 deploy@203.0.113.10
# ⚠️ 有副作用：-fN 后台常连（不开 shell），用 pkill -f 结束
ssh -fN -L 127.0.0.1:3307:127.0.0.1:3306 deploy@203.0.113.10
# ✅ 只读：确认转发建立（lsof 在 macOS 也有）
lsof -i :3307
```

**为什么**：数据库默认只听 127.0.0.1，又不想对公网开 3306，转发是最省事的安全通道。**坑点**：不带 `-N` 会进 shell，一关终端转发就断。

## 八、SSH agent 与跳板

```bash
# ⚠️ 有副作用：建立到跳板的连接；更常用写法是把下面写进 ~/.ssh/config
cat >> ~/.ssh/config << 'EOF'
Host myserver
  HostName 203.0.113.10
  User deploy
  Port 2222
  IdentityFile ~/.ssh/id_ed25519

Host internal
  HostName 10.0.0.5
  User deploy
  ProxyJump myserver
EOF
# ⚠️ 有副作用：权限不对 ssh 会忽略这个文件；✅ 只读：之后 ssh internal 即可
chmod 600 ~/.ssh/config && ssh internal
```

**坑点**：`~/.ssh/config` 必须 600；内网目标机也要先放好公钥，跳板只负责 transport。

## 九、五类连接失败逐条对照

### 1. 连接超时

**报错原文**：`ssh: connect to host 203.0.113.10 port 22: Connection timed out` —— 根因与解决：包没到服务器（安全组未放行 22 / 实例关机 / IP 写错）；放行安全组入方向 22、确认实例运行中、确认 IP 正确。

```bash
# ✅ 只读：能 ping 通说明服务器活着；nc 无输出说明包被丢
ping -c 3 203.0.113.10 && nc -zv -w 5 203.0.113.10 22
```

### 2. 连接被拒绝

**报错原文**：`ssh: connect to host 203.0.113.10 port 22: Connection refused` —— 根因与解决：包到了但 22 没人监听，即 sshd 没起或监听在别的端口；`sudo systemctl enable --now sshd`（⚠️ 有副作用），端口改过就 `ssh -p 新端口`。

```bash
# ✅ 只读：看 sshd 状态与监听端口
systemctl status sshd --no-pager && ss -tulnp | grep sshd
```

### 3. 权限被拒绝（publickey）

**报错原文**：`deploy@203.0.113.10: Permission denied (publickey).` —— 根因与解决：公钥收到但认证没过，最常见是 `~/.ssh` 不是 700 / `authorized_keys` 不是 600，或公钥内容损坏（多了空格、少了 `ssh-ed25519 ` 前缀）。

```bash
# ⚠️ 有副作用：最常见的修复；✅ 只读：第一行应 ssh-ed25519 开头，损坏就重传
ssh deploy@203.0.113.10 'chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && head -1 ~/.ssh/authorized_keys'
```

**★ 排障金钥匙：看报错括号里到底列了什么**

```bash
# 实测采集（OpenSSH 9.4p1）：
# Permission denied (publickey,password,keyboard-interactive)  ← 服务端三种都开着
# Permission denied (publickey)                                ← 服务端只认密钥
```

括号里的列表 = **服务端 sshd 实际放行的认证方式**，不用登服务器就能读到：

- 出现 `password` → 服务端 `PasswordAuthentication yes`，说明你可能只是**密码还没输对或已过期**，别急着折腾密钥
- 只有 `publickey` → 已按你的要求禁了密码登录，**只剩下密钥一条路**，权限/内容错了就是这类报错
- 出现 `keyboard-interactive` → 可能还挂着 OTP/双因子（如云厂商的二次验证）

这就把"报错长的样子"直接映射成"服务端配成了什么样"，省掉一次登服务器。

### 4. 主机名解析失败

**报错原文**：`ssh: Could not resolve hostname myserver: nodename nor servname provided, or not known` —— 根因与解决：写的是主机名/域名却解析不到，或 DNS 未生效；先用公网 IP 确保能连，用域名就等解析生效（受 TTL 影响）。

```bash
# ✅ 只读：确认解析到哪；ping 能通则问题只在 DNS
dig +short myserver.example.com && nslookup myserver.example.com
```

### 5. 密钥权限太开放

**报错原文**：`Permissions 0644 for '/Users/me/.ssh/id_ed25519' are too open.` —— 根因与解决：OpenSSH 的安全设计，不是故障；`chmod 600 ~/.ssh/id_ed25519 && chmod 700 ~/.ssh`（⚠️ 有副作用，改自己电脑上私钥权限）。

**⚠️ 实测校正（本机 OpenSSH 9.4p1 / macOS 复现）**：权限宽的私钥并不一定会报 `too open`。实测把私钥改成 644 再连，输出与 600 时**完全一致**——都是 `Permission denied (publickey,password,keyboard-interactive)`，**没有任何 too open 提示**。也就是说：

```text
644 私钥  → Permission denied (publickey,password,keyboard-interactive)
600 私钥  → Permission denied (publickey,password,keyboard-interactive)   ← 一字不差
```

**所以别把"改了权限就好了"当第一反应**：在 `ssh -i` 这条路径上，权限问题不会给你独立的报错，它会伪装成普通的认证失败，把你往错误的方向带。

- `too open` 更常出现在**私钥已装入 ssh-agent**的场景，或某些发行版/旧版本上
- 600 依然是必须做的安全习惯（644 等于把私钥公开给同机任何账号），只是它**不一定**会立刻报错

排查顺序应该是：先确认公钥在不在服务器上、内容完不完整，再顺手把权限修成 600。

还有别的报错？去 `server-troubleshooting.md` 按报错原文索引。

---

## 下一步

- 连上了，要建用户 / 改密码 / 配防火墙 → @references/server-init.md
- 要传文件、同步目录 → @references/file-transfer.md
- 要加固：禁密码登录、改端口、fail2ban → @references/server-security.md
- 补服务器整体认知（安全组、选配置） → @references/server-basics.md
