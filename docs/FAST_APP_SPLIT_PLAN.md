# Piano split `fast_app.py` (baseline 2026-07-18)

Stato: `devin/ui/fast_app.py` = 3.521 righe, **85 route** (tutte `@app.*`, zero
router), nessun WebSocket/middleware/exception handler. Un solo startup hook
(`_startup_log_retention_cleanup`, L122). Suite baseline allo studio: 235
passed, 1 skipped.

Obiettivo: estrarre `APIRouter` tematici **senza cambiare nessun path** (il
frontend ha ~34 literal `/api/...` hardcoded in JS/template — niente prefix,
niente rinomine) e senza rompere i test che monkeypatchano i privati di
`fast_app`.

## Gruppi endpoint (85 totali)

| Gruppo | N | Note |
|---|---|---|
| runs (core orchestrator) | 13 | include 3 SSE (`/stream/{id}`, events/stream, chat SSE esclusa) + `active_runs` |
| projects | 18 | knowledge, chats, pins, debug_context, export |
| training | 15 | CRUD + `/api/training/run` (runner background ENTANGLED) |
| chat | 7 | `/api/chat` SSE; vision/document/search chiamano `api_chat` direttamente |
| status | 4 | health, readiness, mind, models/info |
| plan_terminal | 4 | 2 stub |
| explorer | 3 | file browse/read/save |
| workspace | 3 | pick_folder (MUTA `_ALLOWED_ROOTS`), projects, new |
| models_desktop | 3 | models/status, kill, close_cleanup (legge active_runs + training jobs) |
| pages | 5 | Jinja HTML |
| knowledge_misc | 4 | youtube, docs_cache ×3 |
| diff | 2 | preview/apply |
| autocomplete | 2 | 1 SSE |
| sandbox | 1 | prepare |
| misc | 1 | favicon |

## Stato mutabile condiviso (MAI duplicare — singleton cross-router)

- `active_runs` / `runs_lock` (L97-98) — letto da runs, SSE, close_cleanup,
  logs retention, plan/current, project/last_run, index page.
- `_run_events` (RunEventStore), `_project_spaces` (cache deliberata),
- `_ALLOWED_ROOTS` / `_LINKED_PROJECT_ROOTS` (mutati da pick_folder),
- `_training_jobs` / lock, accessor lazy `_get_ai_client` / `_get_launcher` /
  `_get_autocomplete` / `_get_automem`.

→ Modulo condiviso `devin/ui/state.py` (o re-export da fast_app) da creare
nella PRIMA fetta che tocca uno di questi.

## Ordine di estrazione (una fetta = un commit, suite verde a ogni passo)

1. ~~**knowledge_misc + sandbox**~~ — FATTO (fetta split 1, 2026-07-18):
   5 handler in `devin/ui/routers/knowledge_misc.py`, move puro, suite verde,
   verifica e2e via TestClient. Lezione: FastAPI 0.139 include lazy (rischio 7).
