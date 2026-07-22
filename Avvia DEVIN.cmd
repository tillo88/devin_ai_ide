@echo off
title DEVIN AI IDE
cd /d "%~dp0"
set "EXE=src-tauri\target\release\devin-ai-ide-desktop.exe"
set "DEVIN_BACKEND_EXE=%~dp0dist\devin-backend\devin-backend.exe"

rem Chiude eventuali istanze rimaste aperte (evita copie fantasma).
taskkill /IM devin-ai-ide-desktop.exe /F >nul 2>&1

if not exist "%EXE%" (
  echo.
  echo  L'app non e' ancora compilata.
  echo  Esegui prima  "Compila DEVIN.cmd"  in questa cartella.
  echo.
  pause
  exit /b 1
)

start "" "%EXE%"
