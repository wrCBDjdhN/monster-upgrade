# 打怪升级 - 一键构建脚本
# 用法：powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# 前置条件：
#   - Python 3.14（py -3.14 可用）且已安装 PyInstaller、arcade
#   - 已安装 Inno Setup 6（含 ISCC.exe）
# 产出：
#   dist\MonsterUpgrade\MonsterUpgrade.exe  游戏本体（PyInstaller onedir，无控制台窗口）
#   dist\installer\Setup_MonsterUpgrade.exe 最终安装包（Inno Setup）

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot  # 项目根目录（packaging/ 的上一级）
Set-Location $Root

Write-Host '== [1/2] PyInstaller 打包 MonsterUpgrade.exe =='
py -3.14 -m PyInstaller packaging\MonsterUpgrade.spec --distpath dist --workpath build --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }

Write-Host '== [2/2] Inno Setup 编译安装包 =='
$iscc = Get-ChildItem `
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", `
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe', `
    'C:\Program Files\Inno Setup 6\ISCC.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $iscc) { throw '未找到 ISCC.exe，请先安装 Inno Setup 6' }
& $iscc.FullName packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw '安装包编译失败' }

Write-Host ''
Write-Host '构建完成：'
Write-Host "  游戏 exe：$Root\dist\MonsterUpgrade\MonsterUpgrade.exe"
Write-Host "  安装包　：$Root\dist\installer\Setup_MonsterUpgrade.exe"