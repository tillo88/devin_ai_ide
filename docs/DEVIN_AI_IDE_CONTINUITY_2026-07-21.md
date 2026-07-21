# DEVIN AI IDE — continuity 2026-07-21: migrazione a Windows nativo

Data checkpoint: **2026-07-21**. Integra e aggiorna
`DEVIN_AI_IDE_CONTINUITY_2026-07-20.md` (che resta valido per architettura,
roadmap P0–P9 e Council): questo documento registra il cambio di ambiente di
lavoro e lo stato verificato.

## Decisione operativa (owner, 2026-07-21)

**La copia Windows (`D:\devin_ai_ide`) è ora l'unica copia di lavoro.**
La copia WSL (`/home/tillo/devin_ai_ide`) è **congelata**: resta come backup
finché la suite Windows nativa non è verde, poi si archivia. Nessuna modifica
va più applicata in WSL. Motivo: problemi di accesso WSL/UNC dagli strumenti di
lavoro; la roadmap prevedeva comunque l'approdo a Windows (packaging P9).

## Stato verificato al checkpoint (fatti, non inferenze)

- HEAD alla ripresa: `dde9b07` (identico a WSL); nessuna modifica persa nella
  copia (le differenze erano solo CRLF/permessi).
- Suite completa in sandbox Linux (Python 3.10, senza playwright/crawl4ai/
  torch): **419 passed, 1 skipped, 0 failed** — poi 420 col nuovo test lock.
  Conferma: il bug export ordering del 2026-07-20 è risolto nel codice attuale;
  il "profilo slim" del sidecar (PACKAGING-ROADMAP FASE 1) è validato
  sperimentalmente.
- Tutto `devin/` compila sotto Python 3.10: nessuna sintassi 3.14-only.
- Il venv WSL copiato era irrecuperabile (symlink → file 0-byte) ed è stato
  rimosso. Il nuovo venv Windows è `.venv-win` (gitignored, escluso da pytest).

## Commit di questo checkpoint

1. `7da6b6a` — `.gitattributes`: LF repo-wide, CRLF per `.cmd/.bat/.ps1`,
   binari marcati; renormalizzati 18 file legacy che avevano CRLF nel repo.
2. `2ddf4c4` — portabilità Windows:
   - `devin/core/change_manifest.py::_decision_lock`: `msvcrt.locking` su nt,
     `fcntl.flock` su POSIX (stesso contratto: non-blocking, rilascio su
     crash); regression test
     `test_change_manifest.py::test_decision_lock_blocks_concurrent_decisions_portably`;
   - `devin/ui/routers/workspace.py::_pick_folder_windows`: branch nativo
     Windows (powershell sul PATH, nessun `wslpath`).
3. `cf8c180` — `scripts/setup_devin_windows.ps1` + esclusioni `.venv-win`.

## Audit portabilità completato (grep sistematico)

- `devin/engine/runner.py`: **già** Windows-aware (`start_new_session` e
  `killpg` dietro guardia `os.name != "nt"`, fallback `proc.kill()`).
- `fast_app.run_server`: browser auto-open già corretto su nt (`_is_wsl()`
  False → `webbrowser.open`).
- `devin/ai/local_model_launcher.py`: **Linux-only by design** (CUDA,
  `LD_LIBRARY_PATH`, `lsof`, `killpg`). Nel profilo rig
  (`rig_self_hosted=false`, attivo) non lancia nulla in locale;
  `kill_server_on_port` degrada con catch ampio. Port nativo = lavoro del
  futuro "profilo LOCALE" (PACKAGING-ROADMAP FASE 0), non bloccante ora.
- `config/settings.json`: `local_models_dir` e `llama_server_path` sono path
  WSL; usati solo dal launcher → irrilevanti nel profilo rig, da rendere
  per-OS quando si farà il profilo LOCALE.

## MILESTONE RAGGIUNTA (2026-07-21, stessa giornata)

**Suite verde su Windows nativo: 420 passed, 1 skipped, 0 failed**
(Python 3.13.11, `.venv-win`, log: `logs/setup_windows.log`, exit code 0).

Il primo run nativo aveva 10 rossi, tutti riprodotti e corretti con prova:

- 9 test (patcher/orchestrator/stalled_guard): il binario GNU `patch` non
  esiste sul PATH Windows → `FileNotFoundError`/WinError 2 abortiva
  `apply_patch` PRIMA dei fallback Python. Fix `d2af042`:
  `_patch_executable()` risolve da PATH o da Git for Windows `usr/bin`;
  se assente ritorna None e si prosegue coi fallback Python.
- 1 test (vector_store): `Path.rename` su cache esistente → WinError 183.
  Fix `d2af042`: `Path.replace` (atomico e overwrite su entrambi gli OS).

Fix precedente dello stesso giorno: lo script ps1 deve essere **ASCII puro**
(`620ae03`) — PowerShell 5.1 legge i .ps1 senza BOM come ANSI e un em-dash
in una stringa diventa una virgoletta curva che rompe il parsing.

La suite Linux (sandbox, Python 3.10) resta verde a 420: le due piattaforme
sono allineate. La copia WSL è ufficialmente archiviabile.

