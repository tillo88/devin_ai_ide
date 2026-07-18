# DEVIN AI IDE — continuity note (2026-07-15)

This document captures the state after the Codex-like UI, Tauri preflight, safe memory, and training/eval work. It is intentionally practical: future sessions should be able to continue without rereading the whole chat.

## 1. Workspace boundaries

There are two related but separate projects:

- DEVIN AI IDE: `/home/tillo/devin_ai_ide` on WSL distro `Ubuntu`.
- AI rig ISO build: `/home/tillo/ai-rig-iso-build` on WSL distro `Ubuntu-24.04`.

Do not mix them. DEVIN is the local coding-agent app. The ISO project is for the external multiboot rig where DEVIN, TEACHER, and HERMES will share a fourth-disk memory layer.

## 2. What DEVIN currently does

DEVIN has these main surfaces:

- FastAPI backend in `devin/ui/fast_app.py`.
- Codex-like web shell at `/app`, used as a prototyping surface while the product is still moving fast.
- Legacy dashboard at `/` and legacy chat at `/chat` kept for rollback.
- Tauri desktop scaffolding/preflight in `src-tauri/`, `package.json`, and `scripts/check-tauri-env.ps1`. This is the intended final shell: a desktop app like Codex/Claude Desktop, not a browser-first web UI.
- Project-aware chat with project selection, multiple chats, multi-file attachments, safe unknown/binary file summaries, and server-side chat persistence.
- Scaffold mode for creating projects from prompts.
- Maintenance mode through the Orchestrator loop: Planner → Coder → Patcher → Runner → Critic.
- Diff preview/apply UI foundation.
- Run log and structured events foundation.
- Mind panel with model/memory/eval state.
- Training mode foundation with cases, attempts, corrections, lessons, export, and a mini benchmark runner.

## 3. Model and hardware assumptions

Local development happens on the Windows/WSL machine. The external rig is expected to run one role at a time:

- `devin`: coding agent role.
- `teacher`: future ForgeStudio/teacher role.
- `hermes`: general/multimodal assistant role.

If the rig is not in DEVIN role, DEVIN AI IDE must fall back to local models. This is expected, not automatically a bug.

Important local/Windows rule learned during setup:

