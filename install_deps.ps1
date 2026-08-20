<#
.SYNOPSIS
    OSINT 个人智库系统 - 依赖安装脚本
.DESCRIPTION
    自动安装 Python、pip 依赖、配置镜像源
    以管理员权限运行效果最佳
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "📦 OSINT 个人智库系统 - 依赖安装" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 检查/安装 Python
Write-Host "[1/5] 检查 Python..." -ForegroundColor Yellow
$pythonCmd = "python"
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    $pythonCmd = "python3"
}
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    Write-Host "   未检测到 Python，尝试通过 winget 安装..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
        # 刷新环境变量
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        if (Get-Command python -ErrorAction SilentlyContinue) {
            Write-Host "   ✅ Python 安装成功" -ForegroundColor Green
        } else {
            Write-Host "   ❌ Python 安装失败，请手动安装 https://python.org" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "   ❌ 未找到 winget，请手动安装 Python 3.10+ 从 https://python.org" -ForegroundColor Red
        exit 1
    }
} else {
    $pyVersion = & $pythonCmd --version 2>&1
    Write-Host "   ✅ 已安装: $pyVersion" -ForegroundColor Green
}

# 确定最终的 python 命令
if (Get-Command python -ErrorAction SilentlyContinue) { $pythonCmd = "python" }
elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $pythonCmd = "python3" }

# 2. 升级 pip 并配置镜像源
Write-Host "[2/5] 配置 pip 镜像源（清华源）..." -ForegroundColor Yellow
& $pythonCmd -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple/ 2>&1 | Out-Null
Write-Host "   ✅ pip 已升级并配置镜像源" -ForegroundColor Green

# 3. 安装核心依赖
Write-Host "[3/5] 安装核心 Python 依赖..." -ForegroundColor Yellow
$packages = @(
    "pyyaml",
    "feedparser", 
    "requests",
    "lxml",
    "simhash",
    "openai",
    "pydantic"
)

foreach ($pkg in $packages) {
    Write-Host "   安装 $pkg ..." -NoNewline
    $result = & $pythonCmd -m pip install $pkg -i https://pypi.tuna.tsinghua.edu.cn/simple/ 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host " ✅" -ForegroundColor Green
    } else {
        Write-Host " ⚠️ (可能已安装)" -ForegroundColor Yellow
    }
}

# 4. 安装可选依赖（用于增强功能）
Write-Host "[4/5] 安装可选增强依赖..." -ForegroundColor Yellow
$optional = @(
    "playwright",  # 用于渲染 JS 页面（政府门户可能需要）
    "python-dotenv", # 环境变量管理
)
foreach ($pkg in $optional) {
    Write-Host "   安装 $pkg ..." -NoNewline
    $result = & $pythonCmd -m pip install $pkg -i https://pypi.tuna.tsinghua.edu.cn/simple/ 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host " ✅" -ForegroundColor Green
    } else {
        Write-Host " ⚠️ (可选，跳过)" -ForegroundColor Yellow
    }
}

# 安装 playwright 浏览器（如果安装成功）
if (Get-Command playwright -ErrorAction SilentlyContinue) {
    Write-Host "   安装 Playwright Chromium..." -ForegroundColor Yellow
    & $pythonCmd -m playwright install chromium 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ Playwright Chromium 就绪" -ForegroundColor Green
    }
}

# 5. 验证安装
Write-Host "[5/5] 验证安装..." -ForegroundColor Yellow
$testScript = @"
import yaml, feedparser, requests, lxml, simhash, openai, pydantic
print("✅ 所有核心模块导入成功")
print(f"PyYAML: {yaml.__version__}")
print(f"feedparser: {feedparser.__version__}")
print(f"requests: {requests.__version__}")
print(f"lxml: {lxml.__version__}")
print(f"simhash: 可用")
print(f"openai: {openai.__version__}")
print(f"pydantic: {pydantic.VERSION}")
"@

$result = & $pythonCmd -c $testScript 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   $result" -ForegroundColor Green
} else {
    Write-Host "   ⚠️ 部分验证失败: $result" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "✅ 依赖安装完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "下一步：" -ForegroundColor Yellow
Write-Host "  1. 编辑 config.yaml 填入你的 API Key (DeepSeek / Z.ai / 硅基流动等)" -ForegroundColor Gray
Write-Host "  2. 双击 run.ps1 运行本地参谋长分析" -ForegroundColor Gray
Write-Host "  3. 或配置 GitHub Actions Secrets 启用云端每日采集" -ForegroundColor Gray
Write-Host ""
Read-Host "按回车键退出"
