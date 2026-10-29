# Windows 安全加固手册

> 读这份文档的时机：报告出现 `RDP_*` / `SMB*` / `WALL_*` / `FIREWALL_OFF` /
> `PASSWORD_POLICY` / `NO_LOCKOUT` / `UAC_OFF` / `SMB1_ENABLED` /
> `SHARE_ROOT_EXPOSED` / `RISKY_PORTS_LISTENING` / `PATCH_STALE` 等条目时。

## 加固的正确姿势

1. **先备份注册表与配置**（本技能的 `harden` 会自动做快照）。
2. **一次只改一类**，改完立刻验证；不要一次性改 20 项然后不知道哪项压垮了服务。
3. **保留回滚脚本**。
4. **远程操作时格外小心**：改防火墙/远程桌面/NLA 有把自己关在门外的风险，
   云主机务必先确认控制台（VNC）可用。

---

## 远程桌面

这是 Windows 服务器最常被打的点，值得单独一节。

### 检查现状

```powershell
# 是否启用
Get-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections
# 0 = 已启用远程桌面

# 是否强制 NLA
Get-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' `
  -Name UserAuthentication
# 1 = 启用 NLA（正确）；0 或缺失 = 未启用（危险）

# 端口
Get-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' `
  -Name PortNumber
```

### 加固动作

```powershell
# 1) 强制 NLA（BlueKeep 类漏洞的主要缓解手段）
Set-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' `
  -Name UserAuthentication -Value 1

# 2) 防火墙只放行内网来源
New-NetFirewallRule -DisplayName 'RDP-Limited-Scope' -Direction Inbound `
  -Protocol TCP -LocalPort 3389 -Action Allow -RemoteAddress LocalSubnet

# 3) 限制并发会话数（防资源耗尽）
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' `
  -Name MaxInstanceCount -Value 5 -Type DWord -Force

# 4) 空闲自动断开
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' `
  -Name MaxIdleTime -Value 1800000 -Type DWord -Force   # 30 分钟
```

⚠️ **改防火墙后立刻用另一个会话验证还能连上**，再关掉原来那个。

### 更彻底的方案

把 3389 从公网摘掉，改走 **VPN / 堡垒机 / 云厂商的会话管理器**
（阿里云的「远程连接」、AWS 的 Session Manager、Azure Bastion）。
端口改非 3389 只是降噪，不是防护。

---

## 账户与口令

### 检查

```powershell
net accounts
Get-LocalUser | Select-Object Name,Enabled,PasswordRequired,PasswordLastSet,LastLogon
Get-LocalGroupMember -Group Administrators | Select-Object Name,PrincipalSource
```

### 加固

```powershell
# 口令与锁定策略
net accounts /minpwlen:12 /maxpwage:90 /minpwage:1 /uniquepw:5 `
             /lockoutthreshold:5 /lockoutduration:15 /lockoutwindow:15

# 禁用不用的内置账户
net user Guest /active:no
net user Administrator /active:no     # ⚠ 先确认有另一个管理员可用！

# 重命名内置管理员（降低爆破命中率）
wmic useraccount where name='Administrator' rename 'svc_admin_ops'   # 或用计算机管理 GUI

# 排查空口令账户
Get-LocalUser | Where-Object { $_.PasswordRequired -eq $false -and $_.Enabled }
```

### 管理员组瘦身

```powershell
Get-LocalGroupMember -Group Administrators | Format-Table
# 移除多余成员（保留至少一个可用管理员）
Remove-LocalGroupMember -Group Administrators -Member 'DOMAIN\someuser'
```

**判据**：管理员组成员 ≤ 5 个，且每个都能说清用途。

---

## 防火墙

```powershell
# 三个 profile 都必须启用
Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction
Set-NetFirewallProfile -All -Enabled True -DefaultInboundAction Block `
  -DefaultOutboundAction Allow

# 看当前放行了哪些入站端口（重点核对）
Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow |
  Get-NetFirewallPortFilter |
  Select-Object @{n='Rule';e={$_.InstanceID}}, Protocol, LocalPort |
  Where-Object { $_.LocalPort -ne 'Any' } | Sort-Object LocalPort
```

**默认拒绝入站 + 白名单放行** 是唯一正确的姿势。看到入站规则里有 `LocalPort = Any` 的
Allow 规则要特别警惕 —— 那等于没防火墙。

---

## SMB 与共享

```powershell
# SMBv1 必须关（WannaCry / NotPetya 的传播载体）
Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol,RequireSecuritySignature,EnableInsecureGuestLogons
Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force
Set-SmbClientConfiguration -EnableInsecureGuestLogons $false -Force
Set-SmbServerConfiguration -RequireSecuritySignature $true -Force

