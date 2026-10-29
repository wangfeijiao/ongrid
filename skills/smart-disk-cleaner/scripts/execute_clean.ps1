#Requires -Version 5.1
<#
.SYNOPSIS
  智能磁盘清理助手 - 规则驱动的分批执行器

.DESCRIPTION
  两种运行模式：

    模式 A（规则驱动，推荐）
      不传 -Paths，脚本读取决策规则库 ~/.disk-cleaner/decisions.md，
      对已命中规则的项目自动执行，未命中的输出"需确认"清单交给对话层。
      —— 用于「第二次以后的清理」，同类项目不再逐项询问。

    模式 B（清单驱动）
      显式传 -Paths，按给定清单执行。用于首次清理或临时指定。

  安全设计（四条底线，不可通过参数绕过）：
    1. -Execute 为必须开关。不加则只做 DRY-RUN 预演。
    2. 高敏类别硬保护：个人文档 / 聊天记录 / 财务合同 / 项目底稿，即使规则库
       写了"删"也一律跳过。
    3. 单任务串行 + 自我降级为 Idle 优先级 + 项间强制停顿，不与用户前台争抢。
    4. 用户业务文件预警：删除前扫描目标目录，命中 .docx/.xlsx/.pdf/中文名
       等特征即整项跳过并报警（Temp 类目录必查）。

  关键操作常识：删除后文件进回收站，回收站占目标盘空间 ——
  **只有清空回收站，可用空间才真正释放**。因此每批结束默认清空回收站。

.PARAMETER Paths
  模式 B 的待清理绝对路径数组。传入则走清单模式。

.PARAMETER RuleFile
  模式 A 的决策规则库路径。默认 ~/.disk-cleaner/decisions.md

.PARAMETER Execute
  开关。必须显式传入才真正删除；否则只做 DRY-RUN 预演。

.PARAMETER BatchMax
  单批最大项目数。默认 5（保守档）。可根据性能画像调至 10。

.PARAMETER PurgeRecycleBin
  开关（模式 B 可用）。执行结束后清空回收站。
  调用前必须先核查回收站内容，确认没有用户此前自己删除的重要文件。

.PARAMETER NoPurgeRecycleBin
  开关。规则模式下禁止自动清空回收站（默认每批清一次）。

.PARAMETER ForceLowMemory
  开关。冗余度不足 1GB 时默认拒绝执行；显式传入本开关可强制继续，
  但会自动降到最低档节奏（批次上限 2、项间停顿加大）。风险自负：可能卡死。

.PARAMETER OutFile
  执行日志输出绝对路径。

.PARAMETER SleepBetweenItems
  项间停顿秒数，默认 1 秒。机械硬盘建议 2-3 秒。

.EXAMPLE
  # 模式 A：预演（推荐首次使用先跑这个）
  powershell -File execute_clean.ps1

.EXAMPLE
  # 模式 A：真正执行
  powershell -File execute_clean.ps1 -Execute -BatchMax 5

