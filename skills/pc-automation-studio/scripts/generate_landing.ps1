<#
.SYNOPSIS
  落地模板生成器（pc-automation-studio 第二阶段）
.DESCRIPTION
  针对第一阶段诊断选中的一项自动化机会，按所选"交付形态"生成三件套：
    1) 方案卡.md      —— 可确认的实施方案模板（交付形态/规格/落盘位置/风险）
    2) 确认清单.md    —— 执行前确认闸门清单（动文件逐项目二次确认）
    3) 执行骨架       —— 随交付形态选模板：run.ps1 / run.py / tool.html / task-config.json / SKILL.md / rpa-steps.md
  本脚本只"生成文件"，不执行任何改动命令、不读写用户业务文件、不动既有文件。

  【机械闸门 v1.0.1】默认 dry-run（预览模式）：
    - 不带 -Execute 时，脚本只在控制台预览"将生成什么、写到哪个目录、是否涉及移动/删除"，
      **不创建目录、不写任何文件、零副作用**。
    - 只有显式加 -Execute，才真正在磁盘上落地生成上述三件套。
    这样即使用户催促"直接落地/别预览了快做"，未带 -Execute 也绝不会动盘，确认闸门由脚本机械保证。
    注意：涉及移动/删除/归档时，即使用 -Execute 生成了模板，真正的动文件动作仍须按确认清单逐项目二次确认，
    且一律走系统回收站，禁止 rm -rf / del /S /Q / shutil.rmtree 等不可逆删除。
.PARAMETER Name      机会名称（如"日报合并"）
.PARAMETER Delivery  交付形态：powershell | python | html | scheduled | skill | rpa
.PARAMETER Spec      一句话方案说明
.PARAMETER Files     将新建的文件名列表（逗号分隔，可选）
.PARAMETER OutDir    落地包输出目录（默认当前目录下的"落地包_<Name>"）
.PARAMETER MoveDelete 开关：若涉及移动/删除/归档，标记高风险二次确认
.PARAMETER Execute   开关：显式指定才真正写盘；缺省为 dry-run（只预览、不写盘、不建目录）
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Name,
  [ValidateSet("powershell", "python", "html", "scheduled", "skill", "rpa")][string]$Delivery = "powershell",
  [string]$Spec = "",
  [string]$Files = "",
  [string]$OutDir = "",
  [switch]$MoveDelete,
  [switch]$Execute
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not $OutDir) { $OutDir = Join-Path (Get-Location) "落地包_$Name" }

# 统一以 UTF-8(BOM) 写文件，避免中文乱码
function Write-File([string]$path, [string]$text) {
  [System.IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($true))
}

$riskLine = if ($MoveDelete) {
  "⚠ 涉及移动/删除/归档：执行前必须逐文件二次确认，并走系统回收站（绝不用 rm -rf / del /S /Q / shutil.rmtree）。"
} else {
  "默认只读/新建，不动既有文件。"
}

$fileList = if ($Files) {
  ($Files -split "," | ForEach-Object { "- $($_.Trim())" }) -join "`n"
} else {
  "- <待填>"
}

# 1) 方案卡
$plan = @"
# 落地方案卡：$Name

- 交付形态：$Delivery
- 生成日期：$stamp
- 一句话目标：$Spec

## 1. 交付形态
$Delivery

## 2. 方案规格
- 输入：<待填>
- 输出：<待填>
- 核心逻辑草图：<待填>
- 边界与异常处理：<待填>
- 隐私立场：本地/离线/不传外网

## 3. 文件清单与落盘位置
$fileList

## 4. 工作量与风险
$riskLine

## 5. 确认状态
- [ ] 待用户确认
"@

# 2) 确认清单
$confirm = @"
# 确认清单：$Name

执行前请逐项确认（任一项未确认不得进入执行）：

