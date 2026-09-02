@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not installed; there is no Demo container to stop.
  pause
  exit /b 0
)

docker compose down
echo The UK Visa Agent Demo has stopped.