## Sessione estesa 2026-07-21 (sera): altri task chiusi

- **Obiettivo finale dichiarato dall'owner**: app Windows installabile
  `.exe/.msi` (PACKAGING-ROADMAP FASE 1-4). Tutto il lavoro converge lì.
- **Fail-open P0 chiuso** (`77ae2e2`): `change_application_mode` default
  `review`; config assente/corrotta/valore ignoto non riattiva più
  auto-sync+auto-commit; legacy solo esplicito con warning loggato; i test
  del percorso legacy lo dichiarano nel config; regression test dedicato.
  Suite: **421 passed**.
- **Launcher Tauri aggiornato** (`3000762`): `devin-tauri-dev.ps1` avvia di
  default il backend nativo `.venv-win` (log `logs/fast_app_native.log`);
  WSL solo con `-UseWsl`, da rimuovere a verifica completata.
- **Backend reale smoke-testato via HTTP** (sandbox Linux): avvio pulito,
  `/api/health`, `/app`, `/api/desktop/readiness` tutti 200; fallback
  corretto "Rig non disponibile" senza rig raggiungibile.
- **FASE 1 packaging avviata** (`ee06120`): `scripts/backend_entry.py`
  (entry che importa fast_app come modulo: ROOT=parents[2] resta valido nel
  bundle) + `scripts/build_backend_sidecar.ps1` (PyInstaller onedir, profilo
  RIG slim, add-data templates/static/config, collect tree_sitter pack,
  hidden-imports uvicorn). NON ancora eseguito dall'owner.

## FASE 1 packaging: VERIFICATA (2026-07-21, sera)

Primo run reale dell'exe + iterazione fix, tutto committato:

- `d2af042` gia' incluso; poi dal primo avvio exe sono emersi e sono stati
  chiusi: encoding utf-8 esplicito nella lettura config (charmap, `1e584ed`),
  skip pulito modelli locali su Windows senza llama-server (`1e584ed`),
  auto-open su `/app` invece della root legacy (`d79a9a2`), dati utente del
  bundle in `%APPDATA%/DEVIN` con override `DEVIN_DATA_DIR` (`9893b9a`),
  stop dell'exe in esecuzione prima del rebuild (`4161fe3`).
- Verifica end-to-end via computer-use sulla finestra Tauri: creazione
  progetto dalla UI -> creato in
  `C:\Users\tillo\AppData\Roaming\DEVIN\workspace\test-appdata`. Bundle
  ~350 MB, exe serve `/app` senza WSL. FASE 1: done.
- Modalita' operative chiarite: sviluppo = `devin-tauri-dev.ps1` (backend
  nativo dal repo, vede i progetti in `workspace/` del repo); prodotto =
  exe sidecar con workspace utente separato in APPDATA.
- Nota UI (review SPA): rendere parlante il badge `SOURCE: UNAVAILABLE`
  (stato rig/locale + azione), base URL backend configurabile nella shell
  Tauri (stesso exe puntabile al rig multi-boot), empty-state progetti con
  CTA "Collega cartella". Il pulsante "Nuovo progetto" del command center
  prepara solo un prompt chat (richiede modello attivo): valutare se
  affiancare la creazione diretta.
- La verifica manuale di Continue e Apply/Reject resta da fare col rig
  acceso (il flusso review e' attivo e i bottoni Diff/Applica/Rifiuta sono
  presenti in UI).

## Prossima ripresa: sequenza esatta

1. Push su GitHub se ci sono commit locali non pubblicati.
2. Build sidecar (owner):
   `powershell -ExecutionPolicy Bypass -File scripts\build_backend_sidecar.ps1`
   poi verifica FASE 1: `dist\devin-backend\devin-backend.exe` parte senza
   WSL e serve `http://localhost:5000/app`. Iterare sui probabili missing
   module/hidden import leggendo `logs/build_sidecar.log`.
3. Verifica manuale browser di `Continue` (chat continuity) e Apply/Reject
   (punto 10 della sequenza del 2026-07-20, mai ancora fatto).
4. FASE 2: registrare `devin-backend.exe` come sidecar Tauri (externalBin),
   avvio/stop con l'app; rimuovere dipendenza WSL per l'uso installato.
5. FASE 3 wizard (config in `%APPDATA%\DEVIN`) e FASE 4 `tauri build`
   (.msi/.exe installer, test su macchina pulita).
6. Riprendere P1+ roadmap 2026-07-20 (PR #1 da riallineare, trust boundary).

## Note per l'ambiente di lavoro Cowork

- La cartella collegata è `D:\devin_ai_ide`; la sandbox shell Linux la monta e
  può eseguire git, pytest (smoke, Python 3.10) e script. La suite autorevole
  è quella Windows via `setup_devin_windows.ps1`/`.venv-win`.
- Claude non può digitare nel terminale dell'owner: i comandi Windows li
  esegue l'owner, l'output va letto da `logs\setup_windows.log` o file simili.
- playwright/crawl4ai: opzionali, non richiesti dalla suite; installarli in
  `.venv-win` solo quando servono (web fetch reale / ingestion avanzata).
