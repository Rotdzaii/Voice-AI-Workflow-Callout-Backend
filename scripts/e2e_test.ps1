# Runs end-to-end checks: DB connectivity, start API server, hit health/docs, run pytest, and clean up.
# Usage:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\scripts\e2e_test.ps1 [-InstallDeps] [-StartPort 8000]

param(
    [switch]$InstallDeps,
    [int]$StartPort = 8000
)

$ErrorActionPreference = 'Stop'

function Find-Python {
    $venvPy = Join-Path $PSScriptRoot '..\\.venv\\Scripts\\python.exe'
    if (Test-Path $venvPy) { return (Resolve-Path $venvPy).Path }
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if ($py) { return $py.Path }
    throw 'Python not found. Create venv or install Python and ensure it is in PATH.'
}

function Find-FreePort([int]$p) {
    for ($port = $p; $port -lt ($p + 50); $port++) {
        try {
            $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
            if (-not $conn) { return $port }
        } catch {}
    }
    throw "No free port found in range $p..$(($p+49))"
}

function Invoke-Http200($url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Stop-ProcessSafe($procId) {
    try { if ($procId) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } } catch {}
}

Write-Host "[E2E] Repo root:" (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Find-Python
Write-Host "[E2E] Using Python:" $python

if ($InstallDeps) {
    Write-Host "[E2E] Installing dependencies from requirements.txt ..."
    & $python -m pip install --upgrade pip
    & $python -m pip install -r (Join-Path $PSScriptRoot '..\\requirements.txt')
}

# 1) DB connectivity check
Write-Host "[E2E] Checking DB connectivity ..."
& $python (Join-Path $PSScriptRoot 'check_db.py')
$dbExit = $LASTEXITCODE
if ($dbExit -eq 0) {
    Write-Host "[E2E] DB connectivity: OK" -ForegroundColor Green
} elseif ($dbExit -eq 2) {
    Write-Warning "[E2E] DATABASE_URL is not set. Some tests may be skipped."
} else {
    Write-Warning "[E2E] DB unreachable. DB-dependent tests will likely be skipped."
}

# 2) Start API server (uvicorn) on a free port
$port = Find-FreePort -p $StartPort
Write-Host "[E2E] Starting API on port $port ..."
$uvicornArgs = @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',"$port")
$server = Start-Process -FilePath $python -ArgumentList $uvicornArgs -PassThru -WindowStyle Hidden
Write-Host "[E2E] API PID:" $server.Id

try {
    # 3) Wait for /health
    $healthUrl = "http://127.0.0.1:$port/health"
    $docsUrl   = "http://127.0.0.1:$port/docs"
    $openapiUrl= "http://127.0.0.1:$port/openapi.json"

    Write-Host "[E2E] Waiting for /health ..."
    $ready = $false
    for ($i=0; $i -lt 60; $i++) {
        if (Invoke-Http200 $healthUrl) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "API did not become ready at $healthUrl" }
    Write-Host "[E2E] /health OK" -ForegroundColor Green

    if (-not (Invoke-Http200 $docsUrl)) { throw "/docs is not responding 200" }
    Write-Host "[E2E] /docs OK" -ForegroundColor Green

    # 4) Run tests
    Write-Host "[E2E] Running pytest ..."
    # Prefer DB path in tests; avoid external Supabase SDK dependency
    $env:SUPABASE_USE_SDK = "false"
    & $python -m pytest -q
    $pytestExit = $LASTEXITCODE
    if ($pytestExit -ne 0) { throw "pytest failed with exit code $pytestExit" }
    Write-Host "[E2E] pytest PASSED" -ForegroundColor Green

} finally {
    Write-Host "[E2E] Stopping API server ..."
    Stop-ProcessSafe $server.Id
}

Write-Host "[E2E] All checks passed." -ForegroundColor Green
exit 0
