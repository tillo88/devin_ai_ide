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

## Prossima ripresa: sequenza esatta

1. Push su GitHub (`origin main`, 6 commit locali) se non già fatto.
2. Avvio backend nativo: `.venv-win\Scripts\python devin\ui\fast_app.py` —
   verificare `/app`, pick_folder nativo, connessione rig (192.168.1.100).
3. Aggiornare `scripts/devin-tauri-dev.ps1`: avviare il backend nativo invece
   che via WSL (il launcher WSL resta finché non verificato l'equivalente).
4. Verifica manuale browser di `Continue` (chat continuity) e Apply/Reject
   (punto 10 della sequenza del 2026-07-20, mai ancora fatto).
5. Riprendere P0/P1 della roadmap 2026-07-20 (PR #1, default fail-open
   `orchestrator.py:99` → `review`).

## Note per l'ambiente di lavoro Cowork

- La cartella collegata è `D:\devin_ai_ide`; la sandbox shell Linux la monta e
  può eseguire git, pytest (smoke, Python 3.10) e script. La suite autorevole
  è quella Windows via `setup_devin_windows.ps1`/`.venv-win`.
- Claude non può digitare nel terminale dell'owner: i comandi Windows li
  esegue l'owner, l'output va letto da `logs\setup_windows.log` o file simili.
- playwright/crawl4ai: opzionali, non richiesti dalla suite; installarli in
  `.venv-win` solo quando servono (web fetch reale / ingestion avanzata).
