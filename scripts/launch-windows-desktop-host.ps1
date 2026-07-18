param(
    [string]$SourceRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath,
    [string]$HostDir = (Join-Path $env:LOCALAPPDATA "DEVIN\desktop-host"),
    [string]$Distro = "Ubuntu",
    [string]$WslRepo = "/home/tillo/devin_ai_ide",
    [switch]$BrowserFallback,
    [switch]$SkipNpmInstall,
    [switch]$Info,
    [switch]$Silent
)

$ErrorActionPreference = "Stop"
$DesktopRoot = Join-Path $env:LOCALAPPDATA "DEVIN"
$LogDir = Join-Path $DesktopRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LaunchLog = Join-Path $LogDir "desktop-launch.log"
$TauriLog = Join-Path $LogDir "tauri-dev.log"
try { Start-Transcript -LiteralPath $LaunchLog -Append | Out-Null } catch { }

function Add-PathIfExists($Path) {
    if ($Path -and (Test-Path $Path) -and -not (($env:Path -split ";") | Where-Object { $_ -ieq $Path })) {
        $env:Path = $Path + ";" + $env:Path
    }
}

function Initialize-DesktopToolPath {
    Add-PathIfExists (Join-Path $env:ProgramFiles "nodejs")
    Add-PathIfExists (Join-Path ${env:ProgramFiles(x86)} "nodejs")
    Add-PathIfExists (Join-Path $env:USERPROFILE ".cargo\bin")
}

function Resolve-ToolPath($Name, [string[]]$Candidates = @()) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) { return (Resolve-Path -LiteralPath $candidate).ProviderPath }
    }
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-NpmCommand {
    $npm = Resolve-ToolPath "npm" @((Join-Path $env:ProgramFiles "nodejs\npm.cmd"), (Join-Path ${env:ProgramFiles(x86)} "nodejs\npm.cmd"))
    if (-not $npm) { throw "npm not found. Install Node.js or fix PATH." }
    return $npm
}

function Get-NodeCommand {
    $node = Resolve-ToolPath "node" @((Join-Path $env:ProgramFiles "nodejs\node.exe"), (Join-Path ${env:ProgramFiles(x86)} "nodejs\node.exe"))
    if (-not $node) { throw "node.exe not found. Install Node.js or fix PATH." }
    return $node
}

function Get-TauriCliScript {
    $tauriJs = Join-Path $HostDir "node_modules\@tauri-apps\cli\tauri.js"
    if (-not (Test-Path -LiteralPath $tauriJs)) { throw "Tauri CLI script not found: $tauriJs. Run prepare-windows-desktop-host.ps1." }
    return $tauriJs
}

Initialize-DesktopToolPath

Write-Host "DEVIN Windows-native desktop launcher"
Write-Host "====================================="
Write-Host "[source] $SourceRepo"
Write-Host "[host]   $HostDir"
Write-Host "[logs]   $LogDir"

$prepareScript = Join-Path (Join-Path $SourceRepo "scripts") "prepare-windows-desktop-host.ps1"
& $prepareScript -SourceRepo $SourceRepo -HostDir $HostDir -SkipNpmInstall:$SkipNpmInstall
if (-not $?) { exit 1 }

$backendScript = Join-Path (Join-Path $SourceRepo "scripts") "devin-tauri-dev.ps1"
& $backendScript -Repo $WslRepo -Distro $Distro -SkipTauri
if (-not $?) { exit 1 }

$node = Get-NodeCommand
$tauriJs = Get-TauriCliScript
Push-Location -LiteralPath $HostDir
try {
    if ($Info) {
        Write-Host "[desktop] running Tauri info from native Windows host"
        $tauriCommand = "info"
    } else {
        Write-Host "[desktop] running Tauri from native Windows host"
        $tauriCommand = "dev"
    }
    Add-Content -LiteralPath $TauriLog -Value ("`n===== DEVIN desktop run " + (Get-Date).ToString("s") + " :: tauri " + $tauriCommand + " =====")
    if ($Silent) {
        # Modalita' senza console (lanciata dal .vbs): l'output di Tauri/cargo
        # non ha un terminale, quindi va TUTTO nel log — senza questo redirect
        # il debug a finestra nascosta sarebbe alla cieca.
        # NB: NON usare il redirect PowerShell (*>>): con ErrorActionPreference
        # =Stop il primo output su stderr di cargo (progresso normale) diventa
        # NativeCommandError fatale e la catena muore in silenzio (visto sul
        # campo 2026-07-16). Il redirect lo fa cmd.exe, PS non tocca gli stream.
        $silentOut = Join-Path $LogDir "tauri-dev.out.log"
        $silentErr = Join-Path $LogDir "tauri-dev.err.log"
        Write-Host "[desktop] silent mode: tauri output -> $silentOut / $silentErr"
        $proc = Start-Process -FilePath $node -ArgumentList @("`"$tauriJs`"", $tauriCommand) `
            -WorkingDirectory $HostDir -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $silentOut -RedirectStandardError $silentErr
        $code = $proc.ExitCode
    } else {
        Write-Host "[desktop] tauri output follows; this window stays open while DEVIN Desktop is running."
        & $node $tauriJs $tauriCommand
        $code = $LASTEXITCODE
    }
    Write-Host "[desktop] tauri exit code: $code"
    if ((-not $Info) -and $code -ne 0 -and $BrowserFallback) {
        Write-Host "[fallback] opening browser http://127.0.0.1:5000/app"
        Start-Process "http://127.0.0.1:5000/app" | Out-Null
        exit 0
    }
    exit $code
} finally {
    Pop-Location
}
