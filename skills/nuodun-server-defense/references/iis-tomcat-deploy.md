# IIS / Tomcat 部署与调优手册

> 读这份文档的时机：需要**架设** IIS 或 Tomcat，或报告出现 `TOMCAT_NO_XMX` /
> `TOMCAT_NO_ENCODING` / `IIS_*` 条目时。

## 通用原则

1. **不要用默认配置上线**。默认配置面向"能跑起来"，不是面向"安全"和"性能"。
2. **日志目录与数据目录分开**，且不要放在系统盘。
3. **最小权限运行**：应用池身份 / Tomcat 用户都应是专用低权账户。
4. **先定端口与用途再装**：80/443 给反向代理，应用端口只监听内网。

---

# 一、部署 IIS

## 1. 安装

```powershell
# 基础（含管理控制台、静态内容、默认文档、HTTP 日志、请求筛选、压缩）
Install-WindowsFeature -Name Web-Server, Web-Mgmt-Console, Web-Static-Content, `
  Web-Default-Doc, Web-Http-Logging, Web-Request-Monitor, Web-Http-Errors, `
  Web-Stat-Compression, Web-Dyn-Compression, Web-Filtering, Web-Mgmt-Tools

# 需要 ASP.NET
Install-WindowsFeature -Name Web-Asp-Net45, Web-Net-Ext45, NET-Framework-45-Core
```

## 2. 基础加固

```powershell
Import-Module WebAdministration

# 隐藏版本号（避免暴露具体版本给扫描器）
Set-WebConfigurationProperty -Filter 'system.webServer/security/requestFiltering' `
  -Name 'removeServerHeader' -Value $true
Set-WebConfigurationProperty -Filter 'system.webServer/httpProtocol/customHeaders' `
  -Name '.' -Value @{name='X-Powered-By';value=''} -PSPath 'IIS:\' -ErrorAction SilentlyContinue

# 关闭目录浏览
Set-WebConfigurationProperty -Filter 'system.webServer/directoryBrowse' `
  -Name enabled -Value $false

# 限制请求体大小（防大包攻击，按业务调整）
Set-WebConfigurationProperty -Filter 'system.webServer/security/requestFiltering/requestLimits' `
  -Name maxAllowedContentLength -Value 104857600    # 100MB

# 禁用不必要的 HTTP 动词
Add-WebConfigurationProperty -Filter 'system.webServer/security/requestFiltering/verbs' `
  -Name '.' -Value @{verb='TRACE';allowed='false'}
Add-WebConfigurationProperty -Filter 'system.webServer/security/requestFiltering/verbs' `
  -Name '.' -Value @{verb='TRACK';allowed='false'}

# 自定义错误页（不泄露堆栈）
Set-WebConfigurationProperty -Filter 'system.webServer/httpErrors' `
  -Name existingResponse -Value 'Replace' -PSPath 'IIS:\'
```

## 3. 应用程序池加固

```powershell
# 每个站点独立应用池，身份用专用低权账户（不要用 LocalSystem）
$pool = 'MyAppPool'
New-WebAppPool -Name $pool -ErrorAction SilentlyContinue
Set-ItemProperty "IIS:\AppPools\$pool" -Name processModel.identityType -Value 4   # ApplicationPoolIdentity
Set-ItemProperty "IIS:\AppPools\$pool" -Name processModel.idleTimeout -Value '00:00:00'
Set-ItemProperty "IIS:\AppPools\$pool" -Name recycling.periodicRestart.time -Value '00:00:00'
# 定期回收（防内存泄漏累积），凌晨低峰
Set-ItemProperty "IIS:\AppPools\$pool" -Name recycling.periodicRestart.schedule `
  -Value @{value='03:00:00'}

# 32 位模式（仅当依赖 32 位组件时开启）
Set-ItemProperty "IIS:\AppPools\$pool" -Name enable32BitAppOnWin64 -Value $false
```

## 4. 站点创建模板

```powershell
New-Website -Name 'mysite' -PhysicalPath 'D:\sites\mysite' `
  -Port 8080 -ApplicationPool 'MyAppPool' -HostHeader 'app.internal'
