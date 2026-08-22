param(
    [string[]]$Bundles = @("nsis", "msi"),
    [string]$SourceRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath,
    [string]$HostDir = (Join-Path $env:LOCALAPPDATA "DEVIN\desktop-host"),
    [string]$ArtifactsDir = (Join-Path (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath "dist\windows"),
    [switch]$SkipNpmInstall,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "desktop-build-env.ps1")

$Bundles = @($Bundles | ForEach-Object { $_.ToLowerInvariant() } | Select-Object -Unique)
foreach ($bundle in $Bundles) {
    if ($bundle -notin @("nsis", "msi")) { throw "Bundle non supportato: $bundle" }
}
if ($Bundles.Count -eq 0) { throw "Seleziona almeno un bundle: nsis oppure msi." }

$dirtyLines = @(& git -C $SourceRepo status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw "Impossibile leggere lo stato Git del repository." }
$sourceDirty = $dirtyLines.Count -gt 0
if ($sourceDirty -and -not $AllowDirty) {
    throw "Release rifiutata: il repository contiene modifiche non committate. Usa -AllowDirty solo per una build di prova."
}

$node = Join-Path $env:ProgramFiles "nodejs\node.exe"
if (-not (Test-Path -LiteralPath $node)) {
    $command = Get-Command node -ErrorAction SilentlyContinue
    if (-not $command) { throw "node.exe non trovato." }
    $node = $command.Source
}

Write-Host "DEVIN Windows thin-client release"
Write-Host "================================="
& (Join-Path $PSScriptRoot "build-frontend-bundle.ps1") -SourceRepo $SourceRepo
& (Join-Path $PSScriptRoot "prepare-windows-desktop-host.ps1") `
    -SourceRepo $SourceRepo -HostDir $HostDir -SkipNpmInstall:$SkipNpmInstall

$target = Initialize-DevinDesktopBuildEnvironment -SourceRepo $HostDir
$tauri = Join-Path $HostDir "node_modules\@tauri-apps\cli\tauri.js"
if (-not (Test-Path -LiteralPath $tauri)) {
    throw "Tauri CLI assente nel desktop host: rilancia senza -SkipNpmInstall."
}

Write-Host "[cache] $target"
Write-Host "[build] bundle: $($Bundles -join ', ')"
$buildStartedUtc = [DateTime]::UtcNow.AddSeconds(-2)
Push-Location -LiteralPath $HostDir
try {
    $arguments = @($tauri, "build", "--ci", "--bundles") + $Bundles
    & $node @arguments
    if ($LASTEXITCODE -ne 0) { throw "tauri build fallita (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}

$expectedArtifacts = [IO.Path]::GetFullPath((Join-Path $SourceRepo "dist\windows"))
$resolvedArtifacts = [IO.Path]::GetFullPath($ArtifactsDir)
if ($resolvedArtifacts -ine $expectedArtifacts) {
    throw "ArtifactsDir rifiutata: atteso $expectedArtifacts"
}
if (Test-Path -LiteralPath $resolvedArtifacts) {
    $safeArtifacts = (Resolve-Path -LiteralPath $resolvedArtifacts).ProviderPath
    if ($safeArtifacts -ine $expectedArtifacts) { throw "ArtifactsDir risolta fuori destinazione." }
    Remove-Item -LiteralPath $safeArtifacts -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $resolvedArtifacts | Out-Null

$artifacts = @()
foreach ($bundle in $Bundles) {
    $extension = if ($bundle -eq "nsis") { "*.exe" } else { "*.msi" }
    $bundleDir = Join-Path $target ("release\bundle\" + $bundle)
    $matches = @(Get-ChildItem -LiteralPath $bundleDir -Filter $extension -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -ge $buildStartedUtc })
    if ($matches.Count -eq 0) { throw "Nessun installer $bundle trovato in $bundleDir" }
    foreach ($file in $matches) {
        $destination = Join-Path $resolvedArtifacts $file.Name
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        $copied = Get-Item -LiteralPath $destination
        $artifacts += [ordered]@{
            bundle = $bundle
            file = $copied.Name
            bytes = $copied.Length
            sha256 = (Get-FileHash -LiteralPath $copied.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
}

$package = Get-Content -Raw -LiteralPath (Join-Path $SourceRepo "src-tauri\tauri.conf.json") | ConvertFrom-Json
$commit = (& git -C $SourceRepo rev-parse HEAD).Trim()
$manifest = [ordered]@{
    schema = "devin_windows_release_v1"
    product = $package.productName
    version = $package.version
    source_commit = $commit
    source_dirty = $sourceDirty
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    architecture = "x86_64-pc-windows-msvc"
    thin_client = $true
    bundled_backend = $false
    bundled_models = $false
    artifacts = $artifacts
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $resolvedArtifacts "build-manifest.json") -Encoding UTF8

Write-Host "[ok] release: $resolvedArtifacts"
foreach ($artifact in $artifacts) {
    Write-Host ("  {0}: {1} ({2:N2} MiB)" -f $artifact.bundle, $artifact.file, ([double]$artifact.bytes / 1048576))
}
