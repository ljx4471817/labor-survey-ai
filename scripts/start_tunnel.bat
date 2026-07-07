@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

cd /D "%~dp0.."
set "ROOT=%CD%"
set "CF_LOG=%TEMP%\cloudflared_%RANDOM%.log"
set "UVICORN_LOG=%TEMP%\uvicorn_%RANDOM%.log"

echo.
echo ============================================
echo  Labor Survey AI Assistant
echo ============================================
echo.

echo [0/3] Checking port 8001...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8001 ^| findstr LISTENING') do (
  echo       port 8001 occupied by PID %%p, killing...
  powershell -NoProfile -Command "Stop-Process -Id %%p -Force -ErrorAction SilentlyContinue" >nul 2>&1
)
ping -n 3 127.0.0.1 >nul

echo [1/3] Starting backend (uvicorn :8001)...
echo       log: %UVICORN_LOG%
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'cmd' -ArgumentList '/k','cd /D %ROOT%\backend && uvicorn app.main:app --reload --port 8001 2>&1' -RedirectStandardOutput '%UVICORN_LOG%'"

echo       waiting for backend health...
set "READY="
for /l %%i in (1,1,15) do (
  ping -n 3 127.0.0.1 >nul
  curl -s -o nul --max-time 1 http://127.0.0.1:8001/health >nul 2>&1 && (
    set "READY=1"
    goto :backend_ready
  )
)
echo.
echo ================================================
echo  ERROR: backend failed to start within 30s
echo  Check log: %UVICORN_LOG%
echo  ================================================
pause
exit /b 1

:backend_ready
echo       backend ready.

echo.
echo [2/3] Starting Cloudflare Tunnel (HTTP/2 mode)...
echo       log: %CF_LOG%
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'cmd' -ArgumentList '/k','cloudflared tunnel --url http://localhost:8001 --protocol http2 2>&1' -RedirectStandardOutput '%CF_LOG%'"

echo.
echo [3/3] Waiting for public URL (up to ~45s)...
set "URL="
for /l %%i in (1,1,23) do (
  ping -n 3 127.0.0.1 >nul
  for /f "delims=" %%U in ('python "%~dp0extract_cf_url.py" "%CF_LOG%" 2^>nul') do (
    set "URL=%%U"
    goto :got_url
  )
)

echo.
echo ================================================
echo  ERROR: timed out waiting for cloudflared URL
echo  Log file: %CF_LOG%
echo  ================================================
echo.
type "%CF_LOG%"
echo.
pause
exit /b 1

:got_url
echo.
echo ============================================
echo  Public URL: %URL%
echo ============================================
echo.
echo Opening default browser...
start "" "%URL%"

echo.
echo Browser opened. Send this URL to colleagues if needed.
echo.
echo ============================================
echo  Press any key to STOP all services and close
echo ============================================
pause >nul
echo.
echo Stopping services...
taskkill /F /IM uvicorn.exe /T >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
echo All stopped.