# 彻底卸载 SMBv1 功能
Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart
# 或（Server）
Uninstall-WindowsFeature FS-SMB1

# 检查共享
Get-SmbShare | Where-Object { $_.Name -notlike '*$' } | Select-Object Name,Path
Get-SmbShareAccess -Name <共享名>
```

**红旗**：共享路径是 `C:\` 这类盘符根。改成共享具体子目录，并在 NTFS 上收紧 ACL。

---

## 补丁

```powershell
# 最近安装的补丁
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10
# 是否还有待装
Get-WindowsUpdate -Install -AcceptAll   # 需 PSWindowsUpdate 模块
# 或用系统自带
UsoClient StartScan
```

**判据**：距最近补丁不超过 30~60 天；关键漏洞（如 PrintNightmare、PetitPotam）当天必须打。

---

## UAC 与纵深防御

```powershell
# UAC 必须开启
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' `
  -Name EnableLUA, ConsentPromptBehaviorAdmin, PromptOnSecureDesktop
```

推荐值：

| 项 | 值 | 含义 |
|---|---|---|
| EnableLUA | 1 | 开启 UAC |
| ConsentPromptBehaviorAdmin | 2 | 提示凭据（服务器建议）或 5（提示同意，桌面机） |
| PromptOnSecureDesktop | 1 | 安全桌面（防伪造 UAC 弹窗） |
| EnableInstallerDetection | 1 | 安装程序检测 |
| FilterAdministratorToken | 1 | 内置管理员也走 UAC |

```powershell
# 关闭 LLMNR（Responder 类中间人工具的主要凭据收割面）
New-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' `
  -Name EnableMulticast -Value 0 -PropertyType DWord -Force

# 关闭 NetBIOS over TCP/IP（逐个网卡）
$k='HKLM:\SYSTEM\CurrentControlSet\Services\NetBT\Parameters\Interfaces'
Get-ChildItem $k | ForEach-Object { Set-ItemProperty $_.PSPath -Name NetbiosOptions -Value 2 }
```

---

## 暴露端口

```powershell
Get-NetTCPConnection -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess,
    @{n='Proc';e={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}} |
  Sort-Object LocalPort
```

高风险清单：

| 端口 | 服务 | 处置 |
|---|---|---|
| 3389 | RDP | 限制来源 IP；优先只走 VPN |
| 445 / 139 | SMB | 防火墙只放行内网；公网必须封 |
| 1433 | SQL Server | 只监听内网；改非默认端口仅降噪 |
| 3306 | MySQL | `bind-address = 127.0.0.1` + 强口令 |
| 6379 | Redis | 设 `requirepass` + bind 内网（未授权可直接写 SSH key） |
| 27017 | MongoDB | 开鉴权；默认无认证是拖库重灾区 |
| 135 | RPC | 公网封禁 |
| 23 / 21 | Telnet / FTP | 不使用则彻底卸载 |

```powershell
# 封禁某个端口的所有入站
New-NetFirewallRule -DisplayName 'Block-6379-Public' -Direction Inbound `
  -Protocol TCP -LocalPort 6379 -RemoteAddress Any -Action Block
```

---

## 审计与日志

```powershell
# 开启关键审计（等保基本要求，也是入侵溯源的唯一依据）
auditpol /set /subcategory:"Logon" /success:enable /failure:enable
auditpol /set /subcategory:"Account Logon" /success:enable /failure:enable
auditpol /set /subcategory:"Account Management" /success:enable /failure:enable
auditpol /set /subcategory:"Process Creation" /success:enable
auditpol /set /subcategory:"Policy Change" /success:enable /failure:enable

# 进程创建审计建议同时开启命令行记录
New-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit' `
  -Name ProcessCreationIncludeCmdLine_Enabled -Value 1 -Type DWord -Force

# 扩大安全日志容量（默认 20MB 会被冲掉）
wevtutil sl Security /ms:1073741824     # 1GB
```

**注意**：开了进程审计后日志会快速增长，务必配合容量与归档策略。

---

## 一条可用的最小加固清单

按性价比排序，时间有限就按这个顺序做：

1. 防火墙三个 profile 全开 + 默认拒绝入站
2. RDP 加 NLA + 限制来源 IP（或改走 VPN）
3. SMBv1 关闭 + 强制签名
4. 口令长度 ≥ 12 + 锁定阈值 5 次
5. 管理员组瘦身，禁用 Guest / 内置 Administrator
6. 开启审计策略 + 扩大安全日志容量
7. LLMNR / NetBIOS 关闭
8. 打齐补丁
