<#
.SYNOPSIS
    安全只读的电脑使用盘点脚本（pc-automation-studio 配套，精准信号增强版）。

.DESCRIPTION
    盘点常驻进程（带疑似工作类别）、常用目录顶层结构、批量序号文件信号、文档格式混放信号、
    最近文件（含星期周期性）、计划任务、PATH 工具、效率工具缺口。
    仅读取元信息（名称/大小/修改时间/扩展名），不打开文件内容，不修改、不删除任何文件。
    默认只扫描各目录"一层"，不递归深层个人目录。

.PARAMETER ScanPaths
    要盘点的目录列表（可选）。默认：桌面、下载、文档、脚本所在目录。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File lightweight_inventory.ps1
    powershell -ExecutionPolicy Bypass -File lightweight_inventory.ps1 -ScanPaths "$env:USERPROFILE\Downloads","$env:USERPROFILE\Desktop"
#>
[CmdletBinding()]
param(
    [string[]]$ScanPaths = @()
)

$ErrorActionPreference = 'SilentlyContinue'

function Section($title) {
    Write-Output ""
    Write-Output ("=" * 60)
    Write-Output $title
    Write-Output ("=" * 60)
}

# 默认扫描位置（仅一层）。用 UserProfile 拼接，避免旧版 .NET 不认 'Downloads' 等 SpecialFolder 枚举名导致整组路径解析失败。
if ($ScanPaths.Count -eq 0) {
    $userRoot = [System.Environment]::GetFolderPath('UserProfile')
    if (-not $userRoot) { $userRoot = $env:USERPROFILE }
    $candidates = @(
        (Join-Path $userRoot 'Desktop'),
        (Join-Path $userRoot 'Downloads'),
        (Join-Path $userRoot 'Documents'),
        $PSScriptRoot
    )
    $ScanPaths = $candidates | Where-Object { $_ -and (Test-Path $_) }
}

# 进程 -> 疑似工作类别映射（用于推断用户常做哪类活）
$procMap = @{
    'EXCEL'='办公表格'; 'WINWORD'='办公文档'; 'WPS'='办公套件'; 'ET'='办公表格'; 'KSOFFICEPRO'='办公套件';
    'chrome'='浏览器/网页'; 'msedge'='浏览器/网页'; 'firefox'='浏览器/网页'; 'iexplore'='浏览器/网页';
    'code'='开发'; 'devenv'='开发'; 'pycharm'='开发'; 'androidstudio'='开发'; 'webstorm'='开发';
    'node'='开发/前端'; 'javaw'='开发'; 'java'='开发';
    'photoshop'='图像编辑'; 'illustrator'='图像编辑'; 'lightroom'='图像编辑';
    'premiere'='视频编辑'; 'aftereffects'='视频编辑'; 'vegas'='视频编辑';
    'wechat'='即时沟通'; 'qq'='即时沟通'; 'dingtalk'='即时沟通'; 'feishu'='即时沟通'; 'wecom'='即时沟通';
    'outlook'='邮件'; 'foxmail'='邮件'; 'thunderbird'='邮件';
    'powerpnt'='演示'; 'onenote'='笔记'; 'notion'='笔记'
}

Section "1. 常驻进程 / 常用软件（Top 20 按内存）+ 疑似工作类别"
$procs = Get-Process |
    Where-Object { $_.WorkingSet -gt 0 } |
    Sort-Object WorkingSet -Descending |
    Select-Object -First 20
foreach ($p in $procs) {
    $cat = ''
    if ($procMap.ContainsKey($p.Name)) { $cat = '  -> ' + $procMap[$p.Name] }
    Write-Output ("  {0,-18} {1,8} MB{2}" -f $p.Name, [math]::Round($p.WorkingSet / 1MB), $cat)
}

