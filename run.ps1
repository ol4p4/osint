<# 
.SYNOPSIS
    OSINT 个人智库系统 - 本地一键运行脚本
.DESCRIPTION
    双击运行：加载云端情报 -> AI深度分析 -> 生成简报/仪表盘/入库
.NOTES
    需要先运行 install_deps.ps1 安装依赖
    配置 config.yaml 中的 API Key
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectDir = Join-Path $scriptDir ".."
$localDir = Join-Path $projectDir "local"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "🏛️  OSINT 个人智库系统 - 本地参谋长" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "项目目录: $projectDir" -ForegroundColor Gray
Write-Host "本地模块: $localDir" -ForegroundColor Gray
Write-Host ""

# 检查 Python
$pythonCmd = "python"
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    $pythonCmd = "python3"
}
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    Write-Host "❌ 未找到 Python，请先运行 install_deps.ps1" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

$pyVersion = & $pythonCmd --version 2>&1
Write-Host "✅ Python: $pyVersion" -ForegroundColor Green

# 检查配置文件
$configFile = Join-Path $projectDir "config.yaml"
if (-not (Test-Path $configFile)) {
    Write-Host "❌ 缺少配置文件: $configFile" -ForegroundColor Red
    Write-Host "请复制 config.yaml.example 为 config.yaml 并填入 API Key" -ForegroundColor Yellow
    Read-Host "按回车键退出"
    exit 1
}

# 检查 API Key
$configContent = Get-Content $configFile -Raw
if ($configContent -match 'api_key:\s*["\']?\s*["\']?\s*$') {
    Write-Host "⚠️  config.yaml 中未配置 API Key" -ForegroundColor Yellow
    Write-Host "请编辑 config.yaml 填入你的 API Key (DeepSeek / Z.ai / 硅基流动 等)" -ForegroundColor Yellow
    $choice = Read-Host "是否继续运行？(y/N)"
    if ($choice -notmatch "^[yY]$") {
        exit 1
    }
}

# 切换到本地目录运行
Set-Location $localDir

Write-Host ""
Write-Host "🚀 启动本地参谋长分析引擎..." -ForegroundColor Green
Write-Host ""

# 运行主程序
try {
    & $pythonCmd main_local.py
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host "❌ 运行出错: $_" -ForegroundColor Red
    $exitCode = 1
}

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "✅ 运行成功完成！" -ForegroundColor Green
    $outputDir = "D:\osint\data"
    if (Test-Path $outputDir) {
        Write-Host "📁 产物目录: $outputDir" -ForegroundColor Cyan
        Get-ChildItem $outputDir -File | Sort-Object LastWriteTime -Descending | Select-Object -First 10 | ForEach-Object {
            Write-Host "   $_" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "❌ 运行失败，退出码: $exitCode" -ForegroundColor Red
}

Write-Host ""
Read-Host "按回车键退出"
exit $exitCode
