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

## Sessione estesa 2026-07-21 (notte): profilo LOCALE + rimozione progetti

- **Rimozione progetti** (`9338fea`): `/api/workspace/projects/remove` —
  interni nel cestino `workspace/_trash` (mai delete permanente), collegati
  solo scollegati; UI con x e conferme differenziate; 3 regression test.
- **Profilo LOCALE Windows completo**: launcher config-aware con chiavi
  per-OS `llama_server_path_windows`/`local_models_dir_windows`
  (settings gia' puntate), kill porta netstat/taskkill, log in APPDATA
  (`f0...`/`6009650`); llama.cpp **b10075** CUDA 13.3 installato e
  verificato in `%LOCALAPPDATA%\DEVIN\llama.cpp` (pin in version.txt,
  script idempotente, aggiornamenti solo deliberati con -Force/-Tag);
  i GGUF (24 GB: Ornith 9B Q8, Qwen coder, planner MoE) gia' su
  `D:\devin_ai_ide\devin\devin_models`.
- **Policy rig-first del launcher** (owner): "Rig up? niente locale.
  Rig down? apri locale." — probe `/health` del rig a startup e a ogni
  run; rig sano -> nessun modello locale (VRAM libera, source='rig');
  rig giu' -> fallback locale automatico. Il locale caricato NON viene
  scaricato da solo quando il rig torna (release manuale). 2 test.
- Suite: **429 passed**.
- Training modello piccolo: confermato che passa SOLO dal percorso P6
  (training store -> review -> SFT export verificato), mai dalla memoria
  grezza AutoMem. Fine-tuning sul rig; QLoRA locale come piano B.
- Da verificare alla prossima sessione: avvio backend con rig giu' ->
  Ornith locale carica davvero; poi il solito giro diff->Applica.

## Architettura finale dichiarata dall'owner (2026-07-21, notte)

**Backend principale SUL RIG** (Linux, always-on col ruolo DEVIN: server +
web app online quando il rig e' online, accesso esterno senza VPN via
TeamViewer/Raspberry). **Il PC principale usa solo l'exe desktop**: frontend
pulito che all'avvio cerca il backend — rig raggiungibile? si usa quello,
niente processi locali; rig giu'? parte il backup locale (sidecar +
llama-server locale). `fast_app` resta Linux-first per il deploy sul rig;
il bundle Windows e' SOLO il fallback d'emergenza.

Implementato in `src-tauri/src/main.rs` (`e0328b1` + `4990a15`): discovery
rig-first (env `DEVIN_RIG_URL`, default `192.168.1.100:5000`, formato
host:porta senza schema), navigate della finestra sul backend scelto,
spawn invisibile del backup locale con `DEVIN_NO_BROWSER=1` (`05b51b2`),
stop alla chiusura solo del backend avviato dall'app — il rig non viene
mai spento dalla GUI. NON ancora compilato/provato: prima `tauri dev`
compilera' il Rust nuovo (possibile aggiustamento su
`WebviewWindow::navigate` a seconda della minor di Tauri 2 in uso).

**MILESTONE (stessa notte): primi installer prodotti.** `npm run
desktop:build` con icona registrata (`8289bfb`) genera
`bundle/msi/DEVIN AI IDE_0.1.0_x64_en-US.msi` e
`bundle/nsis/DEVIN AI IDE_0.1.0_x64-setup.exe`. Il Rust nuovo
(discovery rig-first + sidecar) compila pulito. Gli installer contengono
SOLO il frontend: il backend bundle (350 MB) non e' ancora incluso.

Avanzamento successivo (stessa notte, `79c7b40`..`27c8feb`):

- **deploy rig pronto**: `scripts/rig/install_devin_backend.sh` (Ubuntu
  24.04) — .venv-rig + requirements core, `rig_self_hosted=true`, unit
  systemd `devin-backend` al boot con restart. Da eseguire sul rig quando
  l'owner avra' chiuso i test modello/contesto/cache;
