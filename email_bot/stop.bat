@echo off
chcp 65001 >nul
title Stop Email Bot

echo Stopping all services...

taskkill /F /FI "WINDOWTITLE eq FastAPI - Email Bot" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Celery Worker" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Celery Beat" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq IDLE Watcher" >nul 2>&1

:: Clean up uvicorn processes
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo All services stopped.
pause
