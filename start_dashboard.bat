@echo off
REM Osint Dashboard Starter
REM Double-click to: pull latest + start server (detached) + open browser
REM Server keeps running after this window closes. Kill via Task Manager - python.exe

echo ============================================
echo   Osint Dashboard Launcher
echo ============================================
echo.

echo [1/3] Pulling latest intel + rebuilding dashboard...
echo       (may take 10-30s)
echo.
"E:\software\python3.13.8\python.exe" D:/osint/data/refresh.py
if errorlevel 1 (
    echo.
    echo [WARN] refresh failed - old data will be used
)
echo.

echo [2/3] Starting HTTP server (port 19090, detached)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = New-Object System.Diagnostics.ProcessStartInfo; $s.FileName = 'E:\software\python3.13.8\python.exe'; $s.Arguments = 'D:/osint/data/serve.py'; $s.UseShellExecute = $false; $s.CreateNoWindow = $true; [System.Diagnostics.Process]::Start($s) | Out-Null; Start-Sleep -Seconds 2"
echo       Server started in background (detached from this window)
echo.

echo [3/3] Opening browser...
start http://127.0.0.1:19090/interactive_dashboard.html
echo.

echo ============================================
echo   Dashboard ready
echo   Close this window: server keeps running
echo   Stop server: Task Manager - python.exe
echo ============================================
timeout /t 5 /nobreak >nul
exit
