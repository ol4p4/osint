# Daily Question & View Generator (P2a/P2b entry)
# 用法: 右键"使用 PowerShell 运行"; 计划任务/自动化调用加 -Auto 跳过交互暂停

param([switch]$Auto)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

$projectDir = "D:\osint"
$localDir = Join-Path $projectDir "local"

Write-Host "=== Daily Question & View Generator ===" -ForegroundColor Cyan
Write-Host "  1. Interactive dialogue (type idea -> 5 rounds -> view card)" -ForegroundColor Green
Write-Host "  2. Batch: process Obsidian idea drafts directory" -ForegroundColor Green
Write-Host "  3. Generate daily open questions from analysis" -ForegroundColor Green

$mode = "1"
if (-not $Auto) {
    $mode = Read-Host "Choose mode (1/2/3, default 1)"
}
Set-Location $localDir

switch ($mode) {
    "2" {
        $dir = Read-Host "Path to Obsidian idea drafts directory"
        if (-not $dir) { $dir = "D:\osint\data\dialogues\ideas" }
        & python dialogue_engine.py --batch $dir
    }
    "3" {
        & python question_generator.py
    }
    default {
        $idea = ""
        if (-not $Auto) {
            Write-Host "Type your idea (Enter to submit):" -ForegroundColor White
            $idea = Read-Host
        }
        if ($idea) {
            & python dialogue_engine.py --interactive $idea --feed-hyp
        } else {
            & python dialogue_engine.py --interactive
        }
    }
}

Write-Host ""
Write-Host "Done. Cards: D:\osint\data\dialogues\view_cards\" -ForegroundColor Gray

if (-not $Auto) {
    Read-Host "Press Enter to exit"
}
