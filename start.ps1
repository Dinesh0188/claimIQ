# Starts the API and the UI together.
#   .\start.ps1
# API  -> http://127.0.0.1:8000/docs
# UI   -> http://localhost:8501

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtualenv found. Run:" -ForegroundColor Yellow
    Write-Host '  python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

Write-Host "Starting API on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$api = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "claimiq.api:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden

try {
    Write-Host "Starting UI on http://localhost:8501 ..." -ForegroundColor Cyan
    & $python -m streamlit run (Join-Path $root "ui\app.py")
}
finally {
    if ($api -and -not $api.HasExited) {
        Write-Host "Stopping API ..." -ForegroundColor DarkGray
        Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
    }
}
