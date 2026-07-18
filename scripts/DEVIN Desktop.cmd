@echo off
setlocal
pushd "%~dp0\.." >nul 2>nul
if errorlevel 1 (
  echo [DEVIN] Impossibile entrare nella cartella del progetto.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\prepare-windows-desktop-host.ps1" -SkipNpmInstall
set EXITCODE=%ERRORLEVEL%
popd >nul 2>nul
if not "%EXITCODE%"=="0" (
  echo.
  echo [DEVIN] Preparazione desktop fallita. Controlla C:\Users\%USERNAME%\AppData\Local\DEVIN\logs.
  pause
  exit /b %EXITCODE%
)

start "" "%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd"
exit /b 0
