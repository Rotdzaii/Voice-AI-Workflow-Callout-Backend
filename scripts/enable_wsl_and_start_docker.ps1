# Enables required Windows features for Docker (WSL + VMP),
# sets WSL2 as default, starts Docker Desktop, and runs `docker compose up -d`.
# Run this script in an elevated PowerShell (Run as Administrator).

param(
    [string]$ComposeDir = "$PSScriptRoot\..",
    [switch]$SkipCompose
)

function Assert-Admin {
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Error "Please run this script in an elevated PowerShell (Run as Administrator)."
        exit 1
    }
}

function Get-FeatureState($name) {
    try {
        return (Get-WindowsOptionalFeature -Online -FeatureName $name -ErrorAction Stop).State
    } catch {
        return "Unknown"
    }
}

function Enable-Feature($name, $dismName) {
    Write-Host "Enabling feature: $name ..."
    & dism.exe /online /enable-feature /featurename:$dismName /all /norestart | Out-Null
}

function Ensure-WSL2 {
    try {
        wsl --set-default-version 2 | Out-Null
    } catch {
        Write-Warning "Could not set WSL default version. Ensure WSL is installed."
    }
}

function Restart-WSLServices {
    try { wsl --shutdown | Out-Null } catch {}
    try { Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue } catch {}
}

function Start-DockerDesktop {
    $dockerExe = "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe"
    if (-not (Test-Path $dockerExe)) {
        Write-Error "Docker Desktop executable not found at $dockerExe. Please install Docker Desktop."
        exit 1
    }
    Write-Host "Starting Docker Desktop ..."
    Start-Process $dockerExe | Out-Null
}

function Wait-DockerEngine {
    Write-Host "Waiting for Docker engine to respond ..."
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            docker info | Out-Null
            $ok = $true
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ok) {
        Write-Error "Docker engine did not start. Open Docker Desktop and ensure 'Use the WSL 2 based engine' is enabled (Settings > General)."
        exit 1
    }
}

Assert-Admin

# 1) Ensure required Windows features are enabled
$wslState = Get-FeatureState "Microsoft-Windows-Subsystem-Linux"
$vmpState = Get-FeatureState "VirtualMachinePlatform"
$enabledAny = $false

if ($wslState -ne "Enabled") {
    Enable-Feature "Windows Subsystem for Linux" "Microsoft-Windows-Subsystem-Linux"
    $enabledAny = $true
}
if ($vmpState -ne "Enabled") {
    Enable-Feature "Virtual Machine Platform" "VirtualMachinePlatform"
    $enabledAny = $true
}

if ($enabledAny) {
    Write-Host "One or more features were enabled. A restart is required. Please restart Windows and re-run this script." -ForegroundColor Yellow
    exit 3010
}

# 2) Ensure WSL2 default and restart WSL services
Ensure-WSL2
Restart-WSLServices

# 3) Start Docker Desktop and wait for engine
Start-DockerDesktop
Wait-DockerEngine

# 4) Optionally run compose
if (-not $SkipCompose) {
    if (-not (Test-Path $ComposeDir)) {
        Write-Error "Compose directory not found: $ComposeDir"
        exit 1
    }
    Push-Location $ComposeDir
    try {
        docker compose config | Out-Null
    } catch {
        Write-Warning "Compose config check failed; proceeding to up -d."
    }
    Write-Host "Running 'docker compose up -d' in $ComposeDir ..."
    docker compose up -d
    Pop-Location
}

Write-Host "All done."
