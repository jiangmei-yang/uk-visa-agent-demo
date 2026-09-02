@echo off
setlocal
cd /d "%~dp0"

echo UK Visa Agent Demo
echo Checking Docker Desktop...

where docker >nul 2>nul
if errorlevel 1 goto no_docker

docker info >nul 2>nul
if errorlevel 1 goto docker_stopped

echo Preparing the Demo. The first launch may take a few minutes...
docker compose up --build --detach
if errorlevel 1 goto start_failed

for /L %%i in (1,1,90) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 2 ^| Out-Null; exit 0 } catch { exit 1 }"
  if not errorlevel 1 goto ready
  timeout /t 2 /nobreak >nul
)

echo The Demo did not become ready in time. Recent diagnostic messages:
docker compose logs --tail 30
pause
exit /b 1

:ready
echo The Demo is ready. Opening your browser...
start "" http://127.0.0.1:8000
echo You may close this window. Use STOP_DEMO_WINDOWS.bat when finished.
exit /b 0

:no_docker
echo Docker Desktop is not installed. Install it from:
echo https://www.docker.com/products/docker-desktop/
pause
exit /b 1

:docker_stopped
echo Docker Desktop is installed but not running. Open it, wait until it is ready, then try again.
pause
exit /b 1

:start_failed
echo The Demo could not be started. Review the message above and try again.
pause
exit /b 1
