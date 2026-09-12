# Daily参谋系统 - One-click run
# Reads intel, runs analysis, updates hypotheses, generates reports
# 用法：手动双击运行；计划任务调用加 -Auto 参数跳过末尾暂停
# 2026-09-12 重构：移植 refresh.py 的 _step 隔离哲学（此前 $ErrorActionPreference="Stop"
#   导致任一 Step 挂掉全链死，且 2.5/3/3.5 全不跑）+ Start-Transcript 日志
#   （此前零日志，9-07 静默失败无据可查）+ 清理 Sunday 判断死代码（任务实际跑周一）

param([switch]$Auto)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

$logDir = "D:\osint\data\logs"
if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir ("weekly_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
Start-Transcript -Path $logFile -Append | Out-Null

Write-Host "========================================"
Write-Host "Daily参谋系统 - Personal Intelligence"
Write-Host ("Start: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host "========================================"

$projectDir = "D:\osint"
$localDir = Join-Path $projectDir "local"

# Check Python（环境级故障，隔离无意义，直接退出）
$pythonCmd = "python"
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    $pythonCmd = "python3"
}
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found" -ForegroundColor Red
    Stop-Transcript | Out-Null
    if (-not $Auto) { Read-Host "Press Enter to exit" | Out-Null }
    exit 1
}

# Step 级隔离：任一步失败不连累后续步骤（对齐 refresh.py _step 哲学）
# 原生命令非零退出码不触发 catch，需显式检查 $LASTEXITCODE
function Invoke-PyStep {
    param([string]$Name, [string[]]$PyArgs, [string]$Cwd)
    Write-Host ("`n[{0}] ..." -f $Name)
    try {
        if ($Cwd) { Push-Location $Cwd }
        & $pythonCmd @PyArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("[{0}] FAILED (exit={1}) - 隔离生效, 继续后续步骤" -f $Name, $LASTEXITCODE) -ForegroundColor Red
        } else {
            Write-Host ("[{0}] OK" -f $Name)
        }
    } catch {
        Write-Host ("[{0}] FAILED: {1} - 隔离生效, 继续后续步骤" -f $Name, $_.Exception.Message) -ForegroundColor Red
    } finally {
        if ($Cwd) { Pop-Location }
    }
}

try {
    # Step 1: Load persona and knowledge base (import 验证)
    Invoke-PyStep -Name "1/5 Load persona and knowledge base" `
        -PyArgs @('-c', "import sys; sys.path.insert(0,'.'); from load_intel import load_persona; from load_knowledge import load_knowledge; print('OK')") `
        -Cwd $localDir

    # Step 2: Run main analysis (generates brief, dashboard, wiki)
    Invoke-PyStep -Name "2/5 Running main analysis" `
        -PyArgs @('main_local.py') -Cwd $localDir

    # Step 2.5: Refresh indicator values before weekly verification
    # 周循环 AI 裁判消费 assumptions 树里的 indicators[].current_value；
    # verify_hypotheses.py 内部用绝对路径读 macro_indicators.json，与 cwd 无关
    Invoke-PyStep -Name "2.5/5 Refreshing indicator values" `
        -PyArgs @('D:\osint\verify_hypotheses.py')

    # Step 3: Run hypothesis engine (idempotent: materialized views skipped, resolved_at 跳过已验证)
    Invoke-PyStep -Name "3/5 Running hypothesis engine weekly cycle" `
        -PyArgs @('-c', "import sys; sys.path.insert(0,'.'); from analyze import MacroAnalyzer; from load_knowledge import load_knowledge; from hypothesis_engine import HypothesisEngine; import yaml; config=yaml.safe_load(open(r'D:\osint\config.yaml','r',encoding='utf-8')); kb=load_knowledge(r'D:\Codex输出\视频知识库'); kb.load_all(); analyzer=MacroAnalyzer(config,'',kb); engine=HypothesisEngine(config,kb,analyzer); engine.run_weekly_cycle()") `
        -Cwd $localDir

    # Step 3.5: Policy tracker (read-macro weekly observation card)
    Invoke-PyStep -Name "3.5/5 Running policy tracker" `
        -PyArgs @('policy_tracker.py', '--week') -Cwd $localDir

    # Step 4/5: wiki index 由 render_wiki.py 与各步骤自动维护；
    # 周报由 Step 3 的 run_weekly_cycle 写入 reports/（原空步骤与 Sunday 判断已于 2026-09-12 移除）
    Write-Host "`n[4/5] Wiki index auto-maintained by render steps"
    Write-Host "[5/5] Weekly report written by Step 3 (run_weekly_cycle) -> data/reports/"

    Write-Host "`n========================================"
    Write-Host ("Complete! {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Write-Host "Output: D:\osint\data"
    Write-Host "Log: $logFile"
    Write-Host "========================================"
} finally {
    Stop-Transcript | Out-Null
}

if (-not $Auto) {
    Read-Host "Press Enter to exit" | Out-Null
}
