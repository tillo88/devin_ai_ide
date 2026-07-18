param(
    [switch]$Info,
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$repo = (Get-Location).ProviderPath

function Show-Tool($Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host "[tool] $Name -> $($cmd.Source)"
    } else {
        Write-Host "[tool] $Name -> missing" -ForegroundColor Yellow
    }
}

function Get-NpxCommand {
    $npxCmd = Get-Command "npx.cmd" -ErrorAction SilentlyContinue
    if ($npxCmd) {
        return $npxCmd.Source
    }

    $nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
    if ($nodeCmd) {
        $nodeDir = Split-Path $nodeCmd.Source
        $candidate = Join-Path $nodeDir "npx.cmd"
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $npx = Get-Command "npx" -ErrorAction SilentlyContinue
    if ($npx) {
        return $npx.Source
    }

    throw "npx not found. Install Node.js or fix PATH."
}

Write-Host "DEVIN Tauri desktop launcher"
Write-Host "============================"
Write-Host "[repo] $repo"

Show-Tool "node"
Show-Tool "npm"
Show-Tool "npx.cmd"
Show-Tool "npx"
Show-Tool "rustc"
Show-Tool "cargo"

if (-not $SkipPreflight) {
    Write-Host ""
    Write-Host "Running preflight..."
    & "$PSScriptRoot\check-tauri-env.ps1"
    $preflightExit = Get-Variable -Name LASTEXITCODE -ErrorAction SilentlyContinue
    if ($preflightExit -and $preflightExit.Value -ne 0) {
        exit $preflightExit.Value
    }
}

$npx = Get-NpxCommand
$tauriArgs = @("--no-install", "tauri")
if ($Info) {
    $tauriArgs += "info"
} else {
    $tauriArgs += "dev"
}

Write-Host ""
Write-Host "[run] $npx $($tauriArgs -join " ")"
& $npx @tauriArgs
$tauriExit = Get-Variable -Name LASTEXITCODE -ErrorAction SilentlyContinue
if ($tauriExit) {
    exit $tauriExit.Value
}
exit 0
