# DEVIN Desktop Launcher — prossimo step

Obiettivo: passare da web UI/Tauri dev a esperienza “doppio click”.

## Stato attuale

- Backend FastAPI: WSL Ubuntu, `venv/bin/python devin/ui/fast_app.py`.
- GUI moderna: `http://127.0.0.1:5000/app`.
- Diagnostics: `http://127.0.0.1:5000/app/diagnostics`.
- Tauri scaffold: presente, punta alla `/app` servita da FastAPI.

## Target UX

Doppio click su launcher/app Windows:

1. controlla se `http://127.0.0.1:5000/api/health` risponde;
2. se non risponde, avvia backend in WSL con `wsl.exe -d Ubuntu --cd /home/tillo/devin_ai_ide --exec venv/bin/python devin/ui/fast_app.py`;
3. aspetta health check;
4. apre Tauri su `/app`;
5. mostra errore leggibile se WSL/backend non parte.

## Implementazione consigliata

### Fase A — script Windows launcher

Creare `scripts/devin-desktop-launcher.ps1`:

- no comandi distruttivi;
- health check HTTP;
- start backend via `Start-Process wsl.exe ...`;
- timeout 30-60s;
- apre `npm run desktop:dev` in dev, o binario Tauri in build.

### Fase B — Tauri sidecar/command

Quando lo script è stabile:

- integrare avvio backend come command Tauri controllato;
- mantenere fallback manuale;
- log in `%LOCALAPPDATA%/DEVIN/logs` o equivalente.

### Fase C — build

- `npm run desktop:build`;
- test doppio click;
- documentare prerequisiti: WSL Ubuntu, Rust, Node, venv, browser/Crawl4AI.

## Regola

Il launcher può avviare backend e GUI. Non deve applicare patch, cancellare file o lanciare benchmark automaticamente.

## Implementato: launcher headless dev

Script: `scripts/devin-tauri-dev.ps1`

Comportamento:

1. controlla `http://127.0.0.1:5000/api/health`;
2. se il backend non risponde, avvia FastAPI in WSL headless con `nohup`;
3. scrive log in `logs/fast_app_headless.log`;
4. aspetta health check;
5. avvia Tauri dev con `npm run desktop:dev`;
6. opzionalmente apre browser fallback con `-BrowserFallback`.

Comandi:

```powershell
npm run desktop:launch
npm run backend:headless
powershell -ExecutionPolicy Bypass -File ./scripts/devin-tauri-dev.ps1 -BrowserFallback
```

Nota: il backend resta in background. Per fermarlo usare un endpoint/stop futuro oppure terminare il processo Python in WSL.

### Log retention

Il backend headless ora convive con una retention conservativa dei log. Default: autoclean attivo, 14 giorni, ultimi 50 run sempre conservati. Variabili: `DEVIN_LOG_AUTOCLEAN`, `DEVIN_LOG_RETENTION_DAYS`, `DEVIN_LOG_KEEP_RECENT_RUNS`. La pagina Diagnostics mostra preview e cleanup manuale.

### Primo launcher doppio-click

Aggiunto `scripts/DEVIN Desktop.cmd`: da Esplora file usa `pushd` per gestire anche path UNC WSL, avvia `scripts/devin-tauri-dev.ps1`, tiene il backend WSL headless e usa browser fallback solo se Tauri non parte. Comandi npm utili: `npm run desktop:preflight`, `npm run desktop:open`, `npm run desktop:launch`.

### Windows-native desktop host

Per evitare problemi Windows Security / npm / Tauri da `\wsl.localhost`, il percorso consigliato per la prima GUI desktop e ora `scripts/launch-windows-desktop-host.ps1` o `scripts/DEVIN Desktop.cmd`. Lo script prepara un host nativo in `%LOCALAPPDATA%\DEVIN\desktop-host`, copiando `package.json`, `package-lock.json`, `src-tauri` e gli script minimi. Backend e codice restano in WSL; Tauri/Rust/Node girano da path Windows reale.


### Native launcher copy

`prepare-windows-desktop-host.ps1` crea anche `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd`. Dopo la prima preparazione, quello e il launcher consigliato per evitare del tutto il doppio-click da `\wsl.localhost`.


### Close cleanup

La chiusura della finestra Tauri chiama un cleanup locale: se sono stati avviati model server locali, vengono spenti per liberare VRAM. Il rig remoto non viene toccato.


### Desktop host logs

Il launcher Windows-native scrive log persistenti in `%LOCALAPPDATA%\DEVIN\logs`: `desktop-launch.log` per il flusso PowerShell e `tauri-dev.log` per output/warning Tauri/npm. Usarli come prima fonte quando la GUI parte ma mostra warning.


### Diagnostics readiness cockpit

La pagina Diagnostics espone ora `Desktop readiness`: launcher consigliato, host Windows, log, server locali rilevati e cleanup locale manuale. Usarla come checklist dopo ogni test del desktop launcher.


### npm/Tauri script note

Gli script desktop usano `tauri dev/build/info` direttamente invece di `npx --no-install tauri ...`, per evitare problemi PATH con `npx` nel desktop host Windows-native.


### Tauri command path

Nel desktop host Windows-native gli script npm chiamano direttamente `node_modules\.bin\tauri.cmd` per evitare dipendenze implicite da PATH, `npx` o npm shim.