Set-WebBinding -Name 'mysite' -BindingInformation '*:443:' -PropertyName Protocol -Value https
Set-WebBinding -Name 'mysite' -BindingInformation '*:443:' -PropertyName SslFlags -Value 1
```

**权限**：站点目录只授予应用池身份读权限；需要上传的目录单独授予写权限。

```powershell
icacls D:\sites\mysite /grant "IIS AppPool\MyAppPool:(OI)(CI)(RX)" /T
icacls D:\sites\mysite\uploads /grant "IIS AppPool\MyAppPool:(OI)(CI)(M)" /T
```

## 5. 日志优化

日志默认在 `%SystemDrive%\inetpub\logs\LogFiles`，**系统盘 + 无轮转 = 迟早写满**。

```powershell
# 改到数据盘
Set-WebConfigurationProperty -Filter 'system.applicationHost/sites/siteDefaults/logFile' `
  -Name directory -Value 'D:\logs\iis'
# 限制单文件大小并轮转（配合计划任务清理）
Set-WebConfigurationProperty -Filter 'system.applicationHost/sites/siteDefaults/logFile' `
  -Name truncateSize -Value 104857600    # 100MB
```

清理脚本（挂计划任务，保留 30 天）：

```powershell
$cut = (Get-Date).AddDays(-30)
Get-ChildItem D:\logs\iis -Recurse -Filter *.log |
  Where-Object { $_.LastWriteTime -lt $cut } | Remove-Item -Force
```

---

# 二、部署 Tomcat

## 1. 安装（Linux 推荐 systemd + 专用用户）

```bash
# 专用低权用户
useradd -r -s /sbin/nologin tomcat
mkdir -p /opt/tomcat && chown tomcat:tomcat /opt/tomcat

