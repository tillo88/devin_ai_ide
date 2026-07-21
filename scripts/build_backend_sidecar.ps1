# build_backend_sidecar.ps1 - PACKAGING-ROADMAP FASE 1: backend come exe.
# Solo ASCII (PowerShell 5.1 legge i .ps1 senza BOM come ANSI).
#
# Uso (PowerShell, dalla root del repo, dopo setup_devin_windows.ps1):
#   powershell -ExecutionPolicy Bypass -File scripts\build_backend_sidecar.ps1
#
# Produce: dist\devin-backend\devin-backend.exe (onedir, profilo RIG slim).
# Log completo: logs\build_sidecar.log
#
# Verifica dopo la build (FASE 1 definition of done: parte SENZA WSL):
#   dist\devin-backend\devin-backend.exe
#   -> browser su http://localhost:5000, oppure: curl http://127.0.0.1:5000/api/health

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "build_sidecar.log"
"=== DEVIN build sidecar $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Set-Content $Log

function Step($msg) {
    Write-Host ">> $msg" -ForegroundColor Cyan
    ">> $msg" | Add-Content $Log
}

$VenvPy = Join-Path $RepoRoot ".venv-win\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    $msg = "ERRORE: .venv-win non trovato. Esegui prima scripts\setup_devin_windows.ps1"
    Write-Host $msg -ForegroundColor Red
    $msg | Add-Content $Log
    exit 1
}

Step "Installo/aggiorno PyInstaller in .venv-win"
& $VenvPy -m pip install -q -U pyinstaller 2>&1 | Add-Content $Log

Step "Build devin-backend (onedir): puo' richiedere alcuni minuti"
& $VenvPy -m PyInstaller --noconfirm --clean --onedir --name devin-backend `
    --distpath dist --workpath build `
    --add-data "devin\ui\templates;devin\ui\templates" `
    --add-data "devin\ui\static;devin\ui\static" `
    --add-data "config\settings.json;config" `
    --collect-all tree_sitter_language_pack `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    scripts\backend_entry.py 2>&1 | Add-Content $Log
$BuildExit = $LASTEXITCODE

$Exe = Join-Path $RepoRoot "dist\devin-backend\devin-backend.exe"
"" | Add-Content $Log
"=== ESITO: pyinstaller exit code $BuildExit ===" | Add-Content $Log
if (($BuildExit -eq 0) -and (Test-Path $Exe)) {
    $sizeMb = [math]::Round((Get-ChildItem (Split-Path $Exe) -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
    "Bundle: $sizeMb MB" | Add-Content $Log
    Write-Host ""
    Write-Host "BUILD OK: $Exe ($sizeMb MB)" -ForegroundColor Green
    Write-Host "Verifica FASE 1 (senza WSL): lancia l'exe e apri http://localhost:5000/app"
} else {
    Write-Host ""
    Write-Host "BUILD FALLITA (exit $BuildExit). Log: logs\build_sidecar.log" -ForegroundColor Red
}
