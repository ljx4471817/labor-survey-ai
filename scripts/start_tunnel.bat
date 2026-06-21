@echo off
REM ============================================
REM  Labor Survey AI Assistant - One-Click Start
REM  Backend (uvicorn :8000) + Cloudflare Tunnel
REM  Auto-opens default browser with the public URL
REM ============================================
REM  - Backend runs in a new window (uvicorn)
REM  - Tunnel runs in a new window (cloudflared, log to file)
REM  - This launcher polls the log, extracts URL, opens browser
REM  - Close the launcher window last; close backend/tunnel first to stop
REM ============================================

chcp 65001 >nul

set "ROOT=%~dp0.."
cd /D "%ROOT%"
set "CF_LOG=%TEMP%\cloudflared_%RANDOM%.log"

echo.
echo ============================================
echo  Labor Survey AI Assistant
echo ============================================
echo.

echo [1/3] Starting backend (uvicorn :8000)...
start "Labor Survey AI Backend" /D "%ROOT%\backend" cmd /k "uvicorn app.main:app --reload --port 8000"
echo       waiting 5s for backend...
ping -n 6 127.0.0.1 >nul

echo.
echo [2/3] Starting Cloudflare Tunnel (new window, log: %CF_LOG%)...
start "Cloudflare Tunnel" cmd /k "cloudflared tunnel --url http://localhost:8000 > %CF_LOG% 2>&1"

echo.
echo [3/3] Waiting for public URL (up to 40s)...
set "URL="
for /l %%i in (1,1,20) do (
  ping -n 3 127.0.0.1 >nul
  for /f "tokens=*" %%u in ('findstr /R "https://.*trycloudflare\.com" "%CF_LOG%" 2^>nul') do (
    if not defined URL set "URL=%%u"
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
REM URL line looks like:
REM   INF |  https://xxxx.trycloudflare.com                              |
REM Extract the URL between the pipes, then trim spaces.
for /f "tokens=2 delims=|" %%a in ("%URL%") do set "URL=%%a"
for /f "tokens=*" %%b in ("%URL%") do set "URL=%%b"

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
echo To stop:
echo   - Close "Labor Survey AI Backend" window  (stops backend)
echo   - Close "Cloudflare Tunnel" window       (stops tunnel)
echo   - Then close this launcher window
echo.
pause
