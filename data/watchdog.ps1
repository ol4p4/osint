# Osint Watchdog v2 (2026-09-04) — CI 静默兜底 + 本地自愈优先
# 每 6 小时检查最新 intel jsonl 年龄:
#   无 jsonl 或 >8h 没动 -> 先本地跑 refresh.py (git pull + 本地RSS + 重建, 不依赖 CI)
#                       -> 再 dispatch GitHub CI (双保险)
# 日志按日轮转: logs/watchdog_YYYYMMDD.log
# 并发保护: TEMP 下 osint_refresh.lock, 2h 内视为 refresh.py 正在跑, 直接跳过
$ErrorActionPreference = "Continue"

$repoPath = "D:\osint"
$dataPath = Join-Path $repoPath "data"
$py       = "E:\software\python3.13.8\python.exe"
$lock     = Join-Path $env:TEMP "osint_refresh.lock"
$logDir   = Join-Path $dataPath "logs"

if (!(Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir ("watchdog_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
function Log($m) { Add-Content -Path $log -Value $m }

Log ("[{0}] watchdog run" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))

# --- 并发锁: 避免与 OsintRefresh / 手动刷新同时跑 refresh.py ---
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

function Invoke-CIDispatch {
    try {
        Push-Location $repoPath
        $out = & gh workflow run daily.yml -R ol4p4/osint 2>&1
        if ($LASTEXITCODE -eq 0) { Log "  [gh] CI dispatched" }
        else { Log ("  [gh] dispatch failed exit=" + $LASTEXITCODE + " : " + ($out -join ' ')) }
        Pop-Location
    } catch { Log ("  [gh] ERROR: " + $_.Exception.Message) }
}

# --- 找最新 jsonl ---
$latest = Get-ChildItem -Path $dataPath -Filter "intel_2*.jsonl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($null -eq $latest) {
    Log "  no intel jsonl -> local refresh + dispatch CI"
    Invoke-LocalRefresh
    Invoke-CIDispatch
    exit 0
}

$ageHours = (New-TimeSpan -End (Get-Date) -Start $latest.LastWriteTime).TotalHours
Log ("  latest={0} age={1}h" -f $latest.Name, [math]::Round($ageHours,1))

if ($ageHours -gt 8) {
    Log "  STALE (>8h) -> local refresh first, then dispatch CI"
    Invoke-LocalRefresh
    Invoke-CIDispatch
} else {
    Log "  fresh, skip"
}
