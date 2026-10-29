#Requires -Version 5.1
<#
.SYNOPSIS
  智能磁盘清理助手 - 只读扫描器（无任何删除动作）

.DESCRIPTION
  低优先级、流式枚举的性能友好型扫描器。只读取、只统计、只输出，绝不修改任何文件。
  输出三类报告：
    1. 目标盘分区概况
    2. 本机性能画像（用于确定后续清理节奏）
    3. 候选目录清单（大小 / 文件数 / 最后修改时间 / 一级子目录）

.PARAMETER Drive
  要扫描的盘符，默认 C。

.PARAMETER OutFile
  结果输出文件绝对路径。默认写到当前工作目录下的 disk_scan_report.txt。
  注意：本执行环境下 stdout 回传可能不可靠，统一采用"写文件再读"模式。

.PARAMETER MinSizeMB
  低于该体积的目录不进入清单，默认 100 MB。

.PARAMETER Deep
  开关。加上后额外扫描 AppData\Local 与 Roaming 的每个一级子目录（更慢但更全）。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scan_and_report.ps1 -Drive C -Deep
#>

param(
    [string]$Drive    = 'C',
    [string]$OutFile  = (Join-Path (Get-Location).Path 'disk_scan_report.txt'),
    [int]   $MinSizeMB = 100,
    [switch]$Deep
)

# --- 自我降级为 Idle 优先级，避免与用户前台任务争抢 ---
try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch {}
$ErrorActionPreference = 'SilentlyContinue'

$L = New-Object System.Collections.ArrayList