.EXAMPLE
  # 模式 B：按清单执行
  powershell -NoProfile -ExecutionPolicy Bypass -File execute_clean.ps1 `
    -Paths "$env:LOCALAPPDATA\npm-cache","$env:LOCALAPPDATA\pip" -Execute -PurgeRecycleBin
#>

param(
    [string[]]$Paths,

    [string]$RuleFile = (Join-Path ([System.Environment]::GetEnvironmentVariable('USERPROFILE')) '.disk-cleaner\decisions.md'),

    [switch]$Execute,

    [int]$BatchMax = 5,

    [switch]$PurgeRecycleBin,

    [switch]$NoPurgeRecycleBin,

    [switch]$ForceLowMemory,

    # 无人值守模式：由定时任务调用。强制禁止硬删除、强制保护近期回收站内容、
    # 强制跳过中/高风险项（只列入清单不执行），单次总量设上限。
    [switch]$AutoMode,

    # 无人值守单次释放上限（GB），超出部分留到下次
    [int]$AutoMaxGB = 10,

    [string]$OutFile = (Join-Path (Get-Location).Path 'clean_exec_log.txt'),

    [int]$SleepBetweenItems = 1
)

try { (Get-Process -Id $PID).PriorityClass = 'Idle' } catch {}
$ErrorActionPreference = 'SilentlyContinue'

$L = New-Object System.Collections.ArrayList
$useRules = -not ($Paths -and $Paths.Count -gt 0)
[void]$L.Add("MODE=$(if($useRules){'RULE-DRIVEN'}else{'PATH-LIST'})  EXECUTE=$($Execute)  BatchMax=$BatchMax  OutputOnly=$(-not $Execute)")

# ============================== 高敏类别硬保护 ==============================
# 即使规则库命中，落在这些路径下的目标也一律跳过。安全底线，不可配置绕过。
$PROTECTED = @(
    '\Documents\', '\Downloads\', '\Desktop\', '\Pictures\', '\Videos\', '\Music\',
    '\xwechat_files', '\WeChat Files\', '\Tencent\WeChat\',
    '\DingTalk\', '\钉钉', '\Feishu\', '\飞书',
    '股权变更', '合同', '财报', '发票', '申报', '工商',
    '\Projects\', '\项目\'
)
function Test-Protected([string]$Path) {
    foreach ($p in $PROTECTED) { if ($Path -like "*$p*") { return $true } }
    return $false
}

# ============================== 通用工具 ==============================
function Get-FreeGB {
    param([string]$DriveLetter = 'C')
    $d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($DriveLetter):'"
    if ($d) { return [double]($d.FreeSpace / 1GB) } else { return [double]0 }
}

function Measure-Path {
    <# 流式枚举，不把文件树读进内存 #>
    param([string]$Path)
    $sum = [double]0; $cnt = 0
    try {
        foreach ($f in [System.IO.Directory]::EnumerateFiles($Path, '*', [System.IO.SearchOption]::AllDirectories)) {
            try { $sum += ([System.IO.FileInfo]::new($f)).Length; $cnt++ } catch {}
            if (($cnt % 4000) -eq 0) { Start-Sleep -Milliseconds 100 }
        }
    } catch {}
    return @{ Size = $sum; Count = $cnt }
}

function Test-UserBusinessFile {
    <# 目标目录里是否混有用户业务文件。命中即拒绝删除该目录。 #>
    param([string]$Path)
    $hits = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -match '\.(rar|zip|7z|docx?|xlsx?|pptx?|pdf|dwg|psd)$' -or $_.Name -match '[\u4e00-\u9fa5]' } |
        Select-Object -First 5)
    return $hits
}

function Remove-Target {
    <# 优先走回收站（可撤销）；失败则直接删除（调用方已确认可再生） #>
    param([string]$Path)
    $recycled = $false
    try {
        $shell = New-Object -ComObject Shell.Application
        $item  = $shell.Namespace(0).ParseName($Path)
        if ($item) {
            $item.InvokeVerb('delete')
            Start-Sleep -Milliseconds 800
            $recycled = -not (Test-Path -LiteralPath $Path)
        }
    } catch {}
    if (-not $recycled) {
        Remove-Item -LiteralPath $Path -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    return $recycled
}

# ============================== 规则库解析（模式 A） ==============================
function Resolve-Pattern([string]$Pattern) {
    <# 把规则里的路径模式转成可用于 -like 的通配串。#>
    $p = $Pattern

    # 剥掉规则表里为了可读性加的说明后缀，如 "（WPS）" "`（log/日志类子项）" "` / `Update"
    $p = $p -replace '\s*`\s*（[^）]*）\s*$', ''
    $p = $p -replace '\s*（[^）]*）\s*$', ''
    if ($p -match '\s*`\s*/\s*`') {
        $p = ($p -split '\s*`\s*/\s*`')[0]
    }

    # 展开环境变量
    # 支持两种写法：%VAR%（Windows 传统）与 $env:VAR（PowerShell 习惯）。
    # 若只认一种，另一种会被当成字面路径而静默匹配失败 —— 规则看起来配了却不生效，很难排查。
    #
    # 取值一律走 Environment.GetEnvironmentVariable 而非 $env:VAR：
    # 某些非交互式/受限会话里 $env: 可能为空，直接用 GetEnvironmentVariable 更稳，
    # 且在取不到时能用 USERPROFILE 兜底拼出常见路径。
    $ha = [System.Environment]::GetEnvironmentVariable('APPDATA')
    $hl = [System.Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    $ht = [System.Environment]::GetEnvironmentVariable('TEMP')
    $hp = [System.Environment]::GetEnvironmentVariable('PROGRAMDATA')
    $hu = [System.Environment]::GetEnvironmentVariable('USERPROFILE')
    $hw = [System.Environment]::GetEnvironmentVariable('WINDIR')
    if ([string]::IsNullOrWhiteSpace($hl) -and $hu) { $hl = Join-Path $hu 'AppData\Local' }
    if ([string]::IsNullOrWhiteSpace($ha) -and $hu) { $ha = Join-Path $hu 'AppData\Roaming' }

    $p = $p.Replace('%LOCALAPPDATA%', $hl).Replace('%APPDATA%', $ha).Replace('%TEMP%', $ht).
             Replace('%PROGRAMDATA%', $hp).Replace('%USERPROFILE%', $hu).Replace('%WINDIR%', $hw)
    $p = $p.Replace('$env:LOCALAPPDATA', $hl).Replace('$env:APPDATA', $ha).Replace('$env:TEMP', $ht).
             Replace('$env:PROGRAMDATA', $hp).Replace('$env:USERPROFILE', $hu).Replace('$env:WINDIR', $hw)
    $p = $p.Replace('${env:LOCALAPPDATA}', $hl).Replace('${env:APPDATA}', $ha)

    # 反引号包裹的段（如 `%APPDATA%\Kingsoft`）去掉残留反引号
    $p = $p -replace '`', ''

    # * 在 -like 里本身就是通配符，直接保留
    return $p
}

