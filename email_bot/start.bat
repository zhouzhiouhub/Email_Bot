@echo off
chcp 65001 >nul
title Email Bot - Starting...

:: ====== CONFIGURE THIS PATH ======
set PROJ=%~dp0
:: ==================================

echo ======================================
echo  Email Auto-Processing Bot - Start
echo ======================================
echo.

:: ── Detect IMAP IDLE config ──
set IDLE_ENABLED=0
for /f "usebackq tokens=1,* delims==" %%A in ("%PROJ%\.env") do (
    if /i "%%A"=="IMAP_IDLE_ENABLED" (
        if /i "%%B"=="true" set IDLE_ENABLED=1
    )
)

if "%IDLE_ENABLED%"=="1" (
    echo  Mode: IDLE push + polling fallback
    set TOTAL=5
) else (
    echo  Mode: Polling (60s interval)
    set TOTAL=4
)
echo.

:: ── 1. Redis ──
netstat -an | findstr ":6379" | findstr "LISTENING" >nul
if errorlevel 1 (
    echo [1/%TOTAL%] Starting Redis...
    start "Redis" /MIN redis-server
    timeout /t 2 /nobreak >nul
) else (
    echo [1/%TOTAL%] Redis already running, skip
)

:: ── 2. FastAPI ──
echo [2/%TOTAL%] Starting FastAPI (port 8000)...
start "FastAPI - Email Bot" cmd /k "cd /d %PROJ% && .venv\Scripts\python.exe run.py"
timeout /t 5 /nobreak >nul

:: ── 3. Celery Worker ──
echo [3/%TOTAL%] Starting Celery Worker...
start "Celery Worker" cmd /k "cd /d %PROJ% && set PYTHONPATH=%PROJ% && .venv\Scripts\celery.exe -A celery_app worker --loglevel=info --pool=solo"
timeout /t 3 /nobreak >nul

:: ── 4. Celery Beat ──
if "%IDLE_ENABLED%"=="1" (
    echo [4/%TOTAL%] Starting Celery Beat (IDLE enabled, polling >= 5min fallback)...
) else (
    echo [4/%TOTAL%] Starting Celery Beat (polling every 60s)...
)
start "Celery Beat" cmd /k "cd /d %PROJ% && set PYTHONPATH=%PROJ% && .venv\Scripts\celery.exe -A celery_app beat --loglevel=info"
timeout /t 3 /nobreak >nul

:: ── 5. IDLE Watcher (only when IMAP_IDLE_ENABLED=true) ──
if "%IDLE_ENABLED%"=="1" (
    echo [5/%TOTAL%] Starting IMAP IDLE Watcher (push-based inbox)...
    start "IDLE Watcher" cmd /k "cd /d %PROJ% && .venv\Scripts\python.exe idle_watcher.py"
    timeout /t 3 /nobreak >nul
)

:: ── Health check ──
echo.
echo Checking service status...
timeout /t 2 /nobreak >nul
curl -s http://localhost:8000/health
echo.
echo.
echo ======================================
if "%IDLE_ENABLED%"=="1" (
    echo  All services started (with IDLE Watcher)
    echo  Keep FastAPI / Worker / Beat / IDLE windows open
) else (
    echo  All services started
    echo  Keep FastAPI / Worker / Beat windows open
)
echo  You may safely close this startup window
echo ======================================
pause
