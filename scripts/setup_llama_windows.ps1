# setup_llama_windows.ps1 - Profilo LOCALE: llama-server nativo Windows.
# Solo ASCII (PowerShell 5.1 legge i .ps1 senza BOM come ANSI).
#
# Uso: powershell -ExecutionPolicy Bypass -File scripts\setup_llama_windows.ps1
#
# Scarica l'ultima release precompilata CUDA di llama.cpp (niente build
# locale) e la installa in %LOCALAPPDATA%\DEVIN\llama.cpp - il path gia'
# configurato in config/settings.json (llama_server_path_windows).
# GPU target: RTX 5070 Ti (Blackwell) -> serve una build CUDA 12.8+.
# Log: logs\setup_llama.log

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "setup_llama.log"
"=== DEVIN setup llama.cpp Windows $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Set-Content $Log

function Step($msg) {
    Write-Host ">> $msg" -ForegroundColor Cyan
    ">> $msg" | Add-Content $Log
}

$Dest = Join-Path $env:LOCALAPPDATA "DEVIN\llama.cpp"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

Step "Cerco l'ultima release di llama.cpp (GitHub API)"
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" -UseBasicParsing
} catch {
    $msg = "ERRORE: API GitHub non raggiungibile: $($_.Exception.Message)"
    Write-Host $msg -ForegroundColor Red
    $msg | Add-Content $Log
    exit 1
}
"Release: $($release.tag_name)" | Add-Content $Log
Step "Release trovata: $($release.tag_name)"

# Asset principale: build Windows x64 CUDA. I nomi cambiano nel tempo
# (es. llama-bNNNN-bin-win-cuda-cu12.4-x64.zip): match tollerante.
$mainAsset = $release.assets | Where-Object {
    $_.name -match "win" -and $_.name -match "cuda" -and $_.name -match "x64" -and
    $_.name -match "\.zip$" -and $_.name -notmatch "cudart"
} | Sort-Object name -Descending | Select-Object -First 1
# Runtime CUDA (cudart) separato, se pubblicato.
$cudartAsset = $release.assets | Where-Object {
    $_.name -match "cudart" -and $_.name -match "win" -and $_.name -match "\.zip$"
} | Sort-Object name -Descending | Select-Object -First 1

if (-not $mainAsset) {
    $names = ($release.assets | ForEach-Object { $_.name }) -join "`n"
    "ERRORE: nessun asset win+cuda+x64 trovato. Asset disponibili:`n$names" | Add-Content $Log
    Write-Host "ERRORE: asset CUDA Windows non trovato, vedi logs\setup_llama.log" -ForegroundColor Red
    exit 1
}

Step "Scarico $($mainAsset.name) ($([math]::Round($mainAsset.size/1MB,0)) MB)"
$mainZip = Join-Path $env:TEMP $mainAsset.name
Invoke-WebRequest -Uri $mainAsset.browser_download_url -OutFile $mainZip -UseBasicParsing

Step "Estraggo in $Dest"
Expand-Archive -Path $mainZip -DestinationPath $Dest -Force
Remove-Item $mainZip -Force

if ($cudartAsset) {
    Step "Scarico runtime CUDA: $($cudartAsset.name)"
    $cudartZip = Join-Path $env:TEMP $cudartAsset.name
    Invoke-WebRequest -Uri $cudartAsset.browser_download_url -OutFile $cudartZip -UseBasicParsing
    Expand-Archive -Path $cudartZip -DestinationPath $Dest -Force
    Remove-Item $cudartZip -Force
}

# llama-server.exe puo' stare nella radice o in una sottocartella dello zip.
$serverExe = Get-ChildItem -Path $Dest -Recurse -Filter "llama-server.exe" | Select-Object -First 1
if (-not $serverExe) {
    Write-Host "ERRORE: llama-server.exe non trovato nello zip estratto" -ForegroundColor Red
    "ERRORE: llama-server.exe non trovato in $Dest" | Add-Content $Log
    exit 1
}
$expected = Join-Path $Dest "llama-server.exe"
if ($serverExe.FullName -ne $expected) {
    # Porta tutto il contenuto della sottocartella alla radice: le DLL devono
    # stare accanto all'exe.
    Get-ChildItem -Path $serverExe.DirectoryName | Move-Item -Destination $Dest -Force
}

Step "Verifica: llama-server --version"
& $expected --version 2>&1 | Select-Object -First 3 | Add-Content $Log
$VerExit = $LASTEXITCODE
"=== ESITO: exit $VerExit ===" | Add-Content $Log

if (($VerExit -eq 0) -and (Test-Path $expected)) {
    Write-Host ""
    Write-Host "INSTALLATO: $expected" -ForegroundColor Green
    Write-Host "Config gia' pronta (llama_server_path_windows in settings.json)."
    Write-Host "I modelli GGUF sono gia' in devin\devin_models. Riavvia il backend per usare il profilo locale."
} else {
    Write-Host "Verifica fallita (exit $VerExit). Log: logs\setup_llama.log" -ForegroundColor Yellow
}