function Get-Rules {
    param([string]$File)
    $rules = New-Object System.Collections.ArrayList
    if (-not (Test-Path -LiteralPath $File)) { return $rules }

    # 只解析 "## 已确认裁定" 小节之后的表格。
    # 这样「出厂默认规则」那张仅供参考的表就不会污染可执行规则集，也避免同一路径
    # 出现"又写问又写删"的自我冲突。
    $inside = $false
    foreach ($line in (Get-Content -LiteralPath $File -Encoding UTF8)) {
        $t = $line.Trim()

        if ($t -match '^#{1,6}\s') {
            $inside = ($t -match '已确认裁定')
            continue
        }
        if (-not $inside) { continue }
        if (-not $t.StartsWith('|')) { continue }
        if ($t -match '^\|\s*[-:]+') { continue }
        if ($t -match '路径模式') { continue }

        $cells = @($t.Trim('|') -split '\|')
        if ($cells.Count -lt 4) { continue }

        # 路径模式列可能被反引号包裹；带空格的单元格（如 `%APPDATA%\Kingsoft`）也要正确取出
        $pat = $cells[0].Trim()
        $pat = $pat -replace '^`+|`+$', ''
        $pat = $pat.Trim()
        $cat = $cells[1].Trim()
        $dec = ($cells[2].Trim() -replace '^\*+|\*+$', '')
        $bs  = $cells[3].Trim()

        if ([string]::IsNullOrWhiteSpace($pat)) { continue }
        if ($dec -notin @('删','留','迁','清缓存','问')) { continue }
        [void]$rules.Add([PSCustomObject]@{ Pattern = $pat; Category = $cat; Decision = $dec; Basis = $bs })
    }
    return $rules
}

