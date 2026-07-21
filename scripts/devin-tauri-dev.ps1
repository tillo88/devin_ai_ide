# Dal 2026-07-21 (migrazione nativa) il backend parte di DEFAULT dal venv
# Windows .venv-win. Il percorso WSL resta come fallback esplicito (-UseWsl)
# finche' il nativo non e' verificato a fondo, poi verra' rimosso.
param(
    [string]$Distro = "Ubuntu",
    [string]$Repo = "/home/tillo/devin_ai_ide",
    [string]$HealthUrl = "http://127.0.0.1:5000/api/health",
    [string]$AppUrl = "http://127.0.0.1:5000/app",
    [switch]$BrowserFallback,
    [switch]$SkipTauri,
    [switch]$UseWsl,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$HostRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath
Set-Location -LiteralPath $HostRepo

function Add-PathIfExists($Path) {
    if ($Path -and (Test-Path $Path)) {
        $parts = [System.Collections.Generic.List[string]]::new()
        foreach ($part in ($env:Path -split ";")) {
            if ($part) { [void]$parts.Add($part) }
        }
        if (-not ($parts | Where-Object { $_ -ieq $Path })) {
            $env:Path = $Path + ";" + $env:Path
        }
    }
}

function Initialize-DesktopToolPath {
    Add-PathIfExists (Join-Path $env:ProgramFiles "nodejs")
    Add-PathIfExists (Join-Path ${env:ProgramFiles(x86)} "nodejs")
    Add-PathIfExists (Join-Path $env:USERPROFILE ".cargo\bin")
}

function Resolve-ToolPath($Name, [string[]]$Candidates = @()) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).ProviderPath
        }
    }
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-NpmCommand {
    $candidates = @(
        (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
        (Join-Path ${env:ProgramFiles(x86)} "nodejs\npm.cmd")
    )
    $npm = Resolve-ToolPath "npm" $candidates
    if (-not $npm) { throw "npm not found. Install Node.js or fix PATH." }
    return $npm
}

function Test-BackendReady {
    param([string]$Url)
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-BackendHeadless {
    if ($UseWsl) {
        Write-Host "[backend] starting headless in WSL distro $Distro (fallback esplicito)"
        Write-Host "[backend] repo: $Repo"
        Write-Host "[backend] log: $Repo/logs/fast_app_headless.log"
        $wsl = Join-Path $env:WINDIR "System32\wsl.exe"
        if (-not (Test-Path -LiteralPath $wsl)) { $wsl = "wsl.exe" }
        $proc = Start-Process -FilePath $wsl -ArgumentList @(
            "-d", $Distro,
            "--cd", $Repo,
            "--exec", "bash", "scripts/start-fastapi-headless.sh"
        ) -WindowStyle Hidden -PassThru
        $proc.WaitForExit(5000) | Out-Null
        if ($proc.HasExited -and $proc.ExitCode -ne 0) {
            Write-Host "[warn] backend starter exited with code $($proc.ExitCode)" -ForegroundColor Yellow
        }
        return
    }

    $venvPy = Join-Path $HostRepo ".venv-win\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Write-Host "[error] venv Windows non trovato: $venvPy" -ForegroundColor Red
        Write-Host "        Esegui prima: powershell -ExecutionPolicy Bypass -File scripts\setup_devin_windows.ps1" -ForegroundColor Yellow
        Write-Host "        (oppure usa -UseWsl per il vecchio backend WSL)" -ForegroundColor Yellow
        exit 1
    }
    $logDir = Join-Path $HostRepo "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logFile = Join-Path $logDir "fast_app_native.log"
    Write-Host "[backend] starting native Windows backend (.venv-win)"
    Write-Host "[backend] repo: $HostRepo"
    Write-Host "[backend] log: $logFile"
    Start-Process -FilePath $venvPy -ArgumentList @("devin\ui\fast_app.py") `
        -WorkingDirectory $HostRepo -WindowStyle Hidden `
        -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err" | Out-Null
}

function Wait-Backend {
    param([string]$Url, [int]$Timeout)
    $deadline = (Get-Date).AddSeconds($Timeout)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendReady $Url) { return $true }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

Initialize-DesktopToolPath

Write-Host "DEVIN Desktop headless launcher"
Write-Host "================================"
Write-Host "[host repo] $HostRepo"
if ($UseWsl) { Write-Host "[wsl repo] $Repo" } else { Write-Host "[mode] backend nativo Windows (.venv-win); usa -UseWsl per il fallback WSL" }
Write-Host "[health] $HealthUrl"

if (-not (Test-BackendReady $HealthUrl)) {
    Start-BackendHeadless
}

if (-not (Wait-Backend -Url $HealthUrl -Timeout $TimeoutSeconds)) {
    Write-Host "[error] backend did not become ready within $TimeoutSeconds seconds" -ForegroundColor Red
    if ($UseWsl) {
        Write-Host "        Check WSL log: $Repo/logs/fast_app_headless.log" -ForegroundColor Yellow
    } else {
        Write-Host "        Check log: $HostRepo\logs\fast_app_native.log (+ .err)" -ForegroundColor Yellow
    }
    exit 1
}

Write-Host "[ok] backend reachable"

if ($SkipTauri) {
    if ($BrowserFallback) { Start-Process $AppUrl | Out-Null }
    exit 0
}

try {
    $npm = Get-NpmCommand
    Write-Host "[desktop] starting Tauri dev shell"
    & $npm @("run", "desktop:dev")
    exit $LASTEXITCODE
} catch {
    Write-Host "[warn] Tauri launch failed: $($_.Exception.Message)" -ForegroundColor Yellow
    if ($BrowserFallback) {
        Write-Host "[fallback] opening browser $AppUrl"
        Start-Process $AppUrl | Out-Null
        exit 0
    }
    exit 1
}