# ============================== 0. 分区概况 ==============================
[void]$L.Add('===== [PARTITION] =====')
foreach ($d in (Get-CimInstance Win32_LogicalDisk)) {
    if ($d.DriveType -ne 3) { continue }
    $total = [double]$d.Size
    $free  = [double]$d.FreeSpace
    if ($total -le 0) { continue }
    [void]$L.Add(('{0}: total={1:N1}GB free={2:N1}GB used={3:N1}GB pct={4:N0}%' -f `
        $d.DeviceID, ($total/1GB), ($free/1GB), (($total-$free)/1GB), ((($total-$free)/$total)*100)))
}

# ============================== 1. 性能画像 ==============================
[void]$L.Add('')
[void]$L.Add('===== [PERFORMANCE] =====')
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$os  = Get-CimInstance Win32_OperatingSystem
$memTotalGB = $os.TotalVisibleMemorySize / 1MB
$memFreeGB  = $os.FreePhysicalMemory / 1MB
$memUsedPct = (($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100
$cpuLoad    = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average

[void]$L.Add(('CPU={0} cores={1} threads={2} load={3}%' -f ($cpu.Name -replace '\s+',' ').Trim(), $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors, $cpuLoad))
[void]$L.Add(('MEM total={0:N1}GB free={1:N1}GB usedPct={2:N0}%' -f $memTotalGB, $memFreeGB, $memUsedPct))
[void]$L.Add(('BOOT={0}' -f $os.LastBootUpTime))
foreach ($pd in (Get-PhysicalDisk)) {
    [void]$L.Add(('DISK={0} media={1} bus={2} size={3:N0}GB' -f $pd.FriendlyName, $pd.MediaType, $pd.BusType, ($pd.Size/1GB)))
}

# --- 冗余度评估：决定"能不能开始清理"，而不只是"还剩多少内存" ---
# 冗余度 = 清理前可用内存 − 清理过程预估峰值占用
# 预估峰值 = 脚本自身(约0.2GB) + 文件系统缓存(约0.3GB) + 用户已有负载波动(约1.0GB)
$overheadGB  = 1.5
$headroomGB  = $memFreeGB - $overheadGB

if     ($headroomGB -gt 4) { $tier = 'NORMAL';         $batch = 10; $snd = '每3000-4000文件让出120ms'; $depth = '可开 -Deep 深度扫描' }
elseif ($headroomGB -gt 2) { $tier = 'CONSERVATIVE';   $batch = 5;  $snd = '每2000文件让出200ms';      $depth = '仅扫已知目标清单' }
elseif ($headroomGB -gt 1) { $tier = 'TIGHT';          $batch = 3;  $snd = '每1500文件让出300ms';      $depth = '仅扫已知目标清单，放慢执行' }
else                       { $tier = 'INSUFFICIENT';   $batch = 0;  $snd = '—';                        $depth = '拒绝执行删除，仅只读扫描' }

[void]$L.Add(('HEADROOM={0:N2}GB  (Free {1:N2}GB - Overhead {2:N1}GB)' -f $headroomGB, $memFreeGB, $overheadGB))
[void]$L.Add(("TIER={0}|batchMax={1}|sleep={2}|depth={3}" -f $tier, $batch, $snd, $depth))

switch ($tier) {
    'NORMAL'       { [void]$L.Add('  -> 冗余充裕，可按正常节奏执行') }
    'CONSERVATIVE' { [void]$L.Add('  -> 冗余足够，按保守节奏执行') }
    'TIGHT'        { [void]$L.Add('  -> 冗余偏紧，放慢节奏执行；并向用户说明当前余量紧张') }
    'INSUFFICIENT' { [void]$L.Add('  -> 冗余不足！拒绝执行删除动作。') 
                     [void]$L.Add('     请先关闭部分程序释放内存，或稍后再清理。') }
}

# --- 用户当前负载基线：清理会与这些程序争抢资源 ---
[void]$L.Add('')
[void]$L.Add('--- 用户当前负载 TOP8（内存占用）---')
$heavyKw = 'chrome|msedge|firefox|Code|idea|pycharm|vmware|VirtualBox|Trae|Qoder|Qianwen|WeChat|Weixin|Docker|node'
$heavyCount = 0
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 8 | ForEach-Object {
    $memMB = [math]::Round($_.WorkingSet64/1MB, 0)
    [void]$L.Add(('PROC|{0}|{1}MB' -f $_.Name, $memMB))
}
Get-Process | Where-Object { $_.Name -match $heavyKw } | Group-Object Name | ForEach-Object {
    $m = [math]::Round((($_.Group | Measure-Object WorkingSet64 -Sum).Sum)/1MB, 0)
    if ($m -ge 200) { $heavyCount++ }
}
[void]$L.Add(("HEAVY_APPS={0}| 检测到 {1} 个重负载程序在运行" -f $heavyCount, $heavyCount))
if ($heavyCount -ge 3 -and $tier -ne 'NORMAL') {
    [void]$L.Add('  -> 提示：多个重负载程序正在运行且冗余偏紧，建议先关闭其中不用的再执行清理')
}
if ($cpuLoad -gt 70) { [void]$L.Add("  -> 警告：CPU 负载 $cpuLoad% 偏高，建议延后清理") }

# ============================== 2. 候选目录扫描 ==============================
function Get-DirStat {
    <# 流式枚举统计目录：不把文件树读进内存 #>
    param([string]$Path, [int]$SleepEvery = 3000, [int]$SleepMs = 120)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $sum = [double]0; $cnt = 0; $newest = $null
    try {
        foreach ($f in [System.IO.Directory]::EnumerateFiles($Path, '*', [System.IO.SearchOption]::AllDirectories)) {
            try {
                $fi = [System.IO.FileInfo]::new($f)
                $sum += $fi.Length
                $cnt++
                if ($null -eq $newest -or $fi.LastWriteTime -gt $newest) { $newest = $fi.LastWriteTime }
            } catch {}
            if ($SleepEvery -gt 0 -and ($cnt % $SleepEvery) -eq 0) { Start-Sleep -Milliseconds $SleepMs }
        }
    } catch {}
    return @{ Size = $sum; Count = $cnt; Newest = $newest }
}

function Write-Entry {
    param(
        [string]$Tag,
        [string]$Path,
        [string]$Note,
        [hashtable]$Stat
    )
    $labels = Get-ChildItem -LiteralPath $Path -Directory -Force -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
    $subs = ($labels -join ',')
    if ($subs.Length -gt 300) { $subs = $subs.Substring(0, 300) + '...' }
    if ($null -eq $Stat.Newest) { $dstr = 'n/a' } else { $dstr = $Stat.Newest.ToString('yyyy-MM-dd') }
    [void]$L.Add(('{0}|{1}|{2:N2}GB|files={3}|lastmod={4}|note={5}|subs={6}' -f `
        $Tag, $Path, ($Stat.Size/1GB), $Stat.Count, $dstr, $Note, $subs))
}

[void]$L.Add('')
[void]$L.Add('===== [CANDIDATES] =====')