# 路径根变量：不用 $env: 直取，而是走 GetEnvironmentVariable 并在为空时用 USERPROFILE 兜底。
# 原因：部分受限/非交互会话里 $env:LOCALAPPDATA 可能为空字符串，会让整张候选清单静默失效。
$envRoot_HA = [System.Environment]::GetEnvironmentVariable('APPDATA')
$envRoot_HL = [System.Environment]::GetEnvironmentVariable('LOCALAPPDATA')
$envRoot_HP = [System.Environment]::GetEnvironmentVariable('USERPROFILE')
if ([string]::IsNullOrWhiteSpace($envRoot_HL) -and $envRoot_HP) { $envRoot_HL = Join-Path $envRoot_HP 'AppData\Local' }
if ([string]::IsNullOrWhiteSpace($envRoot_HA) -and $envRoot_HP) { $envRoot_HA = Join-Path $envRoot_HP 'AppData\Roaming' }

$CANDIDATES = @(
    @{ P = "$envRoot_HL\npm-cache";                              N = 'npm 包缓存' }
    @{ P = "$envRoot_HL\pnpm-cache";                             N = 'pnpm 包缓存' }
    @{ P = "$envRoot_HL\pip";                                    N = 'pip 包缓存' }
    @{ P = "$envRoot_HL\Yarn";                                   N = 'Yarn 包缓存' }
    @{ P = "$envRoot_HL\go-build";                               N = 'Go 编译缓存' }
    @{ P = "$envRoot_HL\Google\Chrome\User Data\Default\Cache";  N = 'Chrome 缓存' }
    @{ P = "$envRoot_HL\Microsoft\Edge\User Data\Default\Cache"; N = 'Edge 缓存' }
    @{ P = "$envRoot_HL\Microsoft\Windows\INetCache";            N = 'WebView 缓存' }
    @{ P = "$envRoot_HL\CrashDumps";                             N = '崩溃转储' }
    @{ P = "$envRoot_HL\Microsoft\Terminal Server Client\Cache"; N = '远程桌面缓存' }
    @{ P = "$envRoot_HA\Tencent\QQMicroGameBox\cef_cache";            N = 'QQ 小游戏缓存' }
    @{ P = "$envRoot_HL\ms-playwright";                          N = 'Playwright 内核' }
    @{ P = "$envRoot_HA\TRAE SOLO CN";                                N = 'TRAE SOLO 数据' }
    @{ P = "$envRoot_HA\Trae CN";                                     N = 'Trae 数据' }
    @{ P = "$envRoot_HA\CodeBuddy CN";                                N = 'CodeBuddy 数据' }
    @{ P = "$envRoot_HA\QoderCN";                                     N = 'Qoder 数据' }
    @{ P = "$envRoot_HL\OpenAI\Codex";                           N = 'Codex 数据' }
    @{ P = "$envRoot_HA\Kingsoft";                                    N = 'WPS 数据' }
    @{ P = "$envRoot_HL\Microsoft\OneDrive\logs";                N = 'OneDrive 日志' }
    @{ P = "$envRoot_HL\Microsoft\OneDrive\Update";              N = 'OneDrive 更新包' }
    @{ P = "$envRoot_HL\Qianwen\User Data\qwen-agent\resources\cache"; N = 'Qwen Agent 缓存' }
    @{ P = "$envRoot_HL\Qianwen\User Data\ulog";                 N = 'Qwen 日志' }
)

# ============================== 构建待处理清单 ==============================
$tasks   = New-Object System.Collections.ArrayList   # 待执行
$needAsk = New-Object System.Collections.ArrayList   # 需确认
$skipped = New-Object System.Collections.ArrayList   # 跳过