- Windows PowerShell and WSL can see different Node/Rust/Tauri environments.
- Tauri desktop is easiest when Windows Rust/Node/Tauri are installed and the backend remains WSL-hosted.
- Preflight command from Windows PowerShell rooted at the repo:

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\check-tauri-env.ps1"
```

## 4. Memory model and contamination policy

The project goal is not simply “remember everything”. The goal is complete memory with safety labels.

Memory must distinguish:

- verified success;
- verified failure;
- failed attempt;
- human correction;
- Teacher correction;
- pending review;
- infrastructure/runner error;
- hypothesis or unverified note.

Failures are useful and should be saved as failures so the agent avoids repeating them. But failed or unverified material must not be promoted into recall-safe/good memory.

Current training/eval statuses:

- `auto_success`: benchmark runner thinks the case passed; still needs Teacher/human validation.
- `auto_failure`: model/agent attempt failed; useful evidence, not contamination.
- `runner_error`: infrastructure failure, not model failure.
- `verified_success`: validated result.
- `verified_failure`: validated failure/lesson.
- `human_confirmed`: human-approved memory/lesson.

## 5. Training mode behavior

Training mode is now intended as an eval queue, not just “send this prompt to chat”.

Current flow:

1. `Seed mini bench` creates local DEVIN Mini cases.
2. `Run mini bench` runs cases in sandbox directories under `workspace/_training_runs/`.
3. Each case creates an attempt in `workspace/_training/attempts.jsonl` or project-local `.devin/training` if scoped to a project.
4. Results are recorded as `auto_success`, `auto_failure`, or `runner_error`.
5. Teacher/human validation later converts good evidence into verified corrections/lessons/SFT rows.

Known current limitation: automatic validation is still coarse. The runner can tell whether scaffold succeeded, but deeper semantic evals per case are future work.

Near-term training improvements:

- Add explicit case detail view and per-case run button.
- Add Teacher review queue.
- Add correction UI tied to an attempt id.
- Add benchmark adapters for MBPP/HumanEval/BigCodeBench/LiveCodeBench/SWE-bench Lite, with sandboxing and no silent dataset downloads.
- Add contamination guardrails: no auto-promotion from `auto_*` to good memory.

## 6. UI state

The `/app` UI is now the clean desktop workspace surface:

- left sidebar: projects, linked external folders, conversations;
- center: chat/work stream, composer, diff preview/apply foundation;
- right sidebar: compact Context/principles/quick links;
- topbar: project scope, model/memory/VRAM, command palette, Diagnostics.

Diagnostics is now the technical hub with tabbed sections: Runs, Training, Memory, Knowledge, Sandbox, and Settings. The main workspace no longer polls runs/training or renders raw run logs continuously. Project switching uses lite project overview to avoid file/knowledge scans.

Recent UI changes:

- Windows-native Tauri desktop launcher works from `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd`;
- Tauri build fixed by adding `src-tauri/icons/icon.ico`;
- backend runs headless in WSL;
- closing the desktop window triggers local model cleanup only;
- `Link` button authorizes external project folders such as ForgeStudio for crawl/sandbox;
- chat message delete, project/chat deletion, multi-file attachments, and training seed/run/export controls exist.

Product/UI direction requested by the user:

- desktop app first: `/app` is a temporary web prototype, Tauri is the destination;
- clean desktop UI like Codex/Claude Desktop;
- visual flavor inspired by the referenced Dribbble minimal agent desktop UI;
- modern but coding-focused, not decorative;
- less crowded right rail;
- eventually migrate to Tauri desktop shell.

## 7. Important commands

Start backend:

```bash
cd ~/devin_ai_ide
source venv/bin/activate
venv/bin/python devin/ui/fast_app.py
```

Run tests:

```bash
venv/bin/python -m pytest -q --capture=no
```

Check JS syntax from Windows Node if WSL Node is unavailable:

```powershell
& "C:\Program Files\nodejs\node.exe" --check "\\wsl.localhost\Ubuntu\home\tillo\devin_ai_ide\devin\ui\static\js\codex_app.js"
```

Tauri preflight from Windows PowerShell:

```powershell
cd "\\wsl.localhost\Ubuntu\home\tillo\devin_ai_ide"
powershell -ExecutionPolicy Bypass -File ".\scripts\check-tauri-env.ps1"
```

## 8. Current tested baseline

Latest verified suite result after the desktop responsiveness / diagnostics-tabs update:

```text
72 passed, 1 skipped
```

Recent relevant commits:

- `7ae9ddc Improve desktop workspace responsiveness`
- `e27d875 Fix Windows desktop launcher build`
- `3a48c13 Add desktop validation cockpit`
- `c9552ed Clean up local models when desktop closes`
- `bcf67d6 Add Windows native desktop host launcher`

## 9. Roadmap from here

### Immediate

- Restart backend after each backend/UI patch.
- Re-test `Run mini bench` after the sandbox mkdir fix.
- Improve right rail visual hierarchy and collapse secondary panels.
- Add clearer training job progress in the UI.
- Add a “Teacher review queue” for attempts.

### Short term

- Tauri desktop shell polish and make it the normal launch path.
- Treat the web UI as internal/dev fallback once the desktop app is stable.
- Unify legacy dashboard/chat/history into `/app` or keep legacy only as fallback.
- Add structured plan/action/test cards in the center work stream.
- Add file explorer/editor integration.
- Add web search mode enum: `off`, `auto`, `force`.
- Add visible memory recall/write audit per response.

### Medium term

- Shared memory disk design for DEVIN/TEACHER/HERMES.
- Role-specific local memory plus shared cross-role memory.
- Understory-style long-term memory layer if it proves useful, guarded by validation/status taxonomy.
- Public benchmark adapters and safe eval harness.
- Teacher model review loop with correction export.

### Long term

- Local agent behavior closer to Codex: project-aware, tool-using, evidence-tracked, self-correcting.
- Multi-role rig symbiosis: DEVIN codes, TEACHER validates/trains, HERMES assists broadly, all sharing reviewed memory without contamination.

## 10. Mini bench validation update — 2026-07-15 07:15

Detailed report: [`docs/TRAINING_MINI_BENCH_2026-07-15.md`](TRAINING_MINI_BENCH_2026-07-15.md).

After the sandbox mkdir fix, `Run mini bench` completed three cases. The UI initially showed `ok 3`, but manual validation found:

- `Create tested add function`: `verified_success`, pytest `5 passed`.
- `Fix off-by-one loop`: `verified_failure`, final pytest suite fails.
- `Official API only / Steam checker`: `verified_failure`, tests fail and generated code used invented/non-official Steam endpoints.

The training attempts were manually reclassified so `auto_success` does not contaminate memory. Key next task: strengthen the quality gate to run pytest/test discovery and add case-specific semantic validators.

UI direction refined: the main desktop page should be clean. Move Cognitive loop, Memory safety, Eval detectors, raw run log, detailed training, and memory audit into dedicated pages/tabs.

## 11. Teacher/Colibrì review architecture decision

The rig architecture remains DEVIN / TEACHER / HERMES as primary roles. Colibrì/GLM-5.2 is an extra offline/deep-review component, not a permanent rig role. DEVIN may call it only for batch review of benchmark artifacts and difficult cases.

Desired review stack:

1. Deterministic validators: pytest, lint, endpoint allowlists, file/diff checks.
2. TEACHER local review: fast/local correction and grading.
3. Colibrì review: slow, high-capacity semantic audit for important/doubtful cases.
4. Optional OpenAI/Claude external review: only with explicit user approval and redacted artifacts.
5. Human/Teacher promotion: only validated/rerun improvements become recall-safe memory or dataset rows.

This preserves the core memory policy: no unverified model judgment is promoted directly into good/shared memory.


## Dataset/benchmark continuity

Use [Training datasets and benchmarks](TRAINING_DATASETS_AND_BENCHMARKS.md) as the working plan for future benchmark/training ingestion.
## 13. Repository cleanup status

Root cleanup was performed conservatively. Historical docs were moved to `archive/old_docs/`; generated dumps/checksums and old test backups to `archive/old_tests/`; scattered `.orig` code backups to `archive/old_code_backups/`; local scratch/secret notes to ignored `archive/private_local/`.

Do not delete archive contents casually. They are kept for rollback/context. Future cleanup should focus on a deliberate refactor of root-level utility scripts (`launcher.py`, `clean_files.py`, `dump_progetto_ibrido.py`, `killmodels.py`) only after updating references such as `scripts/run.sh`.


## 14. Main page cleanup implementation

The main `/app` page has been simplified: raw run log and detailed cognitive/training/memory panels were removed from the primary workspace. A new `/app/diagnostics` page was added as the dedicated hub for Runs, Training Review, Memory Audit, and Settings. This matches the desktop-first direction: main view should feel like Codex/Claude Desktop, while internals live in dedicated pages.

## Strict training quality gate (2026-07-15, sera)

Implementato il "key next task" della sezione 10: il verdetto automatico del
mini bench non si fida piu' dello scaffold "riuscito".

- `Orchestrator._scaffold_quality_gate` ora SCOPRE i test reali (`test_*.py`,
  `*_test.py`, `tests/`, oltre al legacy `tests.py`) e li ESEGUE con
  `python -m pytest -q` (fallback `unittest discover`, fallback runner su
  tests.py). Il gate riporta anche `test_files`, `test_command`,
  `test_output` (coda 2000 char) come evidenza per il reviewer.
- Nuovo `devin/training/validators.py`: gli `expected_signals` dei casi
  diventano check macchina dove possibile — `file_created`, `tests_pass`,
  `tests_pass_after_fix`, `tests_or_mocks` (test verdi o mock nel codice),
  `no_invented_endpoint` (scan URL nei file scritti contro
  `metadata.allowed_url_prefixes` del caso, fallback allowlist per tag
  `steam`). Segnali non verificabili a macchina (es. `tests_fail_first`)
  restano `not_machine_checkable` per il reviewer.
- `_run_training_cases_background` in `fast_app.py`: il verdetto puo' solo
  essere DECLASSATO (auto_success -> auto_failure) se il gate tecnico e'
  `verified_failure` o se un validatore fallisce; mai promosso. L'esito dei
  validatori viene salvato in `attempt.tests.validators` e la ragione del
  declassamento in `error_reason`. Policy anti-contaminazione invariata:
  auto_* resta review-only.
- Caso "Official API only" in `benchmarks.py`: aggiunta
  `allowed_url_prefixes` esplicita (finisce in metadata via seed).
- Test: `test_training_quality_gate.py` (9 test: discovery, gate
  verde/rosso/syntax_only, endpoint inventato/ufficiale, tag fallback,
  tests_pass richiesto, segnale non verificabile).

Con questo gate i tre esiti del bench del 15/07 sarebbero stati corretti in
automatico: caso 1 auto_success (pytest verde), casi 2-3 auto_failure (suite
rossa / endpoint inventati) invece di "ok 3".

## Teacher review queue (2026-07-15, sera)

Aggiunta la coda review vera e propria sopra il quality gate:

- `TrainingStore.review_queue()`: attempt con status auto_success /
  auto_failure / runner_error / pending_review SENZA review registrata,
  newest-first, gia' arricchiti con l'evidenza per il reviewer (gate:
  status/test_command/errors; validatori: overall + verdetto per segnale;
  expected_signals del caso; run_id e artifacts).
- API: `review_queue` incluso in `/api/training/overview` (30 voci) + endpoint
  dedicato read-only `GET /api/training/review_queue?limit=N` (per Teacher/
  Colibri via script).
- UI Diagnostics (tab Training): pannello "Teacher review queue" con evidenze
  in riga, bottoni ✓ / ✕ / Fix / Infra (runner_error) che riusano
  `recordAttemptReview` (rationale + method_trace + next_action, append-only),
  bottone Log per il run. Metrica "in coda" nelle training metrics.
- Un attempt reviewato sparisce dalla coda (filtra su reviews.jsonl); nessuna
  promozione automatica, policy invariata.
- Test: 2 nuovi in `test_training_quality_gate.py` (filtro reviewed/verified,
  ordinamento newest-first, evidenze presenti). Baseline attesa: 83 passed.

## Gold tests + flywheel correzioni + adapter MBPP (2026-07-15, notte)

Tre estensioni del training sopra gate e review queue:

1. **Gold tests per caso**: i casi portano `gold_tests` (filename→sorgente) in
   metadata; il training runner li INIETTA nel sandbox prima dello scaffold e
   il quality gate li esegue coi test del modello. Chiude il buco "il modello
   si corregge i compiti da solo". Guardia anti-manomissione: se lo scaffold
   sovrascrive un gold test → auto_failure (`gold_tampered` in attempt.tests).
   I 3 casi del mini bench hanno ora contratti espliciti nel prompt (add /
   count_up_to / player_summaries_url) + gold test con discovery dinamica del
   nome modulo. `seed_cases` ora SUPERSEDE: stesso (source,title) con task
   nuovo → il vecchio caso viene ritirato (tombstone append-only in
   cases.jsonl, `retire_case`/`list_cases(include_retired=)`) e il reseed
   aggiorna il bench senza doppioni nei run.
2. **Correzioni → SFT**: `export_sft_dataset` legge SOLO corrections.jsonl —
   prima la review UI non ne creava mai, quindi l'export restava vuoto.
   Ora dopo ✕/Fix nella review queue l'editor "Correction editor" viene
   precompilato con l'attempt_id; salva via `/api/training/corrections`
   (correzione testuale + soluzione corretta opzionale che diventa la risposta
   SFT).
3. **Adapter MBPP** (`devin/training/adapters.py`): import ESPLICITO (bottone
   "Import MBPP…" con conferma, mai silenzioso), download ufficiale ~5MB in
   `_training/benchmarks_cache/`, sanity check (>=900 righe, JSON valido),
   conversione in casi con gli assert ufficiali come gold tests (namespace
   exec, nome modulo libero), prompt con reference tests (convenzione MBPP),
   source="mbpp" selezionabile nel dropdown benchmark. Endpoint:
   `POST /api/training/adapters/mbpp/import {limit, offset, force_download}`.

Test: +7 in `test_training_quality_gate.py` (gold nei builtin, supersede,
gold rosso/verde end-to-end, conversione MBPP + integrazione gate, cache senza
rete, setup_code). Baseline attesa: 90 passed, 1 skipped.

## Diagnostics hub live read-only

Aggiunta una pagina `/app/diagnostics` collegata agli endpoint reali già presenti. Mostra conteggi run, stato training, policy memoria e stato modelli/VRAM senza spostare azioni rischiose nella home. La home rimane il workspace principale, mentre Diagnostics diventa il posto naturale per log, benchmark, Teacher/Colibrì review e audit memoria.

Validazioni eseguite: `node --check devin/ui/static/js/codex_diagnostics.js` e `venv/bin/python -m pytest -q --capture=no`.

## Teacher packet export and safe Diagnostics actions

Aggiunto export `teacher_review_v1` tramite `TrainingStore.export_teacher_packet()` e API `/api/training/export_teacher_packet`. Il packet contiene caso, attempt, test, errori, artifact, correzioni note e policy di promozione. È fatto per essere letto da TEACHER/Colibrì senza contaminare memoria: `auto_success` e `auto_failure` restano review-only.

La pagina `/app/diagnostics` ora ha azioni esplicite: seed benchmark, run mini bench con conferma, export Teacher packet, export SFT e preview log run. La home `/app` resta pulita.

## Export registry

Aggiunto registro read-only degli export training: `/api/training/exports` legge i JSONL in `workspace/_training/datasets` e Diagnostics mostra gli ultimi file prodotti. Serve per continuità operativa: dopo un export Teacher packet/SFT si vede subito filename, formato, righe, size e path.

## Append-only training reviews

Aggiunto `reviews.jsonl` in TrainingStore. Le review sono append-only: non cambiano attempts/cases originali e servono come strato di verità verificata sopra output automatici. Diagnostics ora permette review rapida degli attempt recenti (`verified_success`, `verified_failure`, `needs_correction`) chiedendo una rationale. I Teacher packet includono `known_reviews`.

## Method trace nelle review

Le review training ora includono campi strutturati per metodo/evidenza: `method_trace`, `failure_mode`, `next_action`, `lesson_candidate`. Diagnostics chiede queste informazioni durante la review rapida. La policy resta append-only: metodo e lezione candidata non entrano automaticamente in memoria recall-safe.

## Command Palette MVP

Aggiunta palette `Ctrl/⌘+K` nella `/app`: focus composer, nuova chat/progetto, refresh, navigazione a Diagnostics/Training/Memory, selezione progetti e run recenti. È intenzionalmente sicura: nessuna esecuzione shell o azione distruttiva.

## Project Sandbox

Aggiunto `devin.engine.project_sandbox` e API `/api/sandbox/prepare`. Serve per far lavorare DEVIN su copie isolate di progetti veri, con manifest e policy anti-danno. Di default salta venv, segreti, node_modules, modelli/file pesanti; `include_venv=true` è possibile ma esplicito. Documentato in `docs/PROJECT_SANDBOX.md`.

## Linked venv sandbox mode

Aggiunto `link_venv` in `ProjectSandboxPolicy` e API `/api/sandbox/prepare`. Permette di creare symlink a venv esistenti per evitare copie da molti GB. Caveat importante: symlink non è read-only, quindi il manifest lo marca come dependency reference da non mutare; per `pip install`/esperimenti sulle dipendenze va creato un venv sandbox locale.

## Instructor/Crawl4AI adapters

Aggiunti contratti strutturati Pydantic compatibili con futura integrazione Instructor e adapter opzionale Crawl4AI. L'endpoint `/api/project/knowledge/crawl` usa la validazione URL anti-SSRF esistente, prova Crawl4AI se disponibile e può fare fallback basic. Documentato in `docs/AI_TOOL_ADAPTERS.md`.

## Instructor/Crawl4AI installati

Installati nel venv: `instructor==1.15.4`, `crawl4ai==0.9.2`. `pip check` pulito e `crawl4ai-doctor` completato con crawl reale. Requirements aggiornati (`requirements.txt` e `requirements/devin.txt`).

## Structured review import + crawl command

Aggiunto endpoint `/api/training/reviews/structured` per importare decisioni Teacher/Colibrì conformi a `TrainingReviewDecision`. Aggiunto comando palette “Crawl URL nella knowledge” nella `/app`, collegato a `/api/project/knowledge/crawl` con mode `auto`.

## GUI test-ready Diagnostics

Aggiunti pannelli visibili in `/app/diagnostics`: Knowledge crawl e Project sandbox. Dalla home `/app` sono presenti link rapidi a Training review, Knowledge crawl, Project sandbox e Memory audit. Obiettivo: poter partire coi test manuali senza ricordare endpoint o comandi nascosti.

## Home senza Runs + prossimo desktop launcher

Rimossi Runs e timeline dalla home `/app`; la home linka Diagnostics per run/log. Questo prepara la UX desktop pulita. Prossimo lavoro consigliato: launcher Windows/WSL per avvio backend FastAPI + Tauri, cioè esperienza doppio-click.

## Headless WSL desktop launcher

Aggiornato `scripts/devin-tauri-dev.ps1`: backend FastAPI parte headless in WSL con `nohup`, senza shell visibile, log in `logs/fast_app_headless.log`, health check su `/api/health`, poi Tauri dev. Aggiunti npm script `desktop:launch` e `backend:headless`.

## Log retention autoclean

Aggiunta retention conservativa per `logs/`: il backend FastAPI esegue una pulizia best-effort allo startup, senza mai bloccare avvio o UI. La policy default e `DEVIN_LOG_AUTOCLEAN=1`, `DEVIN_LOG_RETENTION_DAYS=14`, `DEVIN_LOG_KEEP_RECENT_RUNS=50`. Sono protetti i run attivi e gli ultimi run recenti; l apertura reale di un log via `/api/run/{run_id}/log` viene registrata in `logs/.log_access.json`, cosi la retention usa un concetto esplicito di last-opened invece di affidarsi solo ad atime filesystem. Diagnostics espone preview e cleanup manuale via `/api/logs/retention` e `/api/logs/cleanup`.

## First clickable desktop launcher

Aggiunto `scripts/DEVIN Desktop.cmd` come primo punto di ingresso doppio-click per test manuali: backend WSL headless, Tauri dev shell, browser fallback opzionale. Il preflight `scripts/check-tauri-env.ps1` ora cerca Node e Rust anche nei percorsi standard Windows (`Program Files/nodejs`, `.cargo/bin`) per ridurre i falsi missing quando PowerShell parte con PATH minimale.

## Windows-native Tauri host decision

Durante i test, Windows ha mostrato prompt di sicurezza sugli eseguibili lanciati da `\wsl.localhost`; inoltre Tauri/npm da UNC puo essere inaffidabile. Decisione: backend e source restano in WSL, ma Tauri/Rust/Node vengono eseguiti da `%LOCALAPPDATA%\DEVIN\desktop-host`, preparato da `scripts/prepare-windows-desktop-host.ps1` e lanciato da `scripts/launch-windows-desktop-host.ps1` / `scripts/DEVIN Desktop.cmd`.


La prepare del desktop host crea anche `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd`, launcher nativo consigliato dopo la prima sincronizzazione per evitare prompt/sicurezza UNC.


## Desktop close cleanup

Aggiunto hook Tauri su chiusura finestra: chiama `/api/desktop/close_cleanup`. Il backend spegne solo modelli locali tracciati dal `LocalModelLauncher`, quindi libera VRAM sul PC quando si usa DEVIN localmente ma non tocca il rig remoto. Disattivabile con `DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS=0`.


## Desktop host logs

Aggiunti log Windows-native in `%LOCALAPPDATA%\DEVIN\logs`: `desktop-launch.log` e `tauri-dev.log`. Questo evita debug a vista su warning Tauri/Windows e mantiene separati source WSL e host desktop Windows.


## Desktop readiness cockpit

Diagnostics ora mostra una sezione Desktop readiness: launcher Windows nativo, desktop host, log Tauri/backend, server locali DEVIN rilevati e policy close-cleanup. Da qui si puo anche forzare `Cleanup local models now` con conferma, utile per validare liberazione VRAM senza toccare il rig remoto.


## Desktop validation checkpoints 1-6

Aggiunto `docs/DESKTOP_VALIDATION_CHECKPOINTS.md` con percorso pratico 1→6: launcher desktop, close cleanup, UI polish, agent/diff validation, training/Teacher validation, sandbox validation. Stato reale verificato: readiness ha rilevato `coder`/`planner` locali, `/api/desktop/close_cleanup` li ha spenti e il backend e rimasto attivo su 5000.


## Desktop npm script fix

Il desktop host log ha mostrato che `npm run desktop:info` non trovava `npx` nel contesto Windows-native. Gli script npm desktop ora chiamano direttamente `tauri dev/build/info`; durante `npm run`, `node_modules/.bin` e gia sul PATH ed e piu affidabile di `npx`.


## Desktop Tauri command path fix

Il desktop host Windows non esponeva `npx` ne `tauri` nel PATH durante `npm run`. Gli script desktop ora chiamano direttamente `node_modules\.bin\tauri.cmd`, coerente con la scelta Windows-native del desktop host.


## PROSSIMO EPIC concordato: "Progetti come Claude" (2026-07-16, da Alessandro)

Direzione UX decisa, da implementare in una sessione dedicata:

1. **Cartella di lavoro nel progetto**: alla creazione (o dopo) si collega la
   cartella su cui DEVIN deve lavorare (riusa l'allowlist del bottone Link).
   Il progetto diventa: chat + knowledge + CARTELLA DI LAVORO.
2. **Sandbox trasparente**: niente piu' scheda "Project sandbox" in
   Diagnostics — quando il progetto ha una cartella collegata, la copia
   sandbox (prepare_project_sandbox, policy anti-danno esistente) si crea/
   aggiorna DA SOLA al primo run/modifica; l'utente vede solo "modifiche
   proposte -> diff -> applica all'originale". La UI base resta minimale.
3. **Stile Claude**: condividere cartelle col progetto, leggere/analizzare le
   ALTRE chat su richiesta (cross-chat, oggi c'e' solo cross-progetto via
   _detect_linked_projects), upload multipli gia' presenti.
4. Nel frattempo FATTO (2026-07-16): dropdown progetti in Knowledge/Sandbox
   (populateProjectSelects riempie il campo path; il campo libero resta per
   cartelle esterne). Fix test start_script (env opt-in nel nohup).
5. FONDAZIONE EPIC FATTA (stessa sessione): `ProjectSpace.get/set_work_dir`
   (.devin/work_dir.txt), `POST /api/project/workdir` (allowlist, 403 se non
   linkata), `work_dir` in overview e in /api/workspace/projects, `/api/run`
   instrada AUTOMATICAMENTE sulla cartella di lavoro se collegata (la
   sicurezza non cambia: sandbox engine + allowlist), card progetto mostra
   📁 nome-cartella, comando palette "Cartella di lavoro del progetto"
   (setProjectWorkDir). MANCA (prossima sessione): stesso instradamento per
   "Realizza dalla chat"/generate_patch, rimozione scheda Sandbox con flusso
   diff-back trasparente, cross-chat su richiesta, test dedicati.

## MILESTONE FUTURA: packaging "app Windows" v1 (voluta da Alessandro, 2026-07-16)

Obiettivo dichiarato: DEVIN come vera app installabile (.exe/.msi, icona,
menu Start), non più "backend WSL + finestra". Da fare DOPO stabilizzazione
funzioni (non impacchettare un bersaglio in movimento). Passi concreti:

1. **Backend come sidecar Tauri**: FastAPI impacchettato con PyInstaller in un
   exe che Tauri avvia da solo (feature sidecar nativa). L'utente non deve più
   sapere cos'è WSL né lanciare start-fastapi-headless.sh.
2. **`tauri build`** genera già .msi (WiX) / .exe (NSIS): serve solo che
   `frontendDist`/`devUrl` puntino al backend sidecar invece che a
   127.0.0.1:5000 hostato a mano. Firma opzionale + auto-update (updater Tauri).
3. **Scelta locale vs rig all'INSTALL/onboarding** (richiesta esplicita):
   wizard al primo avvio → "Hai un rig esterno?" Sì = configura IP:porta del
   rig (ruolo devin), il PC fa da solo GUI+fallback chat; No = tutto locale
   sullo stesso PC. Salva in settings.json (models.rig_self_hosted /
   remote_host già esistono: e' cablatura di UI sopra flag che ci sono gia').
   Le due modalità restano OPZIONI coesistenti, selezionabili anche dopo.
4. Modelli: in modalità rig l'exe resta leggero (parla via rete); in locale
   servono i GGUF sul PC (exe leggero + download modelli separato, mai bundle
   da GB).

## GUI: verificata DAL VIVO con computer-use (2026-07-16)

Svolta: Chrome ext non collegato, ma **computer-use** vede/controlla la
finestra Tauri (app allowlist: "devin-ai-ide-desktop.exe") → posso vedere il
render e iterare, non più alla cieca. NB: il CSS static e' CACHATO dalla
webview → le modifiche di stile richiedono **Ctrl+Shift+R** (hard reload), il
solo Ctrl+R ricarica l'HTML ma non il CSS. Polish fatti e confermati a schermo:
(1) topbar ripulita — rimosse le pillole statiche NAME/ROLE/TARGET/SHELL
(agent-card, rumore), restano progetto+sicurezza+stato live (source/mem/vram);
(2) colonna centrale flex → composer ANCORATO in basso + diff preview sotto,
hero centrato, via il vuoto a metà. Test HTML aggiornato (active-scope-label
al posto di agent-inline-card). RESTA il pezzo "vivo": far accendere i
pipe-step Plan/Code/Test/Gate durante un run reale (wiring SSE run events →
#pipeline-steps) — richiede un run per testarlo.

## GUI: pannello destro "Attività" (2026-07-16)

Feedback Alessandro: vuole le 3 colonne COME l'app desktop di Claude/Cowork
(sinistra progetti, centro lavoro, DESTRA avanzamento+cartelle+contesto) — non
il terzo pannello come menu di quick-link (sembrava un sito). Rifatto il rail
destro di /app: "Attività" con 3 sezioni — **Avanzamento** (pipeline
Plan/Code/Test/Gate + ultimo run del progetto da /api/project/last_run),
**Cartella di lavoro** (work_dir collegato + lista file + bottone Collega →
setProjectWorkDir), **Contesto attivo** (pin/knowledge/descrizione/istruzioni
che entrano nel prompt). JS `renderActivityRail(projectPath)` fetcha l'overview
COMPLETO (non-lite) + last_run, non-bloccante. CSS nuovo (pipe-step, workdir-box,
file-list, context-tags, rail-footer). Diagnostics/Knowledge relegati a un
footer discreto del rail. Test HTML aggiornato (Attività/workdir-box/context-
tags/pipeline-steps). Mockup condiviso prima di implementare. RESTA: far
"accendere" i pipe-step in tempo reale durante un run (serve wiring SSE run
events → rail), polish tema warm, e valutare 2-col option. Packaging .exe =
milestone separata (sidecar PyInstaller).

## VALIDAZIONE LIVE 2026-07-16 (run mbpp reale, ~13 casi prima dell'OOM)

Il modello vero CONFERMA che i sistemi nuovi girano (log fast_app_headless):
- 📚 Docs iniettata nel contesto (riga ~16372);
- 🔁 Self-heal loop scatta su gate rosso (iter 1/2), registra "ancora rosso"
  quando non ripara — NON bara;
- ⚠️ Security bandit scansiona e flagga (B102 exec).
Problemi visti SOLO col modello reale + fix applicati stessa sessione:
1. self-heal non riparava perche' il CODER LOCALE andava in ReadTimeout (VRAM
   al limite/OOM). Non e' un bug del loop: sul rig sparira'. Conferma che il
   collo di bottiglia e' il locale, non la logica.
2. bandit flaggava i NOSTRI gold test (exec voluto) → security_critic ora salta
   i file `test_gold_*` (non sono codice del modello). +1 test.
3. docs faceva FETCH LIVE anche per algoritmi puri (spreco/latenza sul locale
   gia' al limite) → in run_scaffold il live-fetch e' gated su intent
   API/libreria (_api_kw); cache/pinned si usano comunque offline.
Baseline confermata: batch da 10 finche' non c'e' il rig (24GB WSL non bastano
per la serializzazione modelli locale).

## Epic Progetti — cross-chat (2026-07-16, in corso)

- `ProjectSpace.search_chats(query, exclude_chat_id)` +
  `build_cross_chat_context()`: cerca snippet rilevanti in TUTTE le chat del
  progetto (match per termini, deterministico), con da quale chat vengono.
- `_build_project_context` in fast_app: se il messaggio ha intent esplicito
  (`_wants_cross_chat`: "cosa avevamo detto", "nell'altra chat"...) inietta il
  contesto cross-chat, escludendo la chat corrente (chat_id passato da api_chat).
  Gated per non fare rumore ad ogni messaggio.
- Test: +3. MANCA dell'epic: sandbox trasparente/diff-back (i run su work_dir
  girano gia' lì ma senza il flusso "copia→diff→applica" esplicito — pezzo
  grosso, richiede test live, prossima sessione), condivisione cartelle in UI.

## Fetcher Crawl4AI-first + YouTube transcript (2026-07-16)

Chiarito (Alessandro): TinyFish/SearXNG = layer RICERCA (chi da' i link),
requests/Playwright/Crawl4AI = layer FETCH (come leggi la pagina). Non sono
fallback l'uno dell'altro.

- `web_search.fetch_page_smart(url)`: escalation Crawl4AI (JS+markdown, best
  per doc) → requests → Playwright (se pagina magra). `search_docs_context()`
  = search + fetch_page_smart, usato dalla docs-cache al posto di
  search_coding_context (che resta per gli errori). Tutto fail-soft.
- `devin/ai/youtube_tools.py`: `get_transcript(url)` via youtube-transcript-api
  (sottotitoli, NON i frame — vision inutile per coding). API
  `POST /api/youtube/transcript` (opz. save_to_docs → docs cache come fonte
  web). Install: pip install youtube-transcript-api.
- Test: +4 (escalation crawl→requests, crawl-ricco-no-fallback, yt id parse,
  yt fail-soft senza pacchetto).

## Docs: INTERNET-FIRST con cache TTL (2026-07-16 — roadmap punto 4)

REVISIONE su feedback Alessandro: NO parco doc hardcoded (diventa gigante e
stantio). Strada primaria = internet; la cache e' solo uno strato TTL davanti
al fetch live, piccola e fresca.

- `devin/core/docs_cache.py`: voci con `source` ("pinned"=fallback offline
  curato, non scade | "web"=fetch live, TTL 7gg, potata da prune_expired).
  `resolve_context(task, web_fetcher, allow_web)`: 1) pota scadute; 2) se cache
  fresca matcha -> usala (no re-fetch); 3) altrimenti FETCH LIVE via
  web_search.search_coding_context, cache con TTL, usa; 4) offline -> solo
  pinned che matchano. build_context/match/add_doc/remove_doc restano.
- `run_scaffold` usa resolve_context col fetcher web reale (allow_web da
  settings web_search.enabled). Antidoto endpoint inventati SENZA parco statico.
- API: GET /api/docs_cache/list, POST /api/docs_cache/{add,remove} (rooted a
  workspace/ via DOCS_CACHE_ROOT). `scripts/seed_docs_cache.py` = 1 sola doc
  pinned Steam (fallback offline per rig-devin senza rete), non un catalogo.
- Test: +6 (add/match/context, remove, live-first + no-refetch, offline
  fallback pinned, prune TTL). Manca (UI): pannello Diagnostics docs +
  crawl4ai→web-doc. Roadmap integrazioni COMPLETA; restano epic Progetti
  (diff-back/cross-chat) e packaging .exe.

## Loop mode + self-heal scaffold (2026-07-16 — roadmap punto 3)

- `devin/core/loop_runner.py`: astrazione PURA `run_loop(action, verifier, ...)`
  = goal + azione iterata + verifica + stop (streak di N verdi / max iter /
  time budget / stop cooperativo). Zero dipendenze da modelli → testabile
  senza GPU, riusabile (scaffold, futuro coverage/docs-sweep/quality-streak).
  Guardie da harness-engineering. Ritorna LoopOutcome con traccia; NON tocca
  memoria (decide il chiamante).
- Primo consumer: **self-heal scaffold** in `run_scaffold`. Se il quality gate
  e' rosso, `_scaffold_heal_loop` rigenera i file di IMPLEMENTAZIONE (non i
  test) passando al Coder l'output dei test falliti come feedback, ri-verifica,
  finche' verde o budget. Colpisce la classe #1 del batch MBPP
  (own_tests_failed 22/46: consegna con suite rossa). Config:
  `coder.self_heal_loop` (default on), `coder.self_heal_max_iterations` (2).
- Test: +5 (4 loop runner puri, 1 integrazione heal con coder finto che
  corregge un add() sbagliato → gate da rosso a verde).
- NB: ogni iterazione = altra generazione del Coder → piu' tempo/VRAM. Con
  local serialization pesa; sul rig sara' trasparente. Tenere iters basse (2).
- BUG latente scoperto e fixato: il gate riusava il .pyc STALE quando un file
  corretto aveva stessa dimensione+mtime della versione buggata (self-heal
  istantaneo) → test rossi a codice giusto. Fix: gate pytest gira con
  PYTHONDONTWRITEBYTECODE=1 + -p no:cacheprovider (nessun bytecode = nessuna
  staleness). Vale anche per run reali veloci.

## Security critic (bandit) + repo map (2026-07-16 — roadmap punti 2 e repo map)

- `devin/engine/security_critic.py`: scan bandit OFFLINE dei .py scritti dal
  run (MEDIUM+ solo, cap 20 finding). DECISIONE: bandit e NON semgrep —
  semgrep scarica le regole dal registry online, anti local-first. Policy
  anti-rumore: i finding NON bocciano il gate, diventano
  `quality_gate.security_warnings` (evidenza per il reviewer) + log ⚠️ nel
  run. Fail-open dichiarato senza bandit. Install: pip install bandit.
- `devin/core/repo_map.py`: repo map stile Aider via `ast` (zero deps) —
  un file per riga, firme classi/funzioni top-level, non-.py solo per nome.
  `ContextEngine.build` la antepone al contesto (~1/8 del budget, scala col
  max_chars): i file esclusi dal budget smettono di "non esistere" per il
  modello (causa storica di import/firme inventate con ctx 8K).
- Test: +5 (2 repo map, 3 security; i bandit-test si auto-skippano senza
  pacchetto).

## Tree-sitter syntax critic (2026-07-16 — roadmap integrazioni, punto 1)

- Nuovo `devin/engine/syntax_critic.py`: verifica sintattica deterministica
  multi-linguaggio. `.py` via compile(), `.json` via json.loads (zero deps),
  il resto (js/ts/rust/go/c/cpp/java/ruby/php/bash/html/css/toml/yaml) via
  tree-sitter (`tree-sitter-language-pack`, opzionale). Linguaggio non
  verificabile = fail-open DICHIARATO (checked=False), mai blocco cieco.
- Quality gate: OGNI file scritto passa dal critic (prima solo compile Python
  — un .js rotto passava inosservato).
- `run_scaffold`: sintassi rotta viene RIGETTATA prima di scrivere il file,
  con riga/colonna nel messaggio → va al Critic via _self_heal (pattern
  "reject and feed back" del tree-sitter structural critic).
- Install: `pip install tree-sitter tree-sitter-language-pack` (aggiunti a
  requirements.txt). Test: +4 in test_training_quality_gate.py (il test JS
  si auto-skippa se il pack non e' installato).
- Batch review 2026-07-16 completato: 107 review, 45 correzioni (concetto +
  soluzione ufficiale MBPP), pass rate ~53%. Pipeline: generate_claude_reviews
  → import_structured_reviews → generate_mbpp_corrections.
- PROSSIMO dalla roadmap: repo map tree-sitter per context_engine, semgrep
  nel gate, poi loop mode.

## Home Claude-like + resume batch + scope store (2026-07-16)

- **Home `/app`**: con chat vuota niente piu' finto messaggio assistant —
  hero centrale (saluto, sottotitolo col progetto attivo, 3 suggerimenti
  cliccabili che precompilano il composer). Sparisce al primo messaggio
  (`appendChatMessage` rimuove `.chat-hero`). Rimosse le chip Plan/Code/Test
  e il banner runs; **diff preview collassata** in `<details>` (stessi ID,
  JS invariato). CSS in coda a codex_app.css (tema warm). Test aggiornato
  (`chat-hero` + `collapsible-panel` al posto del banner).
- **Resume batch training**: `/api/training/run` accetta `skip_attempted`
  (la UI lo chiede con un secondo confirm) → riparte dai casi senza attempt.
  Nato dal batch MBPP 104 casi morto a 62 per riavvio PC.
- **Scope store visibile** in Diagnostics/Training: label `store: globale` o
  `store: progetto X` (con `?project_path=` la pagina guarda lo store del
  progetto — sembrava dati persi, erano nello store globale).
- Baseline MBPP misurata (68 attempt, gate severo): **~49% auto_success**
  (33/68). E' il numero da battere post-LoRA.
- Launcher senza console: `DEVIN Desktop (silenzioso).vbs` (output in
  %LOCALAPPDATA%\DEVIN\logs\tauri-dev.log).

## Desktop host: fix rebuild completo ad ogni avvio (2026-07-15, notte)

`prepare-windows-desktop-host.ps1` faceva Remove-Item + Copy-Item dell'intera
`src-tauri` a ogni lancio, cancellando anche `src-tauri\target` (build cache
Rust): cargo rifaceva fetch/compilazione completa a ogni avvio del desktop.
Ora la sync usa `robocopy /MIR /XD target node_modules`: copia solo i file
cambiati, preserva la cache → dal secondo avvio la build e' incrementale
(secondi, non minuti). Il primo avvio dopo il fix ricompila ancora una volta
(la cache era stata azzerata dall'ultimo prepare vecchio), poi basta.

## 12. Desktop responsiveness update — 2026-07-15

- Preferred launcher: `C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop.cmd`.
- Repo-side launcher delegates to the Windows-native host instead of running Tauri from UNC.
- Tauri output streams live during first build; if Rust compiles crates, the shell is busy but not stuck.
- Main workspace refresh is intentionally light: `/api/mind/status` + `/api/workspace/projects`; heavy run/training/model diagnostics live in Diagnostics tabs.
- Crawl/sandbox preserve the allowlist: external folders must be linked explicitly from Workspace `Link` before use.