Section "2. 目录顶层结构与精准信号（仅一层，只读）"
foreach ($p in $ScanPaths) {
    Write-Output "`n目录: $p"
    if (-not (Test-Path $p)) { Write-Output "  (不存在，跳过)"; continue }
    $items = Get-ChildItem -Path $p -Depth 0
    $files = $items | Where-Object { -not $_.PSIsContainer }
    $dirs  = $items | Where-Object { $_.PSIsContainer }
    Write-Output ("  子文件夹: {0}  文件: {1}" -f $dirs.Count, $files.Count)

    if ($files.Count -gt 0) {
        Write-Output "  文件类型分布 (扩展名:数量):"
        $files | Group-Object Extension | Sort-Object Count -Descending |
            Select-Object -First 15 |
            ForEach-Object {
                $ext = if ($_.Name) { $_.Name } else { '(无)' }
                Write-Output ("    {0,-12} {1}" -f $ext, $_.Count)
            }

        # 精准信号 A：批量序号文件（疑似需批量重命名/归档）
        $prefixCount = @{}
        foreach ($f in $files) {
            $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
            if ($base -match '^(.*?)(\d+)$') {
                $pre = $Matches[1]
                if ($prefixCount.ContainsKey($pre)) { $prefixCount[$pre] = $prefixCount[$pre] + 1 }
                else { $prefixCount[$pre] = 1 }
            }
        }
        $batch = $prefixCount.GetEnumerator() | Where-Object { $_.Value -ge 3 } |
            Sort-Object Value -Descending | Select-Object -First 5
        if ($batch) {
            Write-Output "  [信号A-批量序号文件] 疑似需批量重命名/归档:"
            $batch | ForEach-Object {
                $label = if ($_.Key) { $_.Key } else { '(纯数字名)' }
                Write-Output ("    {0}* : {1} 个" -f $label, $_.Value)
            }
        }

        # 精准信号 B：文档格式混放（疑似需统一/转换）
        $docExts = @('.docx', '.pdf', '.xlsx', '.csv', '.pptx', '.doc', '.xls')
        $present = @()
        foreach ($d in $docExts) {
            $c = ($files | Where-Object { $_.Extension -eq $d }).Count
            if ($c -ge 2) { $present += "$d($c)" }
        }
        if ($present.Count -ge 2) {
            Write-Output ("  [信号B-文档格式混放] 疑似需统一/转换: " + ($present -join ', '))
        }
    }
}

Section "3. 最近使用过的文件（Recent，Top 25）"
$recent = Join-Path $env:APPDATA 'Microsoft\Windows\Recent'
if (Test-Path $recent) {
    Get-ChildItem $recent -Depth 0 |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 25 |
        ForEach-Object { Write-Output ("  {0,-42} {1:yyyy-MM-dd}" -f $_.Name, $_.LastWriteTime) }
} else { Write-Output "  (无 Recent 目录)" }

Section "4. 计划任务（已就绪/启用，Top 25）"
try {
    Get-ScheduledTask |
        Where-Object { $_.State -eq 'Ready' } |
        Select-Object -First 25 TaskName, TaskPath |
        ForEach-Object { Write-Output ("  {0}{1}" -f $_.TaskPath, $_.TaskName) }
} catch { Write-Output "  (读取计划任务需要权限，跳过)" }

Section "5. 常见 CLI / 开发工具是否可用"
$tools = @('python', 'python3', 'node', 'git', 'ffmpeg', 'pandoc', '7z', 'curl', 'pwsh', 'code', 'npm', 'pip')
foreach ($t in $tools) {
    $found = Get-Command $t -ErrorAction SilentlyContinue
    $mark = if ($found) { 'YES' } else { 'no ' }
    Write-Output ("  [{0}] {1}" -f $mark, $t)
}

Section "6. 最近文件周期性（星期分布，近 40 个）"
if (Test-Path $recent) {
    $rf = Get-ChildItem $recent -Depth 0 |
        Sort-Object LastWriteTime -Descending | Select-Object -First 40
    $rf | Group-Object { $_.LastWriteTime.DayOfWeek } |
        Sort-Object Count -Descending |
        ForEach-Object { Write-Output ("  {0,-10} {1}" -f $_.Name, $_.Count) }
} else { Write-Output "  (无 Recent 目录)" }

Section "7. 效率工具缺口（未装/未运行 = 潜在自动化机会）"
$eff = @('Ditto', 'Everything', 'PowerToys', 'AutoHotkey', 'Listary', 'QuickLook', 'Snipaste')
foreach ($e in $eff) {
    $running = Get-Process -Name $e -ErrorAction SilentlyContinue
    $mark = if ($running) { '运行中' } else { '未装' }
    Write-Output ("  [{0}] {1}" -f $mark, $e)
}

Section "完成"
Write-Output "本脚本仅做只读盘点，未修改或删除任何文件。把上面信号交给 本平台，按 pc-automation-studio 流程产出精准建议与落地方案。"