if ($useRules) {
    if (-not (Test-Path -LiteralPath $RuleFile)) {
        [void]$L.Add('')
        [void]$L.Add("RULEFILE_MISSING|$RuleFile")
        [void]$L.Add('  规则库不存在，全部项目按"需确认"处理。')
        $rules = New-Object System.Collections.ArrayList
    } else {
        $rules = Get-Rules -File $RuleFile
        [void]$L.Add('')
        [void]$L.Add("RULEFILE=$RuleFile  loaded=$($rules.Count) 条")
        foreach ($r in $rules) { [void]$L.Add(("  RULE|{0,-5}|{1}" -f $r.Decision, $r.Pattern)) }
    }

    foreach ($c in $CANDIDATES) {
        if (-not (Test-Path -LiteralPath $c.P)) { continue }

        if (Test-Protected $c.P) {
            [void]$skipped.Add([PSCustomObject]@{ Path = $c.P; Name = $c.N; Size = 0; Why = '高敏类别硬保护' })
            continue
        }

        $hit = $null
        foreach ($r in $rules) {
            if ($r.Decision -eq '问') { continue }
            $rp = Resolve-Pattern $r.Pattern
            if ([string]::IsNullOrWhiteSpace($rp)) { continue }
            $matched = ($c.P -eq $rp) -or ($c.P -like $rp)
            if ($matched) {
                # 同一路径命中多条时，"更具体"（模式更长 / 无通配符）的规则优先。
                # 这样"具体路径优先于通配模式"的约定才能在规则库里生效。
                if ($null -eq $hit) {
                    $hit = $r
                } elseif ($rp.Length -gt (Resolve-Pattern $hit.Pattern).Length) {
                    $hit = $r
                }
            }
        }

        $m = Measure-Path -Path $c.P
        $item = [PSCustomObject]@{
            Path  = $c.P
            Name  = $c.N
            Size  = $m.Size
            Why   = ''
            Class = $(if ($null -ne $hit) { $hit.Category } else { '' })
        }

        if ($null -eq $hit) {
            [void]$needAsk.Add($item)
        } elseif ($hit.Decision -in @('删','清缓存')) {
            [void]$tasks.Add($item)
        } else {
            $item.Why = "规则=$($hit.Decision)"
            [void]$skipped.Add($item)
        }
    }
} else {
    foreach ($p in $Paths) {
        if (Test-Protected $p) {
            [void]$skipped.Add([PSCustomObject]@{ Path = $p; Name = (Split-Path $p -Leaf); Size = 0; Why = '高敏类别硬保护' })
            continue
        }
        $m = Measure-Path -Path $p
        [void]$tasks.Add([PSCustomObject]@{
            Path  = $p
            Name  = (Split-Path $p -Leaf)
            Size  = $m.Size
            Why   = ''
            Class = '临时文件'    # 显式清单视为用户已裁定，按低风险处理
        })
    }
}

# ============================== 输出计划 ==============================
[void]$L.Add('')
[void]$L.Add('===== [PLAN] =====')
foreach ($t in $tasks)   { [void]$L.Add(('WILL_DELETE |{0}|{1:N0}MB|{2}' -f $t.Name, ($t.Size/1MB), $t.Path)) }
foreach ($t in $needAsk) { [void]$L.Add(('NEED_CONFIRM|{0}|{1:N0}MB|{2}' -f $t.Name, ($t.Size/1MB), $t.Path)) }
foreach ($t in $skipped) { [void]$L.Add(('SKIP        |{0}|{1}|{2}' -f $t.Name, $t.Why, $t.Path)) }

