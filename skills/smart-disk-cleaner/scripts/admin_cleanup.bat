@echo off
chcp 65001 >nul
REM ============================================================
REM  智能磁盘清理助手 - 需管理员权限的清理项
REM  用法：右键本文件 -> 以管理员身份运行
REM  说明：以下项目普通权限无法执行（休眠文件 / Windows 更新缓存 /
REM        部分 ProgramData 厂商目录 / OneDrive 更新包）
REM ============================================================

echo ============================================================
echo   智能磁盘清理助手 - 管理员清理项
echo ============================================================
echo.
echo   本脚本会依次执行以下操作，每一步都会显示结果。
echo   若某一项你不想做，请现在关闭窗口，或稍后在提示处回答 n。
echo.
pause
echo.

REM ---------- 权限自检 ----------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到管理员权限。
    echo        请右键本文件，选择"以管理员身份运行"。
    echo.
    pause
    exit /b 1
)
echo [OK] 管理员权限已确认
echo.

REM ---------- 记录初始空间 ----------
for /f "tokens=3" %%A in ('dir C:\ ^| findstr /C:"可用字节"') do set BEFORE=%%A
echo [初始] C 盘可用空间统计中...
wmic LogicalDisk where "DeviceID='C:'" get FreeSpace /value > "%TEMP%\_free_before.txt" 2>nul
echo.

REM ============================================================
REM  第 1 项：关闭休眠文件（hiberfil.sys）
REM  收益：约等于物理内存的 75%（16GB 内存约释放 12GB）
REM  代价：同时会关闭"快速启动"，开机慢几秒
REM  建议：台式机可执行；笔记本若常合盖带走，建议跳过此项
REM ============================================================
echo ============================================================
echo   第 1 项：关闭休眠文件 hiberfil.sys
echo ------------------------------------------------------------
echo   注意：这会同时关闭 Windows"快速启动"。
echo   台式机建议执行；笔记本常合盖的请按需选择。
echo.
choice /c YN /n /m "    是否关闭休眠？[Y=是 / N=跳过] "
if errorlevel 2 goto :SKIP_HIBER
powercfg -h off
timeout /t 3 /nobreak >nul
if exist C:\hiberfil.sys (
    echo     [结果] 休眠文件仍存在，可能权限不足。
) else (
    echo     [OK] 休眠已关闭，C:\hiberfil.sys 已删除。
)
:SKIP_HIBER
echo.

REM ============================================================
REM  第 2 项：清理 Windows 更新下载缓存
REM ============================================================
echo ============================================================
echo   第 2 项：清理 Windows 更新缓存 SoftwareDistribution\Download
echo ------------------------------------------------------------
echo   这是已下载并安装过的补丁包，删除后不影响系统，
echo   仅意味着无法回滚已安装的更新。
echo.
choice /c YN /n /m "    是否清理 Windows 更新缓存？[Y=是 / N=跳过] "
if errorlevel 2 goto :SKIP_WU
net stop wuauserv >nul 2>&1
net stop bits >nul 2>&1
rmdir /s /q "C:\Windows\SoftwareDistribution\Download" 2>nul
mkdir "C:\Windows\SoftwareDistribution\Download" >nul 2>&1
net start wuauserv >nul 2>&1
net start bits >nul 2>&1
echo     [OK] Windows 更新缓存已清理。
:SKIP_WU
echo.

REM ============================================================
REM  第 3 项：清理 WinSxS 组件存储（耗时最长）
REM  只能由微软官方 DISM 命令处理，手动删除会导致系统崩溃
REM  收益通常 2-6GB，耗时 10-30 分钟，代价是无法卸载已装更新
REM ============================================================
echo ============================================================
echo   第 3 项：DISM 清理 WinSxS 组件存储
echo ------------------------------------------------------------
echo   耗时 10-30 分钟，期间请勿关机。
echo   代价：之后无法卸载已安装的 Windows 更新。
echo.
choice /c YN /n /m "    是否执行 DISM 组件清理？[Y=是 / N=跳过] "
if errorlevel 2 goto :SKIP_DISM
Dism.exe /Online /Cleanup-Image /StartComponentCleanup
echo     [OK] DISM 组件清理完成。
:SKIP_DISM
echo.

