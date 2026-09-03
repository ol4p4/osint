# Daily参谋系统 - One-click run
# Reads intel, runs analysis, updates hypotheses, generates reports
# 用法：手动双击运行；计划任务调用加 -Auto 参数跳过末尾暂停

param([switch]$Auto)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Daily参谋系统 - Personal Intelligence" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$projectDir = "D:\osint"
$localDir = Join-Path $projectDir "local"

# Check Python
$pythonCmd = "python"
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    $pythonCmd = "python3"
}
if (-not (Get-Command $pythonCmd -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Step 1: Load persona and knowledge base
Write-Host "
[1/5] Loading persona and knowledge base..." -ForegroundColor Green
Set-Location $localDir
& $pythonCmd -c "import sys; sys.path.insert(0,'.'); from load_intel import load_persona; from load_knowledge import load_knowledge; print('OK')"

# Step 2: Run main analysis (generates brief, dashboard, wiki)
Write-Host "
[2/5] Running main analysis..." -ForegroundColor Green
& $pythonCmd main_local.py

# Step 3: Run hypothesis engine (idempotent: materialized views skipped, only due ones verified)
Write-Host "
[3/5] Running hypothesis engine..." -ForegroundColor Green
& $pythonCmd -c "import sys; sys.path.insert(0,'.'); from analyze import MacroAnalyzer; from load_knowledge import load_knowledge; from hypothesis_engine import HypothesisEngine; import yaml; config=yaml.safe_load(open(r'D:\osint\config.yaml','r',encoding='utf-8')); kb=load_knowledge(r'D:\Codex输出\视频知识库'); kb.load_all(); analyzer=MacroAnalyzer(config,'',kb); engine=HypothesisEngine(config,kb,analyzer); engine.run_weekly_cycle()"

# Step 3.5: Policy tracker (read-macro weekly observation card)
Write-Host "
[3.5/5] Running policy tracker..." -ForegroundColor Green
& $pythonCmd policy_tracker.py --week

# Step 4: Update wiki index
Write-Host "
[4/5] Updating wiki index..." -ForegroundColor Green
# (Auto-updated by render_wiki.py)

# Step 5: Weekly report note (run_weekly_cycle in Step 3 already writes reports/ daily)
$dayOfWeek = (Get-Date).DayOfWeek
if ($dayOfWeek -eq "Sunday") {
    Write-Host "
[5/5] Sunday - weekly report already written by Step 3" -ForegroundColor Green
} else {
    Write-Host "
[5/5] Not Sunday" -ForegroundColor Gray
}

Write-Host "
========================================" -ForegroundColor Cyan
Write-Host "Complete!" -ForegroundColor Green
Write-Host "Output: D:\osint\data" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan

if (-not $Auto) {
    Read-Host "Press Enter to exit"
}