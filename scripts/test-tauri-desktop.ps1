param(
    [string]$SourceRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "desktop-build-env.ps1")
$target = Initialize-DevinDesktopBuildEnvironment -SourceRepo $SourceRepo
$cargo = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
if (-not (Test-Path -LiteralPath $cargo)) {
    $command = Get-Command cargo -ErrorAction SilentlyContinue
    if (-not $command) { throw "cargo.exe non trovato." }
    $cargo = $command.Source
}

Write-Host "[cache] $target"
& $cargo test --manifest-path (Join-Path $SourceRepo "src-tauri\Cargo.toml")
exit $LASTEXITCODE
