@echo off
title Compila DEVIN
cd /d "%~dp0"

echo ========================================================
echo   Compilazione DEVIN AI IDE (app desktop + installer)
echo ========================================================
echo.

echo [1/3] Backend sidecar (PyInstaller)...
powershell -ExecutionPolicy Bypass -File "scripts\build_backend_sidecar.ps1"
if errorlevel 1 goto :err

echo.
echo [2/3] Frontend bundle (UI)...
".venv-win\Scripts\python.exe" "scripts\build_frontend_bundle.py"
if errorlevel 1 goto :err

echo.
echo [3/3] App desktop release + installer (.msi/.exe)...
call npm run desktop:build
if errorlevel 1 goto :err

echo.
echo  Fatto. Ora avvia con  "Avvia DEVIN.cmd".
echo  L'installer e' in  src-tauri\target\release\bundle\.
echo.
pause
exit /b 0

:err
echo.
echo  Compilazione interrotta da un errore (vedi sopra).
echo.
pause
exit /b 1