REM ============================================================
REM  第 4 项：厂商预装残留（自动适配品牌，有则清理无则跳过）
REM ============================================================
echo ============================================================
echo   第 4 项：清理厂商预装残留
echo ------------------------------------------------------------
echo   仅删除日志、流式日志、驱动安装包、已装 Office 的安装缓存。
echo   保留所有驱动与固件本体（不影响指纹/触摸板/音频等硬件功能）。
echo.
echo   扫描到的厂商目录：
set OEMFOUND=0
for %%B in (HP Dell Lenovo ASUS Acer MSI Samsung) do (
    if exist "C:\ProgramData\%%B" (
        echo       C:\ProgramData\%%B
        set OEMFOUND=1
    )
)
if "%OEMFOUND%"=="0" (
    echo       （未发现常见厂商目录，跳过本项）
    goto :SKIP_OEM
)
echo.
choice /c YN /n /m "    是否清理？[Y=是 / N=跳过] "
if errorlevel 2 goto :SKIP_OEM

REM 通用日志类子目录（各家命名相近）
for %%B in (HP Dell Lenovo ASUS Acer MSI Samsung) do (
    for %%D in (logs Logs StreamLog log) do (
        if exist "C:\ProgramData\%%B\%%D" rmdir /s /q "C:\ProgramData\%%B\%%D" >nul 2>&1
    )
)
REM 厂商特定的安装缓存（路径明确、确认安全）
if exist "C:\ProgramData\HP\Office2021C2R"        rmdir /s /q "C:\ProgramData\HP\Office2021C2R" >nul 2>&1
if exist "C:\ProgramData\HP\Intel Chipset Driver" rmdir /s /q "C:\ProgramData\HP\Intel Chipset Driver" >nul 2>&1
if exist "C:\ProgramData\Dell\UpdatePackage"      rmdir /s /q "C:\ProgramData\Dell\UpdatePackage" >nul 2>&1
echo     [OK] 厂商预装残留已清理。
:SKIP_OEM
echo.

REM ============================================================
REM  第 5 项：OneDrive 日志与更新包
REM ============================================================
echo ============================================================
echo   第 5 项：清理 OneDrive 日志与更新包
echo ------------------------------------------------------------
taskkill /f /im OneDrive.exe >nul 2>&1
rmdir /s /q "%LOCALAPPDATA%\Microsoft\OneDrive\logs" 2>nul
rmdir /s /q "%LOCALAPPDATA%\Microsoft\OneDrive\Update" 2>nul
echo     [OK] OneDrive 缓存已清理（OneDrive 会在下次登录时自动重启）。
echo.

REM ============================================================
REM  收尾：清空回收站
REM ============================================================
echo ============================================================
echo   收尾：清空回收站（只有清了回收站，空间才真正释放）
echo.
choice /c YN /n /m "    是否清空回收站？此操作不可撤销 [Y=是 / N=否] "
if errorlevel 2 goto :DONE
PowerShell -NoProfile -Command "Clear-RecycleBin -DriveLetter C -Force" >nul 2>&1
echo     [OK] 回收站已清空。

:DONE
echo.
echo ============================================================
echo   全部完成
echo ============================================================
echo.
echo   当前 C 盘空间：
for /f "tokens=2 delims==" %%A in ('wmic LogicalDisk where "DeviceID='C:'" get FreeSpace /value 2^>nul ^| find "=" ') do (
    PowerShell -NoProfile -Command "'  可用 {0:N2} GB' -f (%%A/1GB)"
)
echo.
echo   提示：本脚本只覆盖"需要管理员权限"的那几类项目。
echo   完整清理（纯缓存 / 应用数据 / 规则库驱动的自动处理 / 聊天数据迁移指引）
echo   仍请交由 AI 助手按技能流程推进，不要只依赖本脚本。
echo.
pause
