@echo off
REM ============================================================
REM 劳动力调查 AI 助手 · run_eval 完成弹窗 wrapper
REM
REM 用法：scripts\run_eval_notify.bat
REM 行为：自动启动后端（如未跑） → 跑 run_eval.py → 完成后通过
REM       Windows 系统 MessageBox 弹窗提示通过/失败
REM
REM 与直接跑 python scripts\run_eval.py 的区别：
REM   - 自动确保 8765 后端在跑
REM   - 完成后弹系统弹窗（不弹窗你也可以看 task 通知）
REM   - 退出码透传（CI/脚本可继续用）
REM ============================================================

chcp 65001 > nul
cd /d "%~dp0\.."

REM 1) 检查/启动后端
echo [hook] Checking backend at 127.0.0.1:8765...
curl -s -f -m 3 http://127.0.0.1:8765/api/auth/login -X POST -H "Content-Type: application/json" -d "{\"phone\":\"13985000001\"}" >nul 2>&1
if errorlevel 1 (
    echo [hook] Backend not running, starting uvicorn on 127.0.0.1:8765...
    start /B python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 >nul 2>&1
    timeout /t 5 /nobreak > nul
) else (
    echo [hook] Backend already up.
)

REM 2) 跑 eval
echo [hook] Running run_eval.py ...
python scripts\run_eval.py --phone 13985000001
set EVAL_EXIT=%errorlevel%

REM 3) 解析结果
set RESULT_STR=未知
if exist reports\eval-latest.json (
    for /f "usebackq tokens=*" %%i in (`python -c "import json; d=json.load(open('reports/eval-latest.json',encoding='utf-8')); s=d.get('summary',{}); print(f'{s.get(\"passed\",0)}/{s.get(\"total\",0)}')"`) do set RESULT_STR=%%i
)

REM 4) 弹系统弹窗
if %EVAL_EXIT% equ 0 (
    set POPUP_MSG=Eval 全部通过  %RESULT_STR%^n耗时已写入 reports\eval-latest.json
    set POPUP_TITLE=劳动力调查 AI 助手 · 通过
    set POPUP_ICON=Information
) else (
    set POPUP_MSG=Eval 出现失败 %RESULT_STR%^n请查看 reports\eval-latest.json 找 fail 题
    set POPUP_TITLE=劳动力调查 AI 助手 · 失败
    set POPUP_ICON=Warning
)

powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('%POPUP_MSG%','%POPUP_TITLE%','OK','%POPUP_ICON%') | Out-Null"

exit /b %EVAL_EXIT%
