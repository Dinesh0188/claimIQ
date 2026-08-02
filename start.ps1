# Starts ClaimIQ.
#
#   .\start.ps1              product UI + API on one port
#   .\start.ps1 -Streamlit   also start the legacy Streamlit UI on :8501
#
# Landing   -> http://127.0.0.1:8000/
# Product   -> http://127.0.0.1:8000/app.html
# API docs  -> http://127.0.0.1:8000/docs
#
# One process by default. The product UI is a static SPA served by the same FastAPI
# app that runs the engine, so there is no second server to keep alive, no second port
# to remember and no build step. The Streamlit UI it replaced is still in ui/ and
# still works; -Streamlit runs it alongside for comparison.
#
# Ctrl+C stops everything.

param(
    [switch]$Streamlit,
    [int]$Port = 8000
)

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtualenv found. First-time setup:" -ForegroundColor Yellow
    Write-Host '  python -m venv .venv'
    Write-Host '  .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

# Fail early with a clear message rather than letting uvicorn die on a bound port.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $Port is already in use. Stop the other process, or run:" -ForegroundColor Yellow
    Write-Host '  Get-Process python | Where-Object { $_.Path -like "*health insurance*" } | Stop-Process -Force'
    exit 1
}

$streamlitProc = $null
if ($Streamlit) {
    Write-Host "Starting legacy Streamlit UI  http://localhost:8501 ..." -ForegroundColor DarkGray
    $streamlitProc = Start-Process -FilePath $python `
        -ArgumentList "-m", "streamlit", "run", (Join-Path $root "ui\app.py"), "--server.headless", "true" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden
}

# Open the browser once the server actually answers, so the first paint is the app
# rather than a connection error the user has to reload past.
Start-Job -ScriptBlock {
    param($p)
    foreach ($i in 1..60) {
        try { Invoke-RestMethod "http://127.0.0.1:$p/health" -TimeoutSec 2 | Out-Null; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    Start-Process "http://127.0.0.1:$p/app.html"
} -ArgumentList $Port | Out-Null

Write-Host ""
Write-Host "  Product   http://127.0.0.1:$Port/app.html" -ForegroundColor Cyan
Write-Host "  Landing   http://127.0.0.1:$Port/" -ForegroundColor DarkGray
Write-Host "  API docs  http://127.0.0.1:$Port/docs" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

try {
    & $python -m uvicorn claimiq.api:app --host 127.0.0.1 --port $Port
}
finally {
    if ($streamlitProc -and -not $streamlitProc.HasExited) {
        Write-Host "Stopping Streamlit ..." -ForegroundColor DarkGray
        Stop-Process -Id $streamlitProc.Id -Force -ErrorAction SilentlyContinue
    }
    Get-Job | Remove-Job -Force -ErrorAction SilentlyContinue
}
