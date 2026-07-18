param(
    [string]$SourceRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath,
    [string]$HostDir = (Join-Path $env:LOCALAPPDATA "DEVIN\desktop-host"),
    [switch]$SkipNpmInstall
)

$ErrorActionPreference = "Stop"

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

function Copy-FileIfExists($Name) {
    $src = Join-Path $SourceRepo $Name
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $HostDir $Name) -Force
    }
}

Initialize-DesktopToolPath

Write-Host "DEVIN Windows desktop host prepare"
Write-Host "==================================="
Write-Host "[source] $SourceRepo"
Write-Host "[host]   $HostDir"

New-Item -ItemType Directory -Force -Path $HostDir | Out-Null
Copy-FileIfExists "package.json"
Copy-FileIfExists "package-lock.json"

# FIX 2026-07-15: prima qui c'era Remove-Item + Copy-Item dell'intera src-tauri
# ad ogni lancio. Cancellava anche src-tauri\target (la build cache di Rust):
# risultato, cargo rifaceva fetch/compilazione COMPLETA a ogni avvio del
# desktop. Ora robocopy /MIR sincronizza solo i file cambiati (mtimes
# preservati => rebuild incrementale) ed ESCLUDE target/ e node_modules da
# copia e cancellazione.
$hostSrcTauri = Join-Path $HostDir "src-tauri"
New-Item -ItemType Directory -Force -Path $hostSrcTauri | Out-Null
& robocopy (Join-Path $SourceRepo "src-tauri") $hostSrcTauri /MIR /XD target node_modules /NFL /NDL /NJH /NJS /NP | Out-Null
# robocopy: 0-7 = successo (0 nulla da fare, 1 file copiati, ...); >=8 = errore
if ($LASTEXITCODE -ge 8) {
    throw "robocopy src-tauri fallito (exit $LASTEXITCODE)"
}

$hostScripts = Join-Path $HostDir "scripts"
New-Item -ItemType Directory -Force -Path $hostScripts | Out-Null
foreach ($scriptName in @("check-tauri-env.ps1", "devin-tauri-dev.ps1", "prepare-windows-desktop-host.ps1", "launch-windows-desktop-host.ps1", "start-fastapi-headless.sh")) {
    Copy-Item -LiteralPath (Join-Path (Join-Path $SourceRepo "scripts") $scriptName) -Destination (Join-Path $hostScripts $scriptName) -Force
}

$nativeLauncherDir = Split-Path -Parent $HostDir
$nativeLauncher = Join-Path $nativeLauncherDir "DEVIN Desktop.cmd"
$nativeLauncherContent = @"
@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\DEVIN\desktop-host\scripts\launch-windows-desktop-host.ps1" -SourceRepo "$SourceRepo" -HostDir "%LOCALAPPDATA%\DEVIN\desktop-host" -BrowserFallback
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo [DEVIN] Avvio desktop fallito. Controlla il backend log in WSL: /home/tillo/devin_ai_ide/logs/fast_app_headless.log
  pause
)
exit /b %EXITCODE%
"@
Set-Content -LiteralPath $nativeLauncher -Value $nativeLauncherContent -Encoding ASCII
Write-Host "[launcher] $nativeLauncher"

# Launcher SILENZIOSO (2026-07-15): stesso avvio ma senza finestra console.
# .vbs con window=0 perche' un .cmd apre comunque un terminale; l'output di
# Tauri finisce in %LOCALAPPDATA%\DEVIN\logs\tauri-dev.log (switch -Silent).
$silentLauncher = Join-Path $nativeLauncherDir "DEVIN Desktop (silenzioso).vbs"
$silentLauncherContent = @"
' DEVIN Desktop senza console. Log: %LOCALAPPDATA%\DEVIN\logs\ (desktop-launch.log, tauri-dev.log)
Set sh = CreateObject("WScript.Shell")
cmd = sh.ExpandEnvironmentStrings("powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""%LOCALAPPDATA%\DEVIN\desktop-host\scripts\launch-windows-desktop-host.ps1"" -SourceRepo ""$SourceRepo"" -HostDir ""%LOCALAPPDATA%\DEVIN\desktop-host"" -BrowserFallback -Silent")
sh.Run cmd, 0, False
"@
Set-Content -LiteralPath $silentLauncher -Value $silentLauncherContent -Encoding ASCII
Write-Host "[launcher] $silentLauncher"

if (-not $SkipNpmInstall) {
    $npm = Get-NpmCommand
    $tauriCli = Join-Path $HostDir "node_modules\@tauri-apps\cli"
    $tauriWin = Join-Path $HostDir "node_modules\@tauri-apps\cli-win32-x64-msvc"
    if (-not (Test-Path -LiteralPath $tauriCli) -or -not (Test-Path -LiteralPath $tauriWin)) {
        Write-Host "[npm] installing desktop dependencies in native Windows host"
        Push-Location -LiteralPath $HostDir
        try {
            & $npm @("install", "--include=optional")
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "[npm] dependencies already present"
    }
}

Write-Host "[ok] desktop host ready"
Write-Host $HostDir
