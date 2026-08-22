param(
    [ValidateSet("status", "clean-cache", "clean-legacy", "clean-all")]
    [string]$Action = "status",
    [string]$SourceRepo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).ProviderPath,
    [string]$HostDir = (Join-Path $env:LOCALAPPDATA "DEVIN\desktop-host")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "desktop-build-env.ps1")

$central = Get-DevinDefaultCargoTarget
$repoLegacy = [IO.Path]::GetFullPath((Join-Path $SourceRepo "src-tauri\target"))
$hostLegacy = [IO.Path]::GetFullPath((Join-Path $HostDir "src-tauri\target"))
$targets = @(
    [pscustomobject]@{ Name = "shared"; Path = $central; Expected = $central },
    [pscustomobject]@{ Name = "repo-legacy"; Path = $repoLegacy; Expected = $repoLegacy },
    [pscustomobject]@{ Name = "host-legacy"; Path = $hostLegacy; Expected = $hostLegacy }
)

function Show-CacheStatus($Item) {
    if (-not (Test-Path -LiteralPath $Item.Path)) {
        Write-Host ("[{0}] absent - {1}" -f $Item.Name, $Item.Path)
        return
    }
    [void](Assert-ExactDesktopPath $Item.Path $Item.Expected)
    $measure = Get-ChildItem -LiteralPath $Item.Path -File -Recurse -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum
    $gib = [math]::Round(([double]$measure.Sum / 1073741824), 3)
    Write-Host ("[{0}] {1} GiB, {2} file - {3}" -f $Item.Name, $gib, $measure.Count, $Item.Path)
}

function Remove-ExactCache($Item) {
    if (-not (Test-Path -LiteralPath $Item.Path)) { return }
    $safePath = Assert-ExactDesktopPath $Item.Path $Item.Expected
    if ($Item.Name -eq "repo-legacy") {
        $tracked = & git -C $SourceRepo ls-files -- src-tauri/target
        if ($LASTEXITCODE -ne 0 -or $tracked) {
            throw "Pulizia rifiutata: src-tauri/target contiene file tracciati o git non e' disponibile."
        }
    }
    Write-Host "[remove] $safePath"
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

if ($Action -in @("clean-cache", "clean-all")) {
    Remove-ExactCache $targets[0]
}
if ($Action -in @("clean-legacy", "clean-all")) {
    Remove-ExactCache $targets[1]
    Remove-ExactCache $targets[2]
}

foreach ($item in $targets) { Show-CacheStatus $item }
