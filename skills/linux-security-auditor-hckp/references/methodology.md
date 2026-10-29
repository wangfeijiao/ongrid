# Linux 安全加固基线 · 审计方法学

本 skill 的审计规则依据 CIS (Center for Internet Security) Benchmark 的通用思路裁剪为
可离线解析的纯文本检查，覆盖 9 个维度。每条规则给出「为什么要查」与「怎么修」。

## 1. SSH 基线（[sshd_config]）
| 检查项 | 期望 | 风险 |
|---|---|---|
| PermitRootLogin | no | root 直登=爆破直攻管理员 |
| PasswordAuthentication | no（仅密钥） | 密码爆破 |
| Protocol | 2 | SSHv1 明文 |
| MaxAuthTries | ≤4 | 在线爆破窗口 |
| PermitEmptyPasswords | no | 空密码登录 |
| X11Forwarding | no | 缩小攻击面 |

## 2. 防火墙（[firewall]）
- 必须启用且 INPUT 默认 DROP/REJECT；仅放行 22/80/443 等必要端口。
- INPUT 默认 ACCEPT 且无逐条放行，等于全裸。

## 3. 内核加固（[sysctl]）
- `rp_filter=1` 防 IP spoofing；`accept_redirects/send_redirects=0` 防路由篡改；
- `tcp_syncookies=1` 缓解 SYN flood；`icmp_echo_ignore_broadcasts=1` 防 Smurf 放大；
- `randomize_va_space=2` 全随机 ASLR（提权利用难度）；`ip_forward=0`（非路由器不应转发）。

## 4. 特权账户（[users]）
- 仅 root 允许 UID 0；任何第二个 UID0 账户即后门信号。
- 密码字段为空（`x` 段为 `` 或 `!`/`*` 但配可交互 shell）属弱口令面。
- 系统账户 shell 应为 `/usr/sbin/nologin` 或 `/bin/false`。

## 5. SUID/SGID 提权点（[suid]）
- SUID 程序以 owner（常是 root）权限运行，是经典提权/持久化载体。
- 白名单外的非常规路径（尤其 `/usr/local`、`/opt`、tmpfs）高度可疑，需 `rpm -qf`/`dpkg -S` 溯源。

## 6. 危险启用服务（[services]）
- telnet/rsh/rlogin/rsh-server：明文或遗留协议，应禁用并卸载。
- tftp/nfs/rpcbind：非必要不对外，内网也建议最小暴露。

## 7. 公网暴露端口（[listening]）
- 23/512-514/111/2049/139/445 等明文/遗留端口绝不应对 0.0.0.0 开放。
- 3306/5432/6379/27017/9200 等数据库端口监听公网 = 拖库风险，应绑 127.0.0.1 或加来源 IP 限制。

## 8. 明文/遗留软件包（[packages]）
- 通过包名启发式识别 telnetd、rsh-server、vsftpd、tftpd 等高风险包并建议卸载。

## 9. 可疑 root 计划任务（[cron]）
- root cron 指向 `/tmp`、`/var/tmp` 等可写目录是常见持久化后门特征，应迁移到仅 root 可写目录。

## 评分模型
- 扣分权重：CRITICAL=12 / HIGH=7 / MEDIUM=3 / LOW=1 / PASS/INFO=0。
- `score = max(0, 100 - Σ扣减)`，按 90/75/55/35 划分为 A~E 五档。
- 仅对"已提供且适用"的检查项计分；未提供段不参与扣减（不会因缺数据而误判高危）。
