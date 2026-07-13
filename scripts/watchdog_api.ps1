# API watchdog: 每 60s 探 /api/auth/check，挂了自动重启 uvicorn（不碰 cloudflared）
$ErrorActionPreference = 'Stop'
$Backend = 'D:\code_codex\labor-survey-ai\backend'
$Port = 8001
$CheckUrl = ('http://127.0.0.1:{0}/api/auth/check' -f $Port)
$LogPath = 'D:\code_codex\labor-survey-ai\logs\watchdog.log'
$StdoutPath = 'D:\code_codex\labor-survey-ai\logs\uvicorn-8001-stdout.log'
$StderrPath = 'D:\code_codex\labor-survey-ai\logs\uvicorn-8001-stderr.log'
$IntervalSec = 60
$RequestTimeoutSec = 5

New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null

function Write-Log($msg) {
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Add-Content -Path $LogPath -Value $line
}

function Test-ApiUp {
    try {
        $resp = Invoke-WebRequest -Uri $CheckUrl -TimeoutSec $RequestTimeoutSec -Method GET -UseBasicParsing
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Restart-Uvicorn {
    Write-Log ('WARN: API down, restarting uvicorn on port {0}' -f $Port)
    Get-Process -Name python -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*uvicorn*app.main*' -and $_.CommandLine -like ('*--port* {0}*' -f $Port) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $argList = @('-m','uvicorn','app.main:app','--port', ([string]$Port))
    Start-Process -FilePath python -ArgumentList $argList -WorkingDirectory $Backend `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -WindowStyle Hidden
    Write-Log 'INFO: uvicorn relaunched'
}

Write-Log ('INFO: watchdog started, target={0}' -f $CheckUrl)
while ($true) {
    if (-not (Test-ApiUp)) {
        Restart-Uvicorn
        Start-Sleep -Seconds 8
    }
    Start-Sleep -Seconds $IntervalSec
}