$userHome = $env:USERPROFILE
$targets = @(
    @{ P = "$env:LOCALAPPDATA\npm-cache";                             T = 'PKGCACHE'; N = 'npm 包缓存' }
    @{ P = "$env:LOCALAPPDATA\pnpm-cache";                            T = 'PKGCACHE'; N = 'pnpm 包缓存' }
    @{ P = "$env:LOCALAPPDATA\pip";                                   T = 'PKGCACHE'; N = 'pip 包缓存' }
    @{ P = "$env:LOCALAPPDATA\Yarn";                                  T = 'PKGCACHE'; N = 'Yarn 包缓存' }
    @{ P = "$env:LOCALAPPDATA\go-build";                              T = 'BUILDCACHE'; N = 'Go 编译缓存' }
    @{ P = "$env:APPDATA\npm";                                        T = 'GLOBALPKG'; N = '全局 npm 包（非缓存，勿删）' }
    @{ P = "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Cache"; T = 'BROWSER';   N = 'Chrome 缓存' }
    @{ P = "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Cache";T = 'BROWSER';   N = 'Edge 缓存' }
    @{ P = "$env:LOCALAPPDATA\Microsoft\Windows\INetCache";           T = 'BROWSER';   N = 'IE/WebView 缓存' }
    @{ P = "$env:TEMP";                                               T = 'TEMP';      N = '用户临时目录（务必先扫业务文件）' }
    @{ P = "C:\Windows\Temp";                                         T = 'TEMP';      N = '系统临时目录' }
    @{ P = "$env:LOCALAPPDATA\CrashDumps";                            T = 'DUMP';      N = '崩溃转储' }
    @{ P = "$env:LOCALAPPDATA\Microsoft\Windows\Explorer";            T = 'THUMB';     N = '缩略图缓存' }
    @{ P = "$env:LOCALAPPDATA\Microsoft\Terminal Server Client\Cache";T = 'CACHE';     N = '远程桌面位图缓存' }
    @{ P = "$env:LOCALAPPDATA\Microsoft\OneDrive\logs";               T = 'CLOUD';     N = 'OneDrive 日志（需管理员删）' }
    @{ P = "$env:LOCALAPPDATA\Microsoft\OneDrive\Update";             T = 'CLOUD';     N = 'OneDrive 更新包（需管理员删）' }
    @{ P = "$env:APPDATA\Tencent\QQMicroGameBox";                     T = 'SOCIAL';    N = 'QQ 小游戏盒子' }
    @{ P = "$env:APPDATA\Tencent\xwechat";                            T = 'SOCIAL';    N = '微信配置与日志（主体勿删）' }
    @{ P = "$userHome\Documents\xwechat_files";                       T = 'SOCIAL';    N = '微信聊天数据（迁移不删除）' }
    @{ P = "$env:APPDATA\Tencent\WeMeet";                             T = 'SOCIAL';    N = '腾讯会议' }
    @{ P = "$env:APPDATA\Kingsoft";                                   T = 'OFFICE';    N = 'WPS 数据' }
    @{ P = "$env:APPDATA\TRAE SOLO CN";                               T = 'AITOOL';    N = 'TRAE SOLO 数据' }
    @{ P = "$env:APPDATA\Trae CN";                                    T = 'AITOOL';    N = 'Trae 数据' }
    @{ P = "$env:APPDATA\CodeBuddy CN";                               T = 'AITOOL';    N = 'CodeBuddy 数据' }
    @{ P = "$env:APPDATA\QoderCN";                                    T = 'AITOOL';    N = 'Qoder 数据' }
    @{ P = "$env:LOCALAPPDATA\Qianwen";                               T = 'AITOOL';    N = '通义千问数据' }
    @{ P = "$env:LOCALAPPDATA\OpenAI";                                T = 'AITOOL';    N = 'OpenAI Codex 数据' }
    @{ P = "$env:LOCALAPPDATA\ms-playwright";                         T = 'AITOOL';    N = 'Playwright 浏览器内核' }
    @{ P = "$env:LOCALAPPDATA\Docker";                                T = 'DEVENV';    N = 'Docker 数据（勿手动删）' }
    @{ P = "$env:LOCALAPPDATA\wsl";                                   T = 'DEVENV';    N = 'WSL 子系统' }
    @{ P = "$env:LOCALAPPDATA\微信开发者工具";                         T = 'DEVENV';    N = '微信开发者工具（安装型，勿迁移）' }
    @{ P = "$userHome\Downloads";                                     T = 'USERDATA';  N = '下载目录（只报告不处理）' }
    @{ P = "$userHome\Documents";                                     T = 'USERDATA';  N = '文档库（只报告不处理）' }
    @{ P = "$userHome\Desktop";                                       T = 'USERDATA';  N = '桌面（只报告不处理）' }
    @{ P = "$userHome\Pictures";                                      T = 'USERDATA';  N = '图片库（只报告不处理）' }
    @{ P = "$userHome\Videos";                                        T = 'USERDATA';  N = '视频库（只报告不处理）' }
    @{ P = "C:\ProgramData\HP";                                       T = 'OEM';       N = '惠普预装残留' }
    @{ P = "C:\Windows\SoftwareDistribution\Download";                T = 'WU';        N = 'Windows 更新缓存（需管理员）' }
)

foreach ($t in $targets) {
    $s = Get-DirStat -Path $t.P
    if ($null -eq $s) { continue }
    if (($s.Size / 1MB) -lt $MinSizeMB) { continue }
    Write-Entry -Tag $t.T -Path $t.P -Note $t.N -Stat $s
    Start-Sleep -Milliseconds 60
}