- **rig_url configurabile**: `%APPDATA%\DEVIN\desktop.json` (creato col
  default al primo avvio; priorita' env > file > default). Pre-wizard;
  la UI grafica del wizard resta FASE 3;
- **installer autosufficiente**: bundle.resources copia `dist/devin-backend`
  accanto all'app — prerequisito: `build_backend_sidecar.ps1` prima di
  `npm run desktop:build`. NON ancora ricompilato/testato: prima build
  verifica serde_json + navigate + resources; poi test dell'installer su
  macchina pulita e firma (FASE 5).

App release gia' verificata dall'owner: parte da sola col backup locale,
solo finestra, zero browser ("yeeees partita subito").

## Sessione 2026-07-21 (parte 4): merge timezone, UI, Context Steward

- **Branch remoto integrato**: `fix/timezone-normalization-europe-rome`
  (6 commit, autore esterno) revisionato = update pulito, non regressione;
  merge --no-ff in main (`time_service` UTC-canonico + Europe/Rome, run_events
  con campi tz e `ts` legacy stabile, tzdata nei requirements e nel bundle
  sidecar). File disgiunti dal lavoro locale, zero conflitti.
- **UI badge SOURCE onesto** (`94fb110`): rig/locale/offline con host, probe
  rig cache-ato (TTL 10s) per non spammare gli endpoint pollati.
- **UI empty-state progetti** (`b2a27d1`): CTA dirette Crea/Collega quando non
  ci sono progetti; hero "Nuovo progetto" ora crea davvero (non serve modello).
- **Context Steward** (audit + piano + scaffold):
  - `docs/CONTEXT_STEWARD_PLAN.md`: verdetto = formalizzazione di
    `chat_continuity.py` (gia' ~60%) dentro P4/P5, non componente nuovo; piano
    6 fasi CS0..CS5 con DoD, nessuna dipende dalla successiva per dare valore;
    long-term NON e' un componente Steward (e' AutoMem).
  - **CS0** (`1fd44c0`): `devin/core/context_steward.py` - supervisore
    deterministico GPU/LLM-free: macchina di pressione con isteresi (no
    flapping), cooldown + min_pressure_drop + cap per task (rompe il loop
    riassumi->supera soglia->riassumi), loop guard a fingerprint, resume JSON.
    Config `context_steward` in settings.json (conservativa, DA CALIBRARE).
    11 test.
  - **CS1** (`12f3365`): `devin/core/evidence_archive.py` - archivio
    content-addressed SHA-256 (NVMe-ready): il checkpoint tiene solo
    riferimenti (claim+status+evidence_id), mai il corpo. Idempotente,
    retrieval byte-for-byte con integrity check, tamper detection, provenance
    con UTC+local (time_service). make_ref non setta 'verified' da solo. 7 test.
  - Suite: **454 passed**, 1 skipped.
- Prossime fasi Steward: CS2 retrieval ibrido (lookup esatto + strutturato +
  semantico su VectorStore esistente), CS3 pannello osservabile, CS4
  compattazione LLM a confine (unico pezzo con inferenza), CS5 stabilita' KV.

## Sessione 2026-07-22: Context Steward CS2/CS3 + pulizia repo

- **CS2** (`12f3365`... in realta' assorbito): `evidence_retriever.py` -
  retrieval ibrido (lookup esatto/strutturato/keyword), fragment() bounded
  mai file interi. 5 test.
- **Coordinatore** + **CS3** (`f0693d3`, `04b676c`): `steward_coordinator.py`
  (snapshot derivato, 6 test) + `GET /api/steward/status` read-only + badge
  pannello fail-soft. e2e test. Suite **466 passed**.
- **NOTA REPO IMPORTANTE**: un commit esterno bulk `c688186 "DEVIN WINDOWS"`
  (GitHub Desktop) ha aggiunto ~5174 file di build-cache Rust
  (`src-tauri/target/`) al repo E ha assorbito i miei file CS0/CS1/CS2. Ho
  aggiunto `src-tauri/target/` e `src-tauri/gen/` a `.gitignore` e li ho
  untrackati (`Cargo.lock` resta). ATTENZIONE: `git rm -r --cached` sul mount
  D: e' patologicamente lento (espansione 5174 pathspec, minuti e crash);
  la via veloce e' `git ls-files -z <dir> | git update-index -z
  --force-remove --stdin` (0.05s). La build-cache resta comunque nella STORIA
  (commit c688186): se serve ripulire la history, usare git-filter-repo da
  Windows nativo (non dal mount). Non urgente.
- CS4 (compattazione LLM) e CS5 (stabilita' KV) restano: richiedono il modello
  vivo (rig/llama locale) e osservazione owner. Da wire nel chat loop dove gira
  gia' chat_continuity (chat.py ~L435).

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
