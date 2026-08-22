# Shared build environment for the Windows-native DEVIN thin client.
# Keep Cargo output outside every source checkout and desktop-host mirror.

function Get-DevinDesktopRoot {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA non disponibile: impossibile collocare la cache desktop DEVIN."
    }
    return [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "DEVIN"))
}

function Get-DevinDefaultCargoTarget {
    return [IO.Path]::GetFullPath((Join-Path (Get-DevinDesktopRoot) "build-cache\cargo-target"))
}

function Test-PathInsideRoot([string]$Path, [string]$Root) {
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\")
    return $fullPath.StartsWith($fullRoot + "\", [StringComparison]::OrdinalIgnoreCase)
}

function Initialize-DevinDesktopBuildEnvironment([string]$SourceRepo) {
    $target = if ([string]::IsNullOrWhiteSpace($env:DEVIN_CARGO_TARGET_DIR)) {
        Get-DevinDefaultCargoTarget
    } else {
        [IO.Path]::GetFullPath($env:DEVIN_CARGO_TARGET_DIR)
    }

    if (-not [string]::IsNullOrWhiteSpace($SourceRepo)) {
        $source = [IO.Path]::GetFullPath($SourceRepo)
        if (Test-PathInsideRoot $target $source) {
            throw "CARGO target rifiutato: la cache non puo' stare dentro il checkout ($target)."
        }
    }

    New-Item -ItemType Directory -Force -Path $target | Out-Null
    $env:CARGO_TARGET_DIR = $target
    $env:CARGO_INCREMENTAL = "0"
    return $target
}

function Assert-ExactDesktopPath([string]$Path, [string]$ExpectedPath) {
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    $fullExpected = [IO.Path]::GetFullPath($ExpectedPath).TrimEnd("\")
    if ($fullPath -ine $fullExpected) {
        throw "Percorso rifiutato: atteso '$fullExpected', ricevuto '$fullPath'."
    }
    if (Test-Path -LiteralPath $fullPath) {
        $resolved = (Resolve-Path -LiteralPath $fullPath).ProviderPath.TrimEnd("\")
        if ($resolved -ine $fullExpected) {
            throw "Percorso risolto fuori destinazione: '$resolved'."
        }
    }
    return $fullPath
}