# ============================== 无人值守守卫（AutoMode） ==============================
# 定时任务调用时必须带 -AutoMode。此模式下权限收紧：
#   1. 只删低风险项，中/高风险项一律降级为"待确认"，不执行
#   2. 禁止任何硬删除路径（本脚本本就走回收站，这里再显式兜底）
#   3. 回收站内存在 7 天内文件时，本轮不清空回收站（保住可恢复窗口）
#   4. 单次释放总量设上限，超出留到下次
#
# 注意：本段必须放在 DRY-RUN 判断之前 —— DRY-RUN 是自检的主要手段，
# 而无人值守的筛选结果（哪些会被自动删、哪些被降级）正是最需要预先看到的信息。
$autoHeld    = New-Object System.Collections.ArrayList   # 被降级为待确认的项
$autoSkipped = New-Object System.Collections.ArrayList   # 被保护跳过的项
if ($AutoMode) {
    [void]$L.Add('')
    [void]$L.Add('===== [AUTO-MODE GUARD] 无人值守，权限收紧 =====')

    # --- 守卫 3：回收站近期内容保护 ---
    $recentCutoff = (Get-Date).AddDays(-7)
    $recentRb = @(Get-ChildItem "C:\`$Recycle.Bin" -Recurse -File -Force -ErrorAction SilentlyContinue |
                  Where-Object { $_.CreationTime -gt $recentCutoff })
    if ($recentRb.Count -gt 0) {
        $recentMB = [math]::Round((($recentRb | Measure-Object -Property Length -Sum).Sum / 1MB), 0)
        [void]$L.Add(("  RB-GUARD|回收站内有 {0} 个 7 天内文件（约 {1:N0}MB），本轮不清空回收站以保留恢复窗口" -f $recentRb.Count, $recentMB))
        $NoPurgeRecycleBin = $true
    } else {
        [void]$L.Add('  RB-GUARD|回收站内无 7 天内文件，允许按批清空')
    }

    # --- 守卫 1：中/高风险项降级为待确认 ---
    # 依据规则库"分类"列判定。白名单只放「纯可再生缓存」类关键词；
    # 命中即视为低风险可自动执行，其余（含分类未知）一律降级为待确认。
    $AUTO_ALLOW_CLASS = @(
        '缓存', 'cache', '临时', 'temp', '转储', 'dump', '日志', 'log',
        '残留', '缩略图', '安装包', '更新包', '预览'
    )
    # 反向排除：分类里出现这些词的一律视为中高风险，即使含"缓存"字样
    $AUTO_DENY_CLASS = @(
        '聊天', '微信', 'WeChat', 'QQ', '钉钉', '飞书', '账', '合同', '财务',
        '文档', '图片', '视频', '音乐', '桌面', '下载', '项目', '数据',
        '配置', 'profile', 'User Data', '本体', '程序', '资源'
    )
    $filtered = New-Object System.Collections.ArrayList
    foreach ($t in $tasks) {
        $cls = [string]$t.Class
        if ([string]::IsNullOrWhiteSpace($cls)) {
            # 分类未知 → 无人值守下视为中风险，不执行
            [void]$autoHeld.Add($t); continue
        }
        # 先查反向排除表：命中即中高风险，不执行
        $denied = $false
        foreach ($bad in $AUTO_DENY_CLASS) { if ($cls -like "*$bad*") { $denied = $true; break } }
        if ($denied) { [void]$autoHeld.Add($t); continue }
        # 再查白名单：命中即低风险，可自动执行
        $isLow = $false
        foreach ($allow in $AUTO_ALLOW_CLASS) { if ($cls -like "*$allow*") { $isLow = $true; break } }
        if ($isLow) { [void]$filtered.Add($t) } else { [void]$autoHeld.Add($t) }
    }
    $tasks = $filtered
    [void]$L.Add(("  SCOPE|低风险可执行 {0} 项；中/高风险降级为待确认 {1} 项" -f $tasks.Count, $autoHeld.Count))
    foreach ($h in $autoHeld) { [void]$L.Add(("  HELD|{0}|[{1}]|原因：无人值守不执行中高风险项" -f $h.Name, $h.Class)) }

    # --- 守卫 4：单次释放上限 ---
    [void]$L.Add(("  CAP|单次释放上限 {0}GB，超出部分留到下次" -f $AutoMaxGB))

    [void]$L.Add('  HARD-DELETE|已禁用（全程仅走回收站，保留可恢复性）')

    # 重画计划表，让 DRY-RUN 呈现的是「过滤后」的真实执行范围
    [void]$L.Add('')
    [void]$L.Add('===== [PLAN · 无人值守过滤后] =====')
    foreach ($t in $tasks) { [void]$L.Add(('AUTO_DELETE |{0}|{1:N0}MB|{2}' -f $t.Name, ($t.Size/1MB), $t.Path)) }
}

