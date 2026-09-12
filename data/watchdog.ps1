# Osint Watchdog v3 (2026-09-12) — CI 静默兜底 + 本地自愈 + 周报缺失补跑
# 每 6 小时三项检查:
#   1) intel jsonl 年龄 >8h -> 本地跑 refresh.py (git pull + 本地RSS + 重建, 不依赖 CI)
#                            -> 再 dispatch GitHub CI (v2 原有, 双保险)
#   2) 最近 CI 成功 run >12h -> dispatch CI (v3 新增: 本地 fetch_now 活着时 jsonl 永远新鲜,
#                            v2 的 mtime 检测测不出「CI 死但本地活」)
#   3) 最新周报 >8 天 (错过至少一个周一) -> 补跑 daily_run.ps1 -Auto
#                            (v3 新增: 校准闭环调度兜底, run_weekly_cycle 幂等安全;
#                             补跑带 24h 节流戳, 因 main_local 分析有 AI 成本)
# 日志按日轮转: logs/watchdog_YYYYMMDD.log
# 并发保护: TEMP 下 osint_refresh.lock, 2h 内视为 refresh/daily_run 正在跑, 直接跳过
$ErrorActionPreference = "Continue"

$repoPath = "D:\osint"
$dataPath = Join-Path $repoPath "data"
$py       = "E:\software\python3.13.8\python.exe"
$lock     = Join-Path $env:TEMP "osint_refresh.lock"
$logDir   = Join-Path $dataPath "logs"
$catchupStamp = Join-Path $dataPath ".weekly_catchup_last_run"

if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir ("watchdog_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
function Log($m) { Add-Content -Path $log -Value $m }

Log ("[{0}] watchdog run" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))

# --- 并发锁: 避免与 OsintRefresh / 手动刷新同时跑 refresh.py / daily_run.ps1 ---
if (Test-Path $lock) {
    $lockAge = (New-TimeSpan -Start (Get-Item $lock).LastWriteTime -End (Get-Date)).TotalHours
    if ($lockAge -lt 2) { Log ("  lock held (age {0}h), skip" -f [math]::Round($lockAge,1)); exit 0 }
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}

function Invoke-LocalRefresh {
    New-Item -ItemType File -Path $lock -Force | Out-Null
    try {
        Log "  [refresh] start (local self-heal)"
        $out = & $py (Join-Path $dataPath "refresh.py") 2>&1
        $out | Select-Object -Last 25 | ForEach-Object { Log ("  [refresh] " + $_) }
        Log "  [refresh] done"
    } catch { Log ("  [refresh] ERROR: " + $_.Exception.Message) }
    finally { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
}

function Invoke-WeeklyRun {
    New-Item -ItemType File -Path $lock -Force | Out-Null
    try {
        Log "  [weekly] start (daily_run.ps1 -Auto 补跑)"
        $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $dataPath "daily_run.ps1") -Auto 2>&1
        $out | Select-Object -Last 30 | ForEach-Object { Log ("  [weekly] " + $_) }
        Log "  [weekly] done"
        New-Item -ItemType File -Path $catchupStamp -Force | Out-Null
    } catch { Log ("  [weekly] ERROR: " + $_.Exception.Message) }
    finally { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
}

function Invoke-CIDispatch {
    try {
        Push-Location $repoPath
        $out = & gh workflow run daily.yml -R ol4p4/osint 2>&1
        if ($LASTEXITCODE -eq 0) { Log "  [gh] CI dispatched" }
        else { Log ("  [gh] dispatch failed exit=" + $LASTEXITCODE + " : " + ($out -join ' ')) }
        Pop-Location
    } catch { Log ("  [gh] ERROR: " + $_.Exception.Message) }
}

# --- 检查 2 (v3): 最近 CI 成功 >12h -> dispatch ---
function Invoke-CIFreshnessCheck {
    try {
        $json = & gh run list -R ol4p4/osint --limit 5 --json createdAt,conclusion 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $json) { Log "  [ci-check] gh 不可用, 跳过"; return }
        $runs = ($json -join "`n") | ConvertFrom-Json
        $lastOk = $runs | Where-Object { $_.conclusion -eq 'success' } | Select-Object -First 1
        if (-not $lastOk) { Log "  [ci-check] 近 5 次 run 无成功 -> dispatch"; Invoke-CIDispatch; return }
        $ageH = (New-TimeSpan -End (Get-Date) -Start ([datetime]$lastOk.createdAt)).TotalHours
        Log ("  [ci-check] last CI success age={0}h" -f [math]::Round($ageH,1))
        if ($ageH -gt 12) {
            Log "  [ci-check] STALE (>12h) -> dispatch CI"
            Invoke-CIDispatch
        }
    } catch { Log ("  [ci-check] ERROR: " + $_.Exception.Message) }
}

# --- 检查 3 (v3): 周报缺失 >8 天 -> 补跑 daily_run (24h 节流) ---
function Invoke-WeeklyReportCheck {
    if (Test-Path $catchupStamp) {
        try {
            $ageH = (New-TimeSpan -End (Get-Date) -Start (Get-Item $catchupStamp).LastWriteTime).TotalHours
            if ($ageH -lt 24) { Log ("  [weekly-check] 补跑节流中 (戳 {0:N1}h 前), skip" -f $ageH); return }
        } catch {}
    }
    $latestReport = Get-ChildItem -Path (Join-Path $dataPath "reports") -Filter "hypothesis_weekly_*.md" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -eq $latestReport) {
        Log "  [weekly-check] 无周报 -> 补跑 daily_run"
        Invoke-WeeklyRun
        return
    }
    $ageDays = (New-TimeSpan -End (Get-Date) -Start $latestReport.LastWriteTime).TotalDays
    Log ("  [weekly-check] latest={0} age={1}d" -f $latestReport.Name, [math]::Round($ageDays,1))
    if ($ageDays -gt 8) {
        Log "  [weekly-check] STALE (>8d, 错过周一) -> 补跑 daily_run"
        Invoke-WeeklyRun
    }
}

# --- 检查 1: 找最新 jsonl ---
$latest = Get-ChildItem -Path $dataPath -Filter "intel_2*.jsonl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($null -eq $latest) {
    Log "  no intel jsonl -> local refresh + dispatch CI"
    Invoke-LocalRefresh
    Invoke-CIDispatch
} else {
    $ageHours = (New-TimeSpan -End (Get-Date) -Start $latest.LastWriteTime).TotalHours
    Log ("  latest={0} age={1}h" -f $latest.Name, [math]::Round($ageHours,1))
    if ($ageHours -gt 8) {
        Log "  STALE (>8h) -> local refresh first, then dispatch CI"
        Invoke-LocalRefresh
        Invoke-CIDispatch
    } else {
        Log "  fresh, skip"
    }
}

# --- v3 追加检查（独立执行, 各自失败不互相影响）---
Invoke-CIFreshnessCheck
Invoke-WeeklyReportCheck