# --- AppData 一级子目录全量（Deep 模式）---
if ($Deep -and $memFreeGB -ge 4) {
    [void]$L.Add('')
    [void]$L.Add('===== [LOCAL-SUBDIRS] =====')
    foreach ($c in (Get-ChildItem -LiteralPath $env:LOCALAPPDATA -Directory -Force)) {
        if ($c.Name -eq 'CodeBuddyExtension') { continue }   # 沙箱黑名单保护，勿递归
        $s = Get-DirStat -Path $c.FullName -SleepEvery 4000 -SleepMs 100
        if ($null -eq $s) { continue }
        if (($s.Size / 1MB) -lt $MinSizeMB) { continue }
        [void]$L.Add(('LOCAL|{0}|{1:N2}GB|files={2}' -f $c.FullName, ($s.Size/1GB), $s.Count))
        Start-Sleep -Milliseconds 40
    }
    [void]$L.Add('')
    [void]$L.Add('===== [ROAMING-SUBDIRS] =====')
    foreach ($c in (Get-ChildItem -LiteralPath $env:APPDATA -Directory -Force)) {
        $s = Get-DirStat -Path $c.FullName -SleepEvery 4000 -SleepMs 100
        if ($null -eq $s) { continue }
        if (($s.Size / 1MB) -lt $MinSizeMB) { continue }
        [void]$L.Add(('ROAMING|{0}|{1:N2}GB|files={2}' -f $c.FullName, ($s.Size/1GB), $s.Count))
        Start-Sleep -Milliseconds 40
    }
}

# ============================== 3. 系统级专项 ==============================
[void]$L.Add('')
[void]$L.Add('===== [SYSTEM] =====')
foreach ($pair in @(
    @{ N='WinSxS 组件存储';   P='C:\Windows\WinSxS' },
    @{ N='MSI 安装缓存';      P='C:\Windows\Installer' },
    @{ N='临时（已列）';       P='' }
)) {
    if ([string]::IsNullOrWhiteSpace($pair.P)) { continue }
    $s = Get-DirStat -Path $pair.P -SleepEvery 5000 -SleepMs 120
    if ($null -ne $s) { [void]$L.Add(('SYS|{0}|{1:N2}GB|note={2}' -f $pair.P, ($s.Size/1GB), $pair.N)) }
}
$h = Get-Item -LiteralPath 'C:\hiberfil.sys' -Force -ErrorAction SilentlyContinue
if ($h) { [void]$L.Add(('SYS|C:\hiberfil.sys|{0:N2}GB|note=休眠文件，需管理员 powercfg -h off' -f ($h.Length/1GB))) }
else    { [void]$L.Add('SYS|C:\hiberfil.sys|NOT_PRESENT|note=休眠已关闭') }
$p = Get-Item -LiteralPath 'C:\pagefile.sys' -Force -ErrorAction SilentlyContinue
if ($p) { [void]$L.Add(('SYS|C:\pagefile.sys|{0:N2}GB|note=虚拟内存，默认不动' -f ($p.Length/1GB))) }

# ============================== 4. 回收站 ==============================
[void]$L.Add('')
[void]$L.Add('===== [RECYCLEBIN] =====')
$rbAll = Get-ChildItem "C:\`$Recycle.Bin" -Recurse -File -Force
$rbSize = ($rbAll | Measure-Object -Property Length -Sum).Sum
if ($null -eq $rbSize) { $rbSize = 0 }
[void]$L.Add(('total={0:N0}MB count={1}' -f ($rbSize/1MB), $rbAll.Count))
foreach ($g in ($rbAll | Group-Object { $_.CreationTime.ToString('yyyy-MM-dd') } | Sort-Object Name | Select-Object -Last 12)) {
    [void]$L.Add(('date={0} count={1} {2:N0}MB' -f $g.Name, $g.Count, (($g.Group | Measure-Object Length -Sum).Sum/1MB)))
}

# ============================== 5. Temp 业务文件预警 ==============================
[void]$L.Add('')
[void]$L.Add('===== [TEMP-WARNING] 疑似用户业务文件，删 Temp 前必须人工过目 =====')
$hits = Get-ChildItem -LiteralPath $env:TEMP -Recurse -File -Force |
    Where-Object { $_.Extension -match '\.(rar|zip|7z|docx?|xlsx?|pptx?|pdf|dwg|psd)$' -or $_.Name -match '[\u4e00-\u9fa5]' } |
    Sort-Object Length -Descending | Select-Object -First 30
if ($hits) {
    foreach ($f in $hits) {
        [void]$L.Add(('{0:N1}MB|{1:yyyy-MM-dd}|{2}' -f ($f.Length/1MB), $f.LastWriteTime, $f.FullName.Replace("$env:TEMP\",'')))
    }
} else {
    [void]$L.Add('NONE')
}

# ============================== 输出 ==============================
Set-Content -LiteralPath $OutFile -Value $L -Encoding UTF8
Write-Output "SCAN_DONE -> $OutFile"
