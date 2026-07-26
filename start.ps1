# Starts the ClaimIQ API and UI together.
#
#   .\start.ps1
#
# API  -> http://127.0.0.1:8000/docs
# UI   -> http://localhost:8501
#
# Ctrl+C stops both.

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtualenv found. First-time setup:" -ForegroundColor Yellow
    Write-Host '  python -m venv .venv'
    Write-Host '  .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

# Fail early with a clear message rather than letting uvicorn die on a bound port.
$busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port 8000 is already in use. Stop the other process, or run:" -ForegroundColor Yellow
    Write-Host '  Get-Process python | Where-Object { $_.Path -like "*health insurance*" } | Stop-Process -Force'
    exit 1
}

Write-Host "Starting API  http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$api = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "claimiq.api:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden

# Wait for the API before the UI, so the first page load is not an error banner.
$ready = $false
foreach ($i in 1..40) {
    try {
        Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 750 }
}

if ($ready) {
    Write-Host "API ready." -ForegroundColor Green
} else {
    Write-Host "API did not respond in 30s - the UI will start it in-process instead." -ForegroundColor Yellow
}

Write-Host "Starting UI   http://localhost:8501 ..." -ForegroundColor Cyan
Write-Host "Ctrl+C to stop both." -ForegroundColor DarkGray

# Streamlit runs headless (see .streamlit/config.toml), so open the browser here.
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 4
    Start-Process "http://localhost:8501"
} | Out-Null

try {
    & $python -m streamlit run (Join-Path $root "ui\app.py")
}
finally {
    if ($api -and -not $api.HasExited) {
        Write-Host "Stopping API ..." -ForegroundColor DarkGray
        Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
    }
}