2. ~~**explorer**~~ — FATTO (fetta split 2, 2026-07-18): 3 handler + helper
   explorer-only (`_scan_project_files`, `_read_file_content`) in
   `devin/ui/routers/explorer.py`. `_safe_under_allowed` RESTA in fast_app
   (condivisa + importata dai test) importata lazy dagli handler; identita'
   di `_ALLOWED_ROOTS` preservata. Pattern stabilito: MAI import top-level da
   fast_app nei router (circolo fatale se il router e' importato per primo) —
   solo lazy import dentro le funzioni. Verifica e2e TestClient: traversal
   respinto, workspace consentito, save atomico con .bak.
3. ~~**training CRUD**~~ — FATTO (fetta split 3, 2026-07-18, `f880178`):
   14 handler + `_training_jobs`/lock + helper in `routers/training.py`.
   `/api/training/run` + runner background RESTANO in fast_app e importano lo
   stato dal router (direzione sicura fast_app -> router); il busy check di
   `api_desktop_close_cleanup` legge lo stesso snapshot. Test aggiornati nella
   stessa fetta (monkeypatch sul modulo router; asserzione source-text legge
   ora il sorgente del router).
4. ~~**workspace**~~ — FATTO (fetta split 4, 2026-07-18, `b518a12`):
   3 handler + `_pick_folder_windows` in `devin/ui/routers/workspace.py`.
   Import lazy di `WORKSPACE_DIR` / `_LINKED_PROJECT_ROOTS` /
   `_register_allowed_root` da fast_app (single-owner della allowlist);
   nessun test da aggiornare (nessun test importa questi handler).
   Verifica e2e TestClient: crea/lista/nome-vuoto OK.
5. ~~**models_desktop**~~ — FATTO (fetta split 5, 2026-07-18, `7d5e948`):
   3 handler in `devin/ui/routers/models_desktop.py`. Helper
   (`_known_local_model_servers`, `_shutdown_known_local_model_servers`,
   `_rig_self_hosted`, `_get_launcher`) e stato run-core RESTANO in fast_app,
   risolti lazy a call time: i 6 test che monkeypatchano fast_app.* valgono
   invariati; re-export shim dei 3 handler in fast_app; `threading.Timer`
   via attributo di modulo (la patch muta il modulo globale condiviso).
   Asserzione source-text aggiornata: legge il sorgente del router.
6. ~~**status**~~ — FATTO (fetta split 6, 2026-07-18, `8fabfc5`):
   4 handler (health, desktop/readiness, mind/status, models/info) in
   `devin/ui/routers/status.py`. `_desktop_windows_paths` (readiness-only) si
   sposta col router; helper condivisi e ROOT/LOG_DIR restano in fast_app
   (lazy a call time); re-export shim dei 4 handler. Asserzione source-text
   aggiornata: readiness -> router status, env-var close-kill -> router
   models_desktop.
7. ~~**diff**~~ — FATTO (fetta split 7, 2026-07-18, `2f8ab67`): 2 handler +
   `DiffRequest` in `devin/ui/routers/diff.py`. Zero dipendenze da fast_app
   (patcher importato diretto), nessuno shim, nessun test toccato; rimosso
   l'import `apply_patch` inutilizzato in fast_app.
8. ~~**autocomplete**~~ — FATTO (fetta split 8, 2026-07-18, `97cb39d`):
   2 handler (1 SSE) + request models in `devin/ui/routers/autocomplete.py`.
   `_get_autocomplete` resta in fast_app (accessor lazy condiviso); nessuno
   shim, nessun test toccato.
9. ~~**plan_terminal**~~ — FATTO (fetta split 9, 2026-07-18, `509eaa7`):
   4 handler (2 stub) + request models in `devin/ui/routers/plan_terminal.py`.
   `TerminalRequest` resta definito anche se inutilizzato (move verbatim);
   `active_runs`/`runs_lock`/`LOG_DIR` lazy da fast_app; nessuno shim.
10. ~~**pages + favicon**~~ — FATTO (fetta split 10, 2026-07-18, `76c984e`):
    5 pagine Jinja + favicon in `devin/ui/routers/pages.py`. `templates`
    (Jinja2Templates) e gli accessor restano in fast_app (lazy); re-export
    shim dei 6 handler; rimosso HTMLResponse inutilizzato da fast_app.
11. ~~**projects**~~ — FATTO (fetta split 11, 2026-07-18, `c7d381f`): 18
    endpoint ProjectSpace in `devin/ui/routers/projects.py`. Cache
    `_project_spaces` + accessor, allowlist, `_read_upload_limited`,
    `_build_project_context`/`_detect_linked_projects` restano in fast_app
    (lazy); `_validate_public_url` (SSRF) si sposta col router; shim di
    `api_project_last_run`; 2 test aggiornati (import SSRF + asserzioni
    source-text crawl).
12. ~~**runs_read**~~ — FATTO (fetta split 12, 2026-07-18, `aa375e3`): fetta A
    del runs core — 8 endpoint di sola lettura (runs/active, runs,
    logs/retention, logs/cleanup, run/{id}/events, events/stream SSE,
    run/{id}/log, stream/{id} SSE) in `devin/ui/routers/runs_read.py`.
    `_run_events`/`active_runs`/`runs_lock`/`LOG_DIR` restano in fast_app
    (lazy); shim di `api_run_events`; asserzione source-text aggiornata.
13. ~~**runs_core**~~ — FATTO (fetta split 13, 2026-07-18, `0656f66`): fetta B
    del runs core — nucleo mutante (run, run/resume, chat/scaffold, stop +
    RunRequest/ResumeRequest) in `devin/ui/routers/runs_core.py`. Tutte le
    dipendenze (Orchestrator, LOG_DIR, _run_events, ...) restano in fast_app,
    lazy import a call time ANCHE dentro le closure _bg (leggono lo stato
    monkeypatchato al thread-run time come i global originali). Shim di
    handler + model: test_state_persistence e /api/chat (chiama
    api_chat_scaffold direttamente) invariati, zero test toccati.
14. ~~**chat**~~ — FATTO (fetta split 14, 2026-07-18, `9892e41`): sezione chat
    completa (ChatRequest, /api/chat SSE, vision, document, search, history
    ×3) + helper chat-only (_detect_mode, _is_scaffold_request,
    _wants_web_search, _is_trivial_message, _build_search_query,
    _requires_verified_web_sources, _scaffold_web_reference) + blocco upload
    (MAX_*, _looks_textual, _truncate_attachment_text,
    _format_chat_upload_for_context, _read_upload_limited) in
    `devin/ui/routers/chat.py`. `/api/chat` chiama `api_chat_scaffold` via
    import top-level router→router da runs_core; dipendenze condivise lazy da
    fast_app; shim di api_chat/ChatRequest/4 helper (test + lazy import da
    projects.py). Rimossi import inutilizzati (base64, hashlib,
    StreamingResponse, UploadFile/File/Form, web_search, document_extract,
    eval_recorder). Zero test toccati. `/api/chat/generate_patch` RESTA in
    fast_app (runner background intrecciato col core runs, come
    /api/training/run). Smoke e2e TestClient 11/11.
15. ~~**Fase finale**~~ — FATTO (fetta split 15, 2026-07-18, `1ef6a8c`):
    SPLIT COMPLETO. Le ultime 2 route del main module si ricongiungono ai
    loro router: `/api/training/run` + `_run_training_cases_background` ->
    routers/training.py (il runner rientra nel modulo che possiede
    `_training_jobs` + lock; run-core deps lazy da fast_app a thread-run
    time); `/api/chat/generate_patch` -> routers/chat.py (contratto
    preservato: niente `_run_events.start`, niente `_finish_run_events`).
    **fast_app.py e' ora handlers-free (871 righe, da 3521/85 route): solo
    app assembly, stato condiviso e helper.** Rimossi i back-import dello
    stato training e gli import morti (datetime, List, Request,
    ChatPersistence, TrainingStore, get_builtin_cases, validators);
    Orchestrator resta importabile (lazy import dei router + monkeypatch dei
    test). 1 test aggiornato (TrainingStore dalla sorgente canonica). Smoke
    e2e TestClient 8/8; suite 235 passed, 1 skipped — identica alla baseline.
    Dettagli delle sotto-voci:
    a. ~~projects~~ — FATTO (fetta 11, `c7d381f`).
    b. ~~runs core~~ — FATTO (fette 12-13, `aa375e3` + `0656f66`).
    c. ~~chat core~~ — FATTO (fetta 14, `9892e41` + fetta 15 per
       chat/generate_patch, `1ef6a8c`).
    d. ~~training run~~ — FATTO (fetta 15, `1ef6a8c`): `/api/training/run` +
       runner background rientrati in routers/training.py.
    Vincoli eterni: `launcher.py` importa `run_server` da fast_app — quel
    simbolo deve restare importabile per sempre; MAI rinominare path
    (34+ literal `/api/...` hardcoded nel frontend).

## Rischi silent-break (da rileggere a ogni fetta)

1. **Test che monkeypatchano `fast_app.<privato>`**: dopo lo spostamento,
   `setattr(fast_app, "Orchestrator", ...)` non ha effetto sul modulo router.
   Mitigazione: re-export shim in fast_app + lookup dei dep via state module,
   oppure aggiornare i test NELLA STESSA fetta. Coinvolti:
   `test_understory_hybrid.py`, `test_scaffold_resilience.py`,
   `test_security_regressions.py`, `test_state_persistence.py`.
2. **`test_understory_hybrid.py` L533-540 legge il SORGENTE di fast_app.py**
   e asserisce literal (`/api/desktop/close_cleanup`, `_known_local_model_servers`…):
   si rompe appena quel codice si sposta → aggiornare l'asserzione nella
   fetta che muove models_desktop.
3. Chiamate dirette handler→handler (no HTTP): `api_chat`→`api_chat_scaffold`,
   vision/document/search→`api_chat`. Chat e scaffold nello stesso modulo.
4. Path identici, sempre. Nessun router prefix.
5. SSE (5 endpoint): i generatori pollano `active_runs` sotto `runs_lock` —
   registry condiviso obbligatorio; `run_server()` (timeout_graceful_shutdown,
   `os._exit`) resta nel main module.
6. `app.mount("/static")` + startup hook restano sull'app principale.
7. **FastAPI 0.139: `include_router` è LAZY** (scoperto nella fetta 1,
   2026-07-18): `app.routes` contiene un wrapper `_IncludedRouter`, non le
   route espanse — enumerare `app.routes` per verificare lo split NON
   funziona (le route incluse "sparisco" dall'elenco pur funzionando).
   Verifica obbligatoria end-to-end via `TestClient`, mai per enumerazione.

## Protocollo per fetta (disciplina standard del progetto)

baseline → estrazione meccanica (move puro, no refactor) →
`venv/bin/python -m py_compile devin/ui/fast_app.py devin/ui/routers/*.py` →
full suite → grep mojibake su template/JS toccati → commit → nota in
`docs/CONTINUITY_2026-07-18.md`.
