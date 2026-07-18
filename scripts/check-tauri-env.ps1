param(
    [string]$Url = "http://127.0.0.1:5000/app"
)

$ErrorActionPreference = "Stop"
$failed = $false
$repo = (Get-Location).ProviderPath

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

function Get-ToolCandidates($Name) {
    switch ($Name) {
        "node" { return @((Join-Path $env:ProgramFiles "nodejs\node.exe"), (Join-Path ${env:ProgramFiles(x86)} "nodejs\node.exe")) }
        "npm" { return @((Join-Path $env:ProgramFiles "nodejs\npm.cmd"), (Join-Path ${env:ProgramFiles(x86)} "nodejs\npm.cmd")) }
        "npx" { return @((Join-Path $env:ProgramFiles "nodejs\npx.cmd"), (Join-Path ${env:ProgramFiles(x86)} "nodejs\npx.cmd")) }
        "rustc" { return @((Join-Path $env:USERPROFILE ".cargo\bin\rustc.exe")) }
        "cargo" { return @((Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe")) }
    }
    return @()
}

function Get-IsWindowsHost {
    return [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows
    )
}

function Get-FirstLine($Value) {
    if ($null -eq $Value) { return "" }
    $text = ($Value | Out-String).Trim()
    if (-not $text) { return "" }
    return ($text -split "`r?`n" | Select-Object -First 1)
}

function Invoke-CapturedCommand($File, [string[]]$Arguments) {
    try {
        $output = & $File @Arguments 2>&1
        $exitCodeVar = Get-Variable -Name LASTEXITCODE -ErrorAction SilentlyContinue
        if ($exitCodeVar) { $code = $exitCodeVar.Value } else { $code = 0 }
        return [pscustomobject]@{ Code = $code; Output = $output; Error = "" }
    } catch {
        return [pscustomobject]@{ Code = 999; Output = @(); Error = $_.Exception.Message }
    }
}

function Test-Command($Name, $Hint) {
    $tool = Resolve-ToolPath $Name (Get-ToolCandidates $Name)
    if ($tool) {
        $result = Invoke-CapturedCommand $tool @("--version")
        $version = Get-FirstLine $result.Output
        if (-not $version) { $version = "found at $tool" }
        Write-Host "[ok] $Name - $version"
        return $true
    }
    Write-Host "[missing] $Name - $Hint" -ForegroundColor Yellow
    $script:failed = $true
    return $false
}

function Get-ExpectedTauriPackage {
    $isWindows = Get-IsWindowsHost
    $arch = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString().ToLowerInvariant()
    if ($isWindows) {
        if ($arch -eq "arm64") { return "@tauri-apps/cli-win32-arm64-msvc" }
        if ($arch -eq "x86") { return "@tauri-apps/cli-win32-ia32-msvc" }
        return "@tauri-apps/cli-win32-x64-msvc"
    }
    if ($arch -eq "arm64") { return "@tauri-apps/cli-linux-arm64-gnu" }
    return "@tauri-apps/cli-linux-x64-gnu"
}

function Test-TauriCli {
    $npmTool = Resolve-ToolPath "npm" (Get-ToolCandidates "npm")
    if (-not $npmTool) { return }
    Write-Host "[info] repo - $repo"
    $cliPackage = Join-Path $repo "node_modules/@tauri-apps/cli"
    if (-not (Test-Path $cliPackage)) {
        Write-Host "[missing] tauri cli - run npm install from the same shell that will run Tauri" -ForegroundColor Yellow
        $script:failed = $true
        return
    }
    $expectedPackage = Get-ExpectedTauriPackage
    $expectedPackagePath = Join-Path $repo ("node_modules/" + $expectedPackage)
    if (-not (Test-Path $expectedPackagePath)) {
        Write-Host "[missing] tauri platform binary - expected $expectedPackage" -ForegroundColor Yellow
        Write-Host "       node_modules was likely installed from a different OS context. Run npm install from this same shell." -ForegroundColor Yellow
        $script:failed = $true
        return
    }
    $isWindows = Get-IsWindowsHost
    $candidates = @()
    if ($isWindows) {
        $candidates += Join-Path $repo "node_modules/.bin/tauri.cmd"
        $candidates += Join-Path $repo "node_modules/.bin/tauri.ps1"
    } else {
        $candidates += Join-Path $repo "node_modules/.bin/tauri"
    }
    $lastDetail = ""
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $result = Invoke-CapturedCommand $candidate @("--version")
            $line = Get-FirstLine $result.Output
            if ($result.Code -eq 0 -and $line) {
                Write-Host "[ok] tauri cli - $line"
                return
            }
            $lastDetail = Get-FirstLine $result.Output
            if (-not $lastDetail) { $lastDetail = $result.Error }
        }
    }
    $npx = Resolve-ToolPath "npx" (Get-ToolCandidates "npx")
    if ($npx) {
        $result = Invoke-CapturedCommand $npx @("--no-install", "tauri", "--version")
        $line = Get-FirstLine $result.Output
        if ($result.Code -eq 0 -and $line) {
            Write-Host "[ok] tauri cli - $line"
            return
        }
        if ($line) { $lastDetail = $line } elseif ($result.Error) { $lastDetail = $result.Error }
    }
    if ($lastDetail.Length -gt 220) { $lastDetail = $lastDetail.Substring(0, 220) + "..." }
    if ($lastDetail) { Write-Host "[detail] tauri cli error - $lastDetail" -ForegroundColor DarkYellow }
    Write-Host "[missing] tauri cli - run npm install from the same shell that will run Tauri" -ForegroundColor Yellow
    $script:failed = $true
}

Initialize-DesktopToolPath

Write-Host "DEVIN Tauri desktop preflight"
Write-Host "================================"

[void](Test-Command "node" "Install Node.js LTS")
[void](Test-Command "npm" "Install Node.js LTS")
[void](Test-Command "rustc" "Install Rust via rustup in the same OS context used for Tauri")
[void](Test-Command "cargo" "Install Rust via rustup in the same OS context used for Tauri")
Test-TauriCli

try {
    Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
    Write-Host "[ok] backend reachable - $Url"
} catch {
    Write-Host "[warn] backend not reachable - start WSL backend first: venv/bin/python devin/ui/fast_app.py" -ForegroundColor Yellow
}

if ($failed) {
    Write-Host "Preflight found missing desktop dependencies." -ForegroundColor Yellow
    exit 1
}

Write-Host "Preflight OK. You can try: npm run desktop:dev"
