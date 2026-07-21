# setup_devin_windows.ps1 — Setup ambiente DEVIN AI IDE su Windows nativo
# (migrazione da WSL, 2026-07-21)
#
# Uso (PowerShell, dalla root del repo):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_devin_windows.ps1
#
# Cosa fa:
#   1. rileva Python 3.10+ (py launcher o python.exe sul PATH)
#   2. crea il venv Windows in .venv-win (NON "venv": quello era il venv WSL)
#   3. installa i requirements core (playwright/crawl4ai opzionali, dopo)
#   4. lancia la suite pytest completa
#   5. scrive tutto in logs\setup_windows.log (leggibile da Claude in sessione)
#
# Lo script NON tocca la configurazione, NON avvia il backend, NON fa commit.

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "setup_windows.log"
"=== DEVIN setup Windows $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Set-Content $Log

function Step($msg) {
    Write-Host ">> $msg" -ForegroundColor Cyan
    ">> $msg" | Add-Content $Log
}

# --- 1. Rileva Python 3.10+ ---
Step "Cerco Python 3.10+"
$PyCmd = $null
foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "py -3", "python")) {
    try {
        $parts = $candidate.Split(" ")
        $v = & $parts[0] $parts[1..($parts.Length-1)] --version 2>&1
        if ($v -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) { $PyCmd = $candidate; "Trovato: $candidate -> $v" | Add-Content $Log; break }
        }
    } catch { continue }
}
if (-not $PyCmd) {
    $msg = "ERRORE: nessun Python 3.10+ trovato. Installa da https://www.python.org/downloads/ (3.12 o 3.13), spunta 'Add python.exe to PATH', poi rilancia."
    Write-Host $msg -ForegroundColor Red
    $msg | Add-Content $Log
    exit 1
}
Step "Uso: $PyCmd"

# --- 2. Crea venv .venv-win ---
$VenvDir = Join-Path $RepoRoot ".venv-win"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Step "Creo venv in .venv-win"
    $parts = $PyCmd.Split(" ")
    & $parts[0] $parts[1..($parts.Length-1)] -m venv $VenvDir 2>&1 | Add-Content $Log
    if (-not (Test-Path $VenvPy)) {
        "ERRORE: creazione venv fallita, vedi log." | Tee-Object -Append $Log
        exit 1
    }
} else {
    Step "venv .venv-win gia' presente, lo riuso"
}
& $VenvPy --version 2>&1 | Add-Content $Log

# --- 3. Installa requirements core ---
Step "Aggiorno pip"
& $VenvPy -m pip install -q -U pip wheel 2>&1 | Add-Content $Log

Step "Installo requirements core (playwright/crawl4ai esclusi per ora)"
$CorePkgs = @(
    "openai>=1.0.0", "requests", "numpy", "scikit-learn",
    "fastapi", "uvicorn", "python-multipart",
    "pypdf", "python-docx", "openpyxl", "python-pptx",
    "python-dotenv", "instructor",
    "tree-sitter", "tree-sitter-language-pack==0.13.0",
    "bandit", "youtube-transcript-api",
    "pytest", "httpx"
)
& $VenvPy -m pip install $CorePkgs 2>&1 | Add-Content $Log
if ($LASTEXITCODE -ne 0) {
    Step "ATTENZIONE: pip install ha segnalato errori (exit $LASTEXITCODE) — continuo comunque con la suite"
}

# --- 4. Suite pytest ---
Step "Lancio la suite completa (output in logs\setup_windows.log)"
& $VenvPy -m pytest -q -p no:cacheprovider 2>&1 | Add-Content $Log
$TestExit = $LASTEXITCODE

# --- 5. Esito ---
$summary = Get-Content $Log | Select-String -Pattern "passed|failed|error" | Select-Object -Last 3
"" | Add-Content $Log
"=== ESITO: pytest exit code $TestExit ===" | Add-Content $Log
Write-Host ""
if ($TestExit -eq 0) {
    Write-Host "SUITE VERDE. Log completo: logs\setup_windows.log" -ForegroundColor Green
} else {
    Write-Host "Suite NON verde (exit $TestExit). Log completo: logs\setup_windows.log" -ForegroundColor Yellow
}
$summary | ForEach-Object { Write-Host $_.Line }
