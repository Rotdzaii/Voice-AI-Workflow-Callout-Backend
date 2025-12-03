# Start all services for VoiceAI MITEK in one go
# - Activates venv
# - Loads environment variables
# - Starts FastAPI backend
# - Opens Swagger and simple HTML UI

param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$NoUI
)

Write-Host "[1/4] Activating venv..." -ForegroundColor Cyan
& .\.venv\Scripts\Activate.ps1

Write-Host "[2/4] Loading environment variables..." -ForegroundColor Cyan
& .\scripts\set_env.ps1

Write-Host "[3/4] Starting backend (FastAPI) on $Host:$Port..." -ForegroundColor Cyan
$backendCmd = "python -m uvicorn backend.main:app --host $Host --port $Port"
Start-Process powershell -ArgumentList "-NoExit","-Command", $backendCmd

Start-Sleep -Seconds 2

if (-not $NoUI) {
    Write-Host "[4/4] Opening Swagger UI and Chat Test page..." -ForegroundColor Cyan
    Start-Process "http://$Host:$Port/docs"
    $uiPath = Join-Path $PSScriptRoot "..\frontend\chat_test.html"
    if (Test-Path $uiPath) {
        Start-Process $uiPath
    } else {
        Write-Host "UI file not found at $uiPath" -ForegroundColor Yellow
    }
}

Write-Host "✅ All services started. Backend listening at http://$Host:$Port" -ForegroundColor Green