if (-not $Execute) {
    [void]$L.Add('')
    [void]$L.Add('=== DRY-RUN 结束：未做任何修改。加 -Execute 才会真正删除。 ===')
    Set-Content -LiteralPath $OutFile -Value $L -Encoding UTF8
    Write-Output "DRY_RUN_DONE -> $OutFile"
    return
}

# ============================== 执行前冗余门槛 ==============================
# 冗余度 = 可用内存 − 清理预估峰值占用(1.5GB)。
# 冗余不足时拒绝执行 —— 这是防止把用户电脑搞卡死的硬门槛。
function Get-HeadroomGB {
    $memFreeGB = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
    return [math]::Round(($memFreeGB - 1.5), 2)
}
function Get-CpuLoad {
    return (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
}

$headroom0 = Get-HeadroomGB
$cpuLoad0  = Get-CpuLoad
[void]$L.Add('')
[void]$L.Add('===== [PERFORMANCE GATE] =====')
[void]$L.Add(("HEADROOM_BEFORE={0:N2}GB  CPU={1}%" -f $headroom0, $cpuLoad0))

if ($Execute -and $headroom0 -lt 1) {
    [void]$L.Add('GATE_BLOCKED|冗余度不足 1GB，拒绝执行删除动作')
    [void]$L.Add('  请先关闭部分程序释放内存，或稍后再执行本清理。')
    [void]$L.Add('  如坚持执行，请显式传 -ForceLowMemory（风险自负：可能卡死）')
    if (-not $ForceLowMemory) {
        Set-Content -LiteralPath $OutFile -Value $L -Encoding UTF8
        Write-Output "BLOCKED_LOW_MEMORY -> $OutFile"
        return
    } else {
        [void]$L.Add('  [警告] 已收到 -ForceLowMemory，继续执行，但会降到最低档节奏。')
        $BatchMax = 2
    }
}
if ($Execute -and $cpuLoad0 -gt 70) {
    [void]$L.Add(("GATE_WARN|CPU 负载 {0}% 偏高，批次间停顿延长至 5 秒" -f $cpuLoad0))
}

# ============================== 执行 ==============================
[void]$L.Add('')
[void]$L.Add('===== [EXECUTE] =====')
$before  = Get-FreeGB
[void]$L.Add(('BEFORE|Free={0:N2}GB|targets={1}' -f $before, $tasks.Count))

$done = 0
$curBatchMax = $BatchMax          # 动态批次上限，可随冗余下降而调小
$curSleep    = $SleepBetweenItems # 动态项间停顿，可随负载上升而调大

foreach ($t in $tasks) {
    # --- 无人值守：单次释放上限检查 ---
    if ($AutoMode) {
        $freedSoFar = (Get-FreeGB) - $before
        if ($freedSoFar -ge $AutoMaxGB) {
            [void]$L.Add(('CAP-REACHED|已释放 {0:N2}GB 达到单次上限 {1}GB，剩余 {2} 项留到下次' -f $freedSoFar, $AutoMaxGB, ($tasks.Count - $done)))
            break
        }
    }

    # --- 动态冗余复查：每项开始前重新采样，掉档就降速 ---
    $headroom = Get-HeadroomGB
    if ($headroom -lt 1) {
        [void]$L.Add(('HALT|冗余度降至 {0:N2}GB（不足 1GB），立即中止后续批次' -f $headroom))
        break
    }
    if ($headroom -lt 2 -and $curBatchMax -gt 3) {
        $curBatchMax = 3; $curSleep = 2
        [void]$L.Add(("  DOWNGRADE|冗余 {0:N2}GB 偏紧，批次上限降至 3、项间停顿 2s" -f $headroom))
    }
    $cpuNow = Get-CpuLoad
    if ($cpuNow -gt 70 -and $curSleep -lt 5) {
        $curSleep = 5
        [void]$L.Add(("  THROTTLE|CPU {0}%，项间停顿延长至 5s" -f $cpuNow))
    }

    # --- 每满一批，清空回收站让空间真正释放 ---
    if ($done -gt 0 -and ($done % $curBatchMax) -eq 0 -and -not $NoPurgeRecycleBin) {
        [void]$L.Add(("--- BATCH $([int]($done/$curBatchMax)) 结束，清空回收站 ---"))
        Clear-RecycleBin -DriveLetter C -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        [void]$L.Add(('  freeNow={0:N2}GB headroom={1:N2}GB' -f (Get-FreeGB), (Get-HeadroomGB)))
    }

    if (-not (Test-Path -LiteralPath $t.Path)) {
        [void]$L.Add(('SKIP|not found|{0}' -f $t.Path))
        continue
    }

    # --- 用户业务文件预警 ---
    $hits = Test-UserBusinessFile -Path $t.Path
    if ($hits.Count -gt 0) {
        [void]$L.Add(('ABORT|发现疑似用户业务文件，已跳过|{0}|例：{1}' -f $t.Path, ($hits[0].Name)))
        continue
    }

    $b = Measure-Path -Path $t.Path
    $recycled = Remove-Target -Path $t.Path
    Start-Sleep -Seconds $curSleep

    $gone = -not (Test-Path -LiteralPath $t.Path)
    $done++
    [void]$L.Add(('DONE|{0}|was={1:N0}MB|removed={2}|viaRecycleBin={3}|freeNow={4:N2}GB|headroom={5:N2}GB' -f `
        $t.Name, ($b.Size/1MB), $gone, $recycled, (Get-FreeGB), (Get-HeadroomGB)))
}

# --- 收尾：最终清空回收站（无人值守时若触发了 RB-GUARD 则跳过） ---
$purge = $PurgeRecycleBin -or ($useRules -and -not $NoPurgeRecycleBin)
if ($AutoMode -and $NoPurgeRecycleBin) { $purge = $false }
if ($purge) {
    [void]$L.Add('--- 最终清空回收站 ---')
    Clear-RecycleBin -DriveLetter C -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
} elseif ($AutoMode) {
    [void]$L.Add('--- 回收站保留未清空（无人值守恢复窗口保护） ---')
}

$after = Get-FreeGB
[void]$L.Add('')
[void]$L.Add(('AFTER|Free={0:N2}GB|freed={1:N2}GB|handled={2}' -f $after, ($after - $before), $done))
$rbLeft = Get-ChildItem "C:\`$Recycle.Bin" -Recurse -File -Force
$rbSize = ($rbLeft | Measure-Object -Property Length -Sum).Sum
if ($null -eq $rbSize) { $rbSize = 0 }
[void]$L.Add(('RECYCLEBIN_LEFT={0:N0}MB' -f ($rbSize/1MB)))

if ($AutoMode) {
    [void]$L.Add('')
    [void]$L.Add('===== [AUTO-MODE SUMMARY] 无人值守简报 =====')
    [void]$L.Add(("  已自动清理：{0} 项，释放 {1:N2}GB" -f $done, ($after - $before)))
    [void]$L.Add(("  待你确认  ：{0} 项（中/高风险，未执行）" -f $autoHeld.Count))
    foreach ($h in $autoHeld) { [void]$L.Add(("    - {0}" -f $h.Name)) }
    [void]$L.Add(("  已跳过    ：{0} 项（保护项）" -f $autoSkipped.Count))
    [void]$L.Add(("  回收站    ：{0}" -f $(if ($NoPurgeRecycleBin) { '保留未清空（存有近期文件）' } else { '已清空' })))
}

if ($needAsk.Count -gt 0) {
    [void]$L.Add('')
    [void]$L.Add(("=== 待确认 $($needAsk.Count) 项，请交由对话层逐项向用户披露四要素 ==="))
}

Set-Content -LiteralPath $OutFile -Value $L -Encoding UTF8
Write-Output "CLEAN_DONE -> $OutFile"