- [ ] 交付形态（$Delivery）对不对？
- [ ] 落盘位置对不对？
- [ ] 输入/输出理解对不对？
$(if ($MoveDelete) { "- [ ] 受影响文件清单（逐个二次确认、走回收站）：`n  - <文件1>`n  - <文件2>" } else { "- [ ] 是否涉及移动/删除：否" })
- [ ] 依赖与运行方式清楚（如何跑、如何停、如何回滚）？
"@

# 3) 执行骨架（按交付形态选型；先取内容，不直接写盘）
$skName = ""
$skText = ""
switch ($Delivery) {
  "powershell" {
    $skName = "run.ps1"
    $skText = @'
<# run.ps1 —— 执行骨架（TODO 由你填充）#>
[CmdletBinding()] param()
$ErrorActionPreference = "Stop"
# 1) 读取输入
# 2) 核心逻辑
# 3) 写出输出
Write-Host "TODO: 实现该机会的执行逻辑"
'@
  }
  "python" {
    $skName = "run.py"
    $skText = @'
# run.py —— 执行骨架（TODO 由你填充）
# 依赖装进受管 venv：python -m venv .venv && .venv\Scripts\pip install <pkg>
def main():
    # 1) 读取输入
    # 2) 核心逻辑
    # 3) 写出输出
    print("TODO: 实现该机会的执行逻辑")
if __name__ == "__main__":
    main()
'@
  }
  "html" {
    $skName = "tool.html"
    $skText = @'
<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>工具：该机会</title></head><body>
<h1>该机会</h1>
<p>单文件离线工具骨架（纯本地、不联网）。TODO: 实现交互与逻辑。</p>
<script>
// TODO: 核心逻辑
</script>
</body></html>
'@
  }
  "scheduled" {
    $skName = "task-config.json"
    $skText = [ordered]@{
      name  = "自动化_$Name"
      scheduleType = "recurring"
      rrule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
      prompt = "运行该机会的落地脚本（run.py/run.ps1），仅本地、不联网。"
    } | ConvertTo-Json -Depth 3
  }
  "skill" {
    $skName = "SKILL.md"
    $skText = @'
---
name: <skill-name>
description: <一句话触发词与用途>
agent_created: true
version: 1.0.0
display_name: <展示名>
display_name_en: <Display Name>
description_zh: |
  <中文说明>
description_en: |
  <English description>
---
# <技能名>
TODO: 写正文与步骤。
'@
  }
  "rpa" {
    $skName = "rpa-steps.md"
    $skText = @'
# RPA 步骤骨架（交给 playwright-cli / agent-browser 执行）
1. 打开目标页面/应用
2. 登录（如需要，凭据由用户自管，不写入脚本）
3. 导航到目标功能
4. 执行重复操作（循环处理每项）
5. 校验结果并保存
TODO: 按该机会填具体步骤与选择器。
'@
  }
}

# ===== 机械闸门：默认 dry-run，未显式 -Execute 一律不写盘 =====
if (-not $Execute) {
  Write-Host "【DRY-RUN 预览】未加 -Execute：以下为将要生成的内容，未创建目录、未写入任何文件。"
  Write-Host "  机会名称  : $Name"
  Write-Host "  交付形态  : $Delivery"
  Write-Host "  目标目录  : $OutDir"
  Write-Host "  将生成文件: 方案卡.md / 确认清单.md / $skName"
  Write-Host "  移动/删除 : $(if ($MoveDelete) { '是 —— 真正动文件时须逐项目二次确认并走回收站' } else { '否（默认只读/新建）' })"
  Write-Host "  用户明确确认后，重跑本命令并追加 -Execute 才会真正写盘。"
  exit 0
}

# ===== 显式 -Execute：真正落地写盘 =====
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-File -path (Join-Path $OutDir "方案卡.md") -text $plan
Write-File -path (Join-Path $OutDir "确认清单.md") -text $confirm
Write-File -path (Join-Path $OutDir $skName) -text $skText

Write-Host "✅ 落地包已生成：$OutDir"
Write-Host "   方案卡.md / 确认清单.md / $skName 已写入（仅模板；涉及移动/删除时仍须按确认清单逐项二次确认、走回收站）。"