# 解压安装
cd /opt/tomcat
wget https://dlcdn.apache.org/tomcat/tomcat-9/v9.0.xx/bin/apache-tomcat-9.0.xx.tar.gz
tar xzf apache-tomcat-9.0.xx.tar.gz --strip-components=1
chown -R tomcat:tomcat /opt/tomcat
chmod +x /opt/tomcat/bin/*.sh
```

## 2. systemd 托管（配合资源限制）

```ini
# /etc/systemd/system/tomcat.service
[Unit]
Description=Apache Tomcat
After=network.target

[Service]
Type=forking
User=tomcat
Group=tomcat
Environment="JAVA_HOME=/usr/lib/jvm/java-17-openjdk"
Environment="CATALINA_HOME=/opt/tomcat"
Environment="CATALINA_BASE=/opt/tomcat"
EnvironmentFile=-/opt/tomcat/conf/setenv.conf
PIDFile=/opt/tomcat/temp/tomcat.pid

ExecStart=/opt/tomcat/bin/startup.sh
ExecStop=/opt/tomcat/bin/shutdown.sh
Restart=on-failure
RestartSec=10

# 资源与安全限制
LimitNOFILE=65535
MemoryMax=4G
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/opt/tomcat /data/logs

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now tomcat
```

## 3. JVM 参数（放在 setenv.sh，不要改 catalina.sh）

```bash
# /opt/tomcat/bin/setenv.sh
export CATALINA_OPTS="\
  -Xms2g -Xmx2g \
  -XX:MaxDirectMemorySize=1g \
  -XX:MetaspaceSize=256m -XX:MaxMetaspaceSize=512m \
  -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -XX:G1HeapRegionSize=16m \
  -XX:+HeapDumpOnOutOfMemoryError \
  -XX:HeapDumpPath=/data/dumps \
  -XX:ErrorFile=/data/dumps/hs_err_%p.log \
  -XX:+ExitOnOutOfMemoryError \
  -Dfile.encoding=UTF-8 -Dsun.jnu.encoding=UTF-8 \
  -Duser.timezone=Asia/Shanghai \
  -Djava.security.egd=file:/dev/./urandom"
```

**参数说明（每条都有理由）**：

| 参数 | 为什么 |
|---|---|
| `-Xms` = `-Xmx` | 堆不动态伸缩，避免扩容抖动与额外 GC |
| `-XX:MaxDirectMemorySize` | **限制堆外**。Netty/Lettuce 等 NIO 库大量用堆外，不设上限会吃光物理内存 |
| `-XX:+HeapDumpOnOutOfMemoryError` | OOM 时自动 dump，事后可分析 |
| `-XX:ErrorFile` | 指定 hs_err 位置，默认写到工作目录会弄丢 |
| `-XX:+ExitOnOutOfMemoryError` | OOM 后立即退出，交给 systemd 拉起，而不是僵死半响应 |
| `-Dfile.encoding=UTF-8` | 中文 Windows 上默认 GBK，跨端会乱码 |
| `-Djava.security.egd` | 避免 Tomcat 启动时读 `/dev/random` 阻塞（云主机常见启动慢） |

⚠️ **Windows 上的 Tomcat**：用 `setenv.bat`，且要注意 `CATALINA_OPTS` 与 `JAVA_OPTS`
的区别 —— 前者只用于 Tomcat 自身进程，后者会被 stop/shutdown 等脚本继承。

## 4. server.xml 加固

```xml
<!-- 关闭自动部署（防止上传 war 直接执行） -->
<Host name="localhost" appBase="webapps" unpackWARs="true" autoDeploy="false" deployOnStartup="true">

<!-- 屏蔽版本信息 -->
<Connector port="8080" protocol="org.apache.coyote.http11.Http11NioProtocol"
    connectionTimeout="20000"
    maxThreads="400" minSpareThreads="50"
    acceptCount="200"            <!-- 等待队列，配合 somaxconn -->
    maxConnections="10000"
    URIEncoding="UTF-8"
    server="Server"              <!-- 覆盖默认的 Apache-Coyote/1.1 -->
    compression="on" compressionMinSize="2048"
    compressableMimeType="text/html,text/xml,text/plain,text/css,application/json,application/javascript"
    maxPostSize="104857600"
    maxHttpHeaderSize="16384"
/>

<!-- 管理端口只监听本机 -->
<Connector port="8005" address="127.0.0.1" shutdown="SHUTDOWN_SECRET" />
```

**必须做的两件事**：
1. 删除 `webapps` 下的 `manager` / `host-manager`（除非确实要用，且必须改强口令 + IP 白名单）
2. 关掉 `autoDeploy`，改用手动部署

```bash
rm -rf /opt/tomcat/webapps/manager /opt/tomcat/webapps/host-manager /opt/tomcat/webapps/docs
rm -rf /opt/tomcat/webapps/examples
```

## 5. 日志

```bash
# 日志目录迁到独立盘，避免写满系统盘
mkdir -p /data/logs/tomcat && chown tomcat:tomcat /data/logs/tomcat
ln -sfn /data/logs/tomcat /opt/tomcat/logs

# 配置轮转（logrotate）
cat > /etc/logrotate.d/tomcat <<'EOF'
/data/logs/tomcat/*.log /data/logs/tomcat/*.out {
    daily
    rotate 30
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
EOF
```

`copytruncate` 很关键 —— Tomcat 不会自己重开日志文件，没有它轮转后日志会继续写旧句柄。

---

# 三、Windows 上跑 Tomcat 的额外注意

```powershell
# 以服务方式安装（不要用 startup.bat 挂在桌面会话里）
# 推荐用 WinSW 或 Tomcat 自带的 service.bat
D:\tomcat\bin\service.bat install MyTomcat
# 服务的"登录身份"改为专用低权账户，不要 LocalSystem

# 确认文件编码参数已生效
# 在 setenv.bat 中：
set "CATALINA_OPTS=-Xms2g -Xmx2g -XX:MaxDirectMemorySize=1g ^
 -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=D:\dumps ^
 -Dfile.encoding=UTF-8 -Dsun.jnu.encoding=UTF-8 -Duser.timezone=Asia/Shanghai"
```

**中文乱码的完整清单**（缺一个都会乱）：

| 环节 | 设置 |
|---|---|
| JVM 读取文件 | `-Dfile.encoding=UTF-8` |
| JVM 环境 | `-Dsun.jnu.encoding=UTF-8` |
| Connector | `URIEncoding="UTF-8"` |
| HttpServletResponse | `response.setCharacterEncoding("UTF-8")` |
| JDBC 连接串 | `?useUnicode=true&characterEncoding=utf8` |
| 数据库表/库 | `CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci` |

验证是否生效：

```powershell
# 查进程命令行里有没有该参数（这才是"配置生效"的证据）
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'java' } |
  Select-Object ProcessId, CommandLine
```

```bash
cat /proc/$(pgrep -f tomcat | head -1)/cmdline | tr '\0' '\n' | grep -i 'file.encoding'
```

---

# 四、上线前检查清单

- [ ] 应用池 / Tomcat 跑在专用低权账户上，不是 root / LocalSystem
- [ ] 应用端口只监听内网，对外只开 80/443 由反向代理转发
- [ ] 管理界面（manager / iis 管理服务）已删除或 IP 白名单 + 强口令
- [ ] 关闭默认示例应用与自动部署
- [ ] JVM 堆与堆外都设了上限，且 `Xms=Xmx`
- [ ] 日志目录在数据盘，且配置了轮转与清理
- [ ] 数据库连接串带 UTF-8 参数
- [ ] 上传目录做了可执行权限剥离（上传目录不解析脚本）
- [ ] 已配置进程守护（systemd `Restart=on-failure` / Windows 服务恢复策略）
- [ ] 已确认重启后服务能自动起来：`systemctl is-enabled <svc>`
