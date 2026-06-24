@echo off
REM ============================================
REM  Labor Survey AI Assistant - One-Click Start
REM  Backend (uvicorn :8001) + Cloudflare Tunnel
REM  Auto-opens default browser with the public URL
REM ============================================
REM  - Backend runs in a new window (uvicorn)
REM  - Tunnel runs in a new window (cloudflared --protocol http2, log to file)
REM  - This launcher polls the log, extracts URL, opens browser
REM  - Close the launcher window last; close backend/tunnel first to stop
REM
REM  --protocol http2: 走 TCP 而非 QUIC(UDP)。QUIC 在国内运营商常被
REM  QoS 限速，导致 cloudflared 隧道内 DNS(region1.v2.argotunnel.com)
REM  持续 i/o timeout。http2 是更稳的选择。详见 ADR 0004。
REM ============================================

chcp 65001 >nul
setlocal EnableDelayedExpansion

set "ROOT=%~dp0.."
cd /D "%ROOT%"
set "CF_LOG=%TEMP%\cloudflared_%RANDOM%.log"

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
REM 给进程一点退出时间
ping -n 3 127.0.0.1 >nul

echo [1/3] Starting backend (uvicorn :8001)...
start "Labor Survey AI Backend" /D "%ROOT%\backend" cmd /k "uvicorn app.main:app --reload --port 8001"

REM Wait for backend /health to respond (max ~30s)
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
echo  Check the "Labor Survey AI Backend" window for details
echo  ================================================
pause
exit /b 1

:backend_ready
echo       backend ready.

echo.
echo [2/3] Starting Cloudflare Tunnel (HTTP/2 mode, log: %CF_LOG%)...
start "Cloudflare Tunnel" cmd /k "cloudflared tunnel --url http://localhost:8001 --protocol http2 > %CF_LOG% 2>&1"

echo.
echo [3/3] Waiting for public URL (up to ~45s)...
set "URL="
for /l %%i in (1,1,23) do (
  ping -n 3 127.0.0.1 >nul
  REM 用 Python helper 解析日志（正则 + 去前后缀一步到位，比 findstr /R 可靠）
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
REM URL is already extracted by findstr + token split (no further cleanup needed).

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
