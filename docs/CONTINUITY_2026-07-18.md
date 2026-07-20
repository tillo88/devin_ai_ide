# DEVIN AI IDE — continuity note (2026-07-18)

Sessione di **reliability hardening** guidata dall'obiettivo "DEVIN come senior
engineer disciplinato" (vedi `Prompt.txt` alla root). Nessuna feature nuova:
solo correttezza, sicurezza, recoverability, osservabilità — con la disciplina
richiesta: baseline → debolezza concreta → cambio minimo → regression test →
verifica full suite → documentazione.

Baseline iniziale: suite NON raccoglibile (errore di collection).
Baseline finale: **143 passed, 1 skipped** (+25 test nuovi).

## Commit della sessione (in ordine)

1. **`05fff69` — Collection della suite riparata.** `test_streaming.py`
   importava `devin/ui/stream_console.py`, modulo rimosso col passaggio alla
   web UI: pytest falliva la collection di TUTTA la suite. Rimossi i riferimenti
   al console, tenuti i test su `AIClient.stream`/`stream_chat`.

2. **`81204c1` — Run-resume hijack fix + state cleanup.** Ogni `run()` nuovo su
   un progetto con stato crash-interrotto ereditava silenziosamente task, piano
   e attempt del vecchio run (`get_resume_info()` usava `load_latest()` su
   qualunque run_id) e `load_latest()` riscriveva `run_id`/`state_file`
   dell'istanza: il nuovo run clobberava il file di stato del vecchio.
   Ora il resume riguarda SOLO il run_id esplicitamente passato;
   `load_latest()` resta per gli endpoint di sola lettura.
   `StatePersistence.cleanup()` (mai chiamato prima) gira all'avvio di ogni
   `run()` e rimuove stati >24h del progetto attivo.
   Test: `test_state_persistence.py` (6).

3. **`1f3c42e` — No-progress guard + status espliciti.** Se il fallimento
   normalizzato è IDENTICO al giro precedente (caso tipico: Critic offline →
   `_self_heal` ritorna l'errore raw → il Coder rifallisce uguale), il loop si
   ferma con status `stalled` invece di bruciare tutti i retry. Errori diversi
   continuano fino a max retries. Inoltre TUTTI i return path di `run()`
   portano `"status"` esplicito: prima mancava sempre, e
   `_finish_run_events(run_id, result.get("status", "failed"))` registrava
   "failed" anche per i successi. Test: `test_stalled_guard.py` (3).

4. **`a081d7e` — VectorStore: cache JSON versionata, addio pickle.** La cache
   pickle era disabilitata di default (`DEVIN_ALLOW_UNSAFE_PICKLE_CACHE`) →
   persistenza di fatto morta: ogni run re-embeddava l'intero progetto
   (HARDENING_STATUS la segnalava come debito). Ora JSON con marker
   `devin-vector-cache/1`; i path `.pkl` legacy migrano a `.json` e il pickle
   viene cancellato SENZA mai essere letto. Dopo il load da cache, gli engine
   corpus-dipendenti (tfidf/keyword) rifittano deterministicamente gli
   embedding sui testi cached: il vocabolario deriva dal corpus, quindi gli
   embedding cached erano incompatibili con la query encoding (score
   spazzatura silenziosi). `_cosine_similarity` ritorna 0.0 su dimensioni
   diverse invece di troncare via zip. Test: +3 in `test_vector_store_e2e.py`.

5. **`630ce21` — Bypass della quarantena nel recall remoto.** Il filtro di
   `HybridMemoryClient.recall` cercava il marker `[STRUCTURED_MEMORY status=…]`
   solo sulla PRIMA riga; il recall semantico di Understory prefissa gli hit
   con `[path] `, spostando il marker oltre → memorie quarantine/pending/
   superseded entravano nel contesto. Ora lo status viene estratto ovunque nel
   testo come token intero. Memorie non marcate (appunti manuali) e marker
   senza status restano ammissibili (comportamento invariato).
   Test: +2 in `test_understory_hybrid.py`.

6. **`c8e64a4` — Allowed-root: fallimenti osservabili.**
   `_register_allowed_root` ingoiava tutto (`except: pass`): una cartella
   scelta dal picker risultava collegata ma ogni lettura finiva in 403 senza
   traccia. Ora ritorna bool, logga ogni rifiuto (`[SECURITY]`), e il picker
   aggiunge `warning` nella risposta invece di marcare la cartella linked.
   Test: +2 in `test_security_regressions.py`.

7. **`13630dc` — Scaffold: evidence tier nel reporting.** Uno scaffold senza
   test eseguibili riportava lo stesso "success" indistinto di uno con suite
   verde. `run_scaffold` ora espone `status`: `verified_success` |
   `syntax_only` | `failed` (`success` = contratto file-scritti, invariato).
   `/api/chat/scaffold` chiude gli eventi col tier e scrive `evidence: <tier>`
   nel log; il footer `status: success|failed` (letto da /api/runs e altri
   parser) NON cambia. Badge UI mappa i nuovi stati (⚠️ syntax_only,
   ⏸️ stalled). Test: +2.

8. **`47c6e57` — Resume ESPLICITO dei run interrotti (endpoint + UI).**
   Chiusura del cerchio del punto 2: `last_run` dichiarava `resumable: true`
   senza alcun percorso per farlo. Nuovo `POST /api/run/resume`: valida
   path + run_id sicuro, rifiuta stati mancanti/finiti/retry-esauriti e il
   doppio-resume di run attivi, poi rilancia `run()` con lo STESSO run_id
   (log e timeline continuano). `run()` non tronca più il log del crash:
   appende `--- Run resumed ---`. Badge progetto con bottone "▶ Riprendi"
   quando resumable. Test: +4.

9. **`7ee677e` — Silent evidence gaps.** (a) validators: file dichiarati
   scritti ma mancanti/illeggibili sparivano — `file_created` passava sul
   sottoinsieme e `no_invented_endpoint` non li scansionava; ora fanno
   fallire `file_created` con i nomi e finiscono in `skipped_files`.
   (b) unlink fallito della cache indice knowledge → warning esplicito
   (knowledge stale). (c) outbox AutoMem illeggibile → warning (memorie
   accodate mai sincronizzate). Skippati di proposito `client.py:537` e
   `web_search.py:231`: fail-soft documentati e gestiti, loggare lì è rumore.
   Test: +3.

## Decisioni architetturali prese (e perché)

- **Resume = azione esplicita dell'utente, mai implicita.** Il resume
  automatico "best effort" era la causa dell'hijack. Ora: nuovo run = stato
  nuovo; ripresa = endpoint dedicato con lo stesso run_id. È il pattern
  trust-boundary del goal: si chiede permesso ai confini di fiducia reali.
- **"success" (contratto consegna) ≠ "verified" (livello di prova).** Per gli
  scaffold senza test non si boccia la consegna (romperebbe l'uso normale) ma
  si DICHIARA il tier di evidenza ovunque tranne nei parser legacy, che
  restano compatibili.
- **Cache solo in formati non eseguibili.** Niente pickle lato progetto, per
  nessun motivo; la migrazione cancella il legacy senza leggerlo.
- **I validatori training possono solo declassare, mai promuovere**
  (policy preesistente confermata ed estesa ai file illeggibili).
- **Fail-soft ≠ silenzioso.** I percorsi degrado-grazioso restano, ma ogni
  degrado a un confine di fiducia (sicurezza, memoria, evidenza) lascia
  traccia. I fail-soft puramente interni e documentati restano muti.

## Limiti residui noti (onesti)

- `fast_app.py`: split COMPLETO (fetta 15, `1ef6a8c`, piano:
  `docs/FAST_APP_SPLIT_PLAN.md`) — 871 righe, zero route handler, solo app
  assembly + stato condiviso + helper; 15 router sotto `devin/ui/routers/`.
  Estratti: knowledge_misc (`30a9fcf`), explorer
  (`f0ac384`), training CRUD + job state (`f880178`), workspace
  (`b518a12`), models_desktop (`7d5e948`), status (`8fabfc5`), diff
  (`2f8ab67`), autocomplete (`97cb39d`), plan_terminal (`509eaa7`), pages +
  favicon (`76c984e`), projects (`c7d381f`), runs_read (`aa375e3`),
  runs_core (`0656f66`), chat (`9892e41`), runner finali training/run +
  chat/generate_patch (`1ef6a8c`).
- Il recall locale (`LocalMemoryStore`) ora ha un fallback fuzzy a trigrammi
  per query con zero overlap esatto (slice 18, `6d5fb98`): copre varianti
  morfologiche e testi misti IT/EN con vocabolario simile. NON traduce:
  parafraisi cross-language senza termini tecnici condivisi restano mancate
  (un vero embedding recall resta un passo futuro possibile, non urgente).
- Il no-progress guard rileva sia fallimenti identici consecutivi sia cicli
  A,B,A,B (periodo-2, confermato alla 4a ricorrenza — slice 15, `a269702`).
  Limite residuo: con `max_attempts=3` di default un ciclo periodo-2 non ha
  abbastanza ricorrenze per essere confermato e il run termina "failed"
  (comunque bounded); i cicli emergono con budget >= 4.
- Il resume ri-costruisce il contesto da zero (lo stato salva
  `context_length`, non il contesto): ripresa corretta ma non istantanea.
- `active_runs` (dict in-process) non viene riconciliato con `.devin_state`
  allo startup: dopo un restart i run "fantasma" compaiono come interrotti
  (corretto) ma nessuno li marca esplicitamente "crash rilevato".
- Gli stati `stalled`/`syntax_only` sono visibili in timeline/badge; i parser
  testuali del footer (`/api/runs` ecc.) vedono solo success/failed/timeout/
  stopped (compatibilità voluta).
- `orchestrator.py` (run loop) ha ancora copertura test parziale.
  (`ai/client.py` 29 test slice 13; `engine/patcher.py` + `engine/runner.py`
  37 test slice 16, `7f554a5`.)

## Stato verificato

```text
229 passed, 1 skipped, 3 warnings in ~33s
```

## Aggiornamento sera (slice 13-14, commit `fbc610f`..`67e21bf`)

- **Slice 13 — test retry/backoff `ai/client.py`** (`e725941`): 29 test in
  `test_ai_client_retry.py` che fissano il contratto documentato: backoff 2s/4s,
  MAX_RETRIES=3, 4xx NON ritentato (voluto, `client.py:533-545`), 502/503/504
  ritentati, circuit breaker open/half-open/closed, fallimenti locali NON
  conteggiati nel breaker (solo rig), stream() notice su 4xx ed esaurimento.
  Tutto il confine HTTP/WOL/sleep mockato: suite istantanea, zero side-effect.
- **Slice 14 — tree sporco pre-esistente committato** (`38e4bec`, `27c9f83`,
  `9aa7a08`, `e525812`, `c3e0625`, `67e21bf`): 6 commit tematici. Scoperta
  chiave della review: `orchestrator.py` e `fast_app.py` (già committati)
  importavano moduli untracked → **HEAD non si importava pulito**; ora sì.
  Gruppi: critics+context (syntax/security critic, loop_runner, repo_map,
  docs_cache), training pipeline (gold tests, review_queue, adapter MBPP),
  config IP rig 192.168.1.100, UI (review queue + /app redesign), desktop/
  packaging, docs. Review: zero secret, zero mojibake, nessun dump runtime.
  `Prompt.txt` lasciato untracked di proposito (prompt del goal, non docs).
  `core.filemode=false` settato: git Windows sul mount 9p vedeva mode 644
  spurie su script 755.
- Coda residua: split `fast_app.py`, recall con embedding. Copertura test
  completata (slice 16-17): patcher, runner, context_engine, repo_map,
  loop_runner tutti coperti; `orchestrator.py` resta il modulo meno coperto.

(le 3 warning: deprecazione `verify_requirements` in bandit — benigna)

## Split fast_app — fetta 14: router chat (2026-07-18, commit `9892e41`)

- Mosso in `devin/ui/routers/chat.py` (move puro, path invariati): la sezione
  chat completa — `ChatRequest`, `/api/chat` (SSE), `/api/chat/vision`,
  `/api/chat/document`, `/api/chat/search`, `/api/chat/history` ×3 — gli
  helper chat-only (`_detect_mode`, `_is_scaffold_request`,
  `_wants_web_search`, `_is_trivial_message`, `_build_search_query`,
  `_requires_verified_web_sources`, `_scaffold_web_reference`) e il blocco
  upload (costanti `MAX_*`, `_looks_textual`, `_truncate_attachment_text`,
  `_format_chat_upload_for_context`, `_read_upload_limited`).
- `/api/chat` chiama `api_chat_scaffold(RunRequest(...))` con import
  top-level router→router da `runs_core` (direzione sicura, nessun ciclo);
  vision/document/search chiamano `api_chat(req)` come chiamata locale,
  esattamente come avveniva dentro fast_app.
- Dipendenze condivise restano single-owner in fast_app, lazy import a call
  time: `_validated_project_path`, `_get_launcher`, `_get_ai_client`,
  `_get_automem` (importato a livello handler e catturato dalla closure
  SSE), `GENERAL_CHAT_PROJECT_KEY`, `_build_project_context`.
- Shim re-export in fast_app: `api_chat`, `ChatRequest`,
  `_is_scaffold_request`, `_format_chat_upload_for_context`,
  `_requires_verified_web_sources` (importati dai test),
  `_read_upload_limited` (lazy-importato da `routers/projects.py`).
  Zero test aggiornati (nessun test legge il sorgente fast_app per literal
  chat).
- Import rimossi da fast_app (provabilmente inutilizzati dopo il move):
  `base64`, `hashlib`, `StreamingResponse`, `UploadFile`/`File`/`Form`,
  web_search (`get_web_search_provider`, `format_results_as_context`,
  `fetch_top_results`), `extract_document_text`, tutto `eval_recorder`.
  `ChatPersistence` RESTA: lo usa `/api/chat/generate_patch`.
- `/api/chat/generate_patch` NON estratto: runner background intrecciato
  col core runs, stesso profilo di `/api/training/run` — restano le uniche
  2 route del main module.
- Line count: fast_app.py 1741 → **1157**; chat.py **672**.
- Verifica: py_compile OK; smoke e2e TestClient 11/11 (empty message,
  vision rifiutata con/senza immagine, history get/clear/delete, search
  delega, SSE meta/token/done con fake client, persistenza user+assistant);
  suite completa **235 passed, 1 skipped** — identica alla baseline.

## Split fast_app — fetta 15 (FINALE): split completo (2026-07-18, commit `1ef6a8c`)

- Le ultime 2 route del main module si ricongiungono ai loro router (move
  puro, path invariati):
  - `/api/training/run` + `_run_training_cases_background` ->
    `devin/ui/routers/training.py` (283 -> **509** righe): il runner rientra
    nel modulo che possiede il suo stato (`_training_jobs` + lock). Dipendenze
    run-core lazy da fast_app a thread-run time (pattern runs_core):
    `Orchestrator`, `CONFIG_PATH`, `LOG_DIR`, `_run_events`,
    `_make_run_callback`, `_finish_run_events`, `active_runs`/`runs_lock`;
    `validate_case`/`decision_reason` importati diretti.
  - `/api/chat/generate_patch` -> `devin/ui/routers/chat.py` (672 -> **778**
    righe): contratto originale preservato verbatim — NON registra
    `_run_events.start`, NON chiama `_finish_run_events`. Lazy da fast_app:
    `LOG_DIR`/`_make_run_callback`/`_validated_project_path` nell'handler,
    `Orchestrator`/`CONFIG_PATH`/`active_runs`/`runs_lock` nella closure `_bg`.
- **fast_app.py e' ora HANDLERS-FREE: 1157 -> 871 righe (da 3521 / 85 route
  al baseline).** Restano solo app assembly, stato condiviso
  (`active_runs`/`runs_lock`/`_run_events`/allowlist/accessor lazy) e helper
  condivisi (`_validated_project_path`, `_build_project_context`,
  `_make_run_callback`, `run_server`, ...).
- Rimossi i back-import dello stato training da fast_app (il runner e'
  rientrato; `models_desktop` legge `_training_job_snapshot` direttamente da
  routers.training). Import trimmati (provabilmente morti): `datetime`,
  `List`, `Request`, `ChatPersistence`, `TrainingStore`, `get_builtin_cases`,
  `validate_case`/`decision_reason`. `Orchestrator` RESTA importabile da
  fast_app (lazy import dei router + monkeypatch di test_state_persistence).
- Nessuno shim nuovo: zero consumer Python dei due handler fuori da fast_app.
  1 test aggiornato nella stessa fetta: `test_structured_training_review_...`
  importa `TrainingStore` da `devin.training.store` (era l'attributo
  incidentale `fast_app.TrainingStore`).
- Verifica: py_compile OK; smoke e2e TestClient 8/8 (training/run validation
  error + happy path FakeOrch fino a job `finished` con `auto_success=1`,
  generate_patch missing project_path / 403 fuori allowlist / nessuna
  conversazione, regressione /api/chat/history); suite completa
  **235 passed, 1 skipped** — identica alla baseline.
- Coda post-split: `orchestrator.py` resta il modulo meno coperto; token
  gate approvato per il lavoro PWA successivo (vedi "Decisioni di direzione").

## Orchestrator coverage — slice 1: run() stop/retry guards (2026-07-18, commit `b7d8f12`)

- Nuovo `test_orchestrator_run_guards.py` (4 test, ~3s): pinnati i quattro
  comportamenti stop/retry di `Orchestrator.run()` finora al buio, senza
  toccare il sorgente. Fixture autocontenute copiate da
  `test_stalled_guard.py` (stile repo), `whole_file_enabled=False`.
  1. **Timeout** (L1059-1070): status/footer/stato concordi su `timeout`.
  2. **User stop** (L1073-1084 + `stop()` L142-149): flag alzato durante il
     planner -> run fermo prima del 1° Coder call, zero chiamate coder.
  3. **Critic feedback -> retry** (L1275-1308): feedback del Critic nel
     prompt del 2° tentativo, `success` al 2° giro, progetto sincronizzato.
  4. **Critic offline bounded** (L1307-1308): warn-only, `failed` dopo
     esattamente max_retries esecuzioni del runner.
- Suite: **235 -> 239 passed, 1 skipped**.
- Coda restante: slice 2 = whole-file mode + scaffold failure wiring;
  `_sync_sandbox_to_project` exclusions; `_maybe_web_reference` per-lifetime
  quirk da pin-or-fix; resume-with-saved-plan.

## Orchestrator coverage — slice 2: whole-file mode + scaffold failure wiring (2026-07-18, commit `ad1ab92`)

- Nuovo `test_orchestrator_wholefile_scaffold.py` (6 test, ~1.5s), fixture
  autocontenute in stile repo. Due aree finora al buio, sorgente intoccato.
- **Whole-file mode** (prima ZERO copertura: ogni test `run()` esistente
  forzava `whole_file_enabled=False`):
  1. Success path (L1040-1051 + L1140-1182): `### FILE:` + fence ->
     `apply_full_files` -> runner verde -> `success`, progetto aggiornato.
  2. Empty-output guard (L1158-1163): output senza blocchi -> contratto
     esatto "You returned NO files..." in `last_error`, MAI apply. Il run
     chiude `stalled` (non `failed` come ipotizzato nel brief): errore
     identico a ogni giro -> no-progress guard al 2° fallimento.
     Composizione corretta dei due guard, NON un bug.
  3. Mode selection (`_small_project` L847-861): boundary 300/301 righe,
     fail-safe su lista vuota/eccezione; progetto grande con
     `whole_file_enabled=True` resta in unified diff.
- **Scaffold failure wiring** (il gate in se' era gia' coperto; qui le
  conseguenze):
  4. Gate rosso -> heal loop esaurito -> `failed`, entry `<quality_gate>`
     in `files_failed` (L737-743), nessun commit (L762-767).
  5. Memory polarity (L758 + L554-587): fallimento registrato
     `verified_failure`/`polarity:negative` — evidenza strutturata.
  6. Empty-plan exit (L630-641): `{"success": False, "error": "empty file
     plan"}`, zero chiamate al Coder.
- Osservazione (non bug, da valutare): i return path anticipati di
  `run_scaffold` ("empty file plan", "No models available") NON portano la
  chiave `status` che il path principale ha — i consumer devono trattarla
  come assente.
- Suite: **239 -> 245 passed, 1 skipped**.
- Coda restante: `_sync_sandbox_to_project` exclusions;
  `_maybe_web_reference` per-lifetime cap quirk (pin-or-fix);
  resume-with-saved-plan + attempt-counter; `_run_pytest_gate`
  timeout/unittest-fallback branches; heal-loop edge cases.

## Orchestrator coverage — slice 3: status-key contract fix + pin misti (2026-07-18, commit `1271529`, `fb8d330`)

- **FIX AUTORIZZATO (unico al sorgente)** — `run_scaffold` early returns
  senza `status` (osservazione della slice 2, ora risolta): TRE early
  return omettevano la chiave che il main path porta sempre
  (`failed`/`verified_success`/`syntax_only`) — "No models available"
  (L607), "local planner unavailable" (L618, terzo ramo oltre ai due del
  brief, stesso pattern one-line) e "empty file plan" (L641). Tutti e
  tre sono fallimenti pre-generazione -> `status: "failed"`, coerente col
  `failed` del main path quando nulla viene scritto. Consumer verificati:
  nessuno legge `status` dagli early return oggi (`training.py` L370-382:
  `success`/`error`/`quality_gate`; `chat.py` L761: solo `success`;
  `runs_core.py` L265-268 via `_scaffold_event_status`: `success` +
  `quality_gate`) — il fix chiude un'ambiguita' di contratto, non un bug
  attivo. `run()` e `run_from_conversation()` verificati: tutti i return
  path portano gia' `status`, nessuna incoerenza analoga. Regression
  test: aggiornato `test_scaffold_empty_plan_exits_without_generation` +
  2 nuovi (`no_models`, `local_planner_unavailable` — quest'ultimo
  richiede degraded+serialize_vram DENTRO `ensure_models`, che ricalcola
  `_degraded_mode` a L330).
- **Pin test-only** (`test_orchestrator_sync_resume.py`, 7 test, ~0.3s):
  1. Sync exclusions (`_sync_sandbox_to_project` L151-177): excluded_parts
     (`workspace`, `.devin_state`, ...) + suffixes (`.gguf`, `.rej`, ...)
     pinnate comportamentalmente; byte-compare skip L173-174: file
     identico non ritoccato (contenuto E mtime), modificato aggiornato.
  2. Resume-with-saved-plan (L892-922, L1028-1039): plan serializzato +
     attempt=2/3 -> MAI re-plan (prompt "TASK" solleva nel mock), Plan
     ricostruito da steps/raw_response, last_error nel feedback del
     Coder, un solo tentativo residuo (3/3). Nota: il banner "Resuming
     previous run" esce da `self._log` prima che esista la closure
     `log()` — nei test si cattura via `sse_callback`, non in
     `result["logs"]`.
  3. `_run_pytest_gate` (L377-416), subprocess.run finto: fallback
     unittest su "No module named pytest" (L407-410), continue su
     eccezione generica (L402-405), timeout finto via `TimeoutExpired`
     -> failed immediato "timeout dopo 180s" senza fallback ne' attese
     reali, entrambi i runner ko -> dict `last`.
  4. **QUIRK NOTO PINNATO (NON fixato)**: `_web_searches_done`
     (L230-236) e' per-vita-Orchestrator, mai resettato tra run — il cap
     `max_per_run` (default 2) si consuma a lifetime, il nome suggerisce
     per-run. Pin con commento, in attesa di decisione owner (reset
     per-run vs budget lifetime).
- Suite: **245 -> 254 passed, 1 skipped** (247 dopo il fix, +7 pin).
- Coda restante: heal-loop edge cases; decisione owner sul quirk
  web-ref per-lifetime.

## Orchestrator coverage — slice 4 (FINALE): heal-loop edge cases + outcome polarity (2026-07-18, commit `d5c24cf`)

- Ultima fetta della mappa: 20 test nuovi, sorgente intoccato. Pin in
  `test_orchestrator_wholefile_scaffold.py` (4, bare orchestrator + quality
  gate reale) e `test_scaffold_resilience.py` (16 col parametrize).
  1. **Stop mid-loop** (L520-521 + loop_runner L83-84): `_should_stop`
     alzato durante la rigenerazione -> file restanti saltati, uscita
     "stopped" al giro successivo, budget non consumato.
  2. **Pre-stopped** (L552): 0 iterazioni -> ritorna il quality dict di
     input (identita'), coder mai chiamato.
  3. **Esaurimento rosso**: ritorna il gate dell'ultima iterazione
     (verified_failure + errori pytest), senza raise — pin unitario del
     contratto di ritorno (il wiring e2e era gia' della slice 2).
  4. **Regen sintassi rotta** (L529-530): non scritta, versione precedente
     conservata, loop continua.
  5. **`_is_test_filename`** (L49-51): 10 casi (tests.py/TESTS.py, test_*,
     *_test.py, basename su path annidati; test.py e contest.py FUORI).
  6. **`_self_heal` VRAM-swap** (L200-212): degraded+serialize -> swap
     planner<-coder pre-Critic, restore coder<-planner nel finally; swap
     fallito -> errore raw, Critic mai chiamato, nessun restore. Pinnati
     anche fast-path/ensure-failure/exception di `_check_vram_and_swap`.
  7. **`_remember_scaffold_outcome` verde** (L554-587): polarity:positive,
     evidence:tests.py_exit_zero, kind:eval_result, nessun failure_type.
- Nessun bug esposto: il comportamento reale e' coerente col design
  (stop cooperativo, reject-and-keep-previous, fallback fail-soft).
- Suite: **254 -> 274 passed, 1 skipped**.
- **Copertura orchestrator ora ragionevolmente pinnata** (slice 1-4
  complete). Coda residua nota:
  1. DECISIONE OWNER sul quirk `_web_searches_done` per-lifetime
     (L230-236, pinnato in slice 3): reset per run vs budget lifetime.
  2. Lavoro differito: token gate + PWA (vedi "Decisioni di direzione").

## Training runner — declass chain pinnata (2026-07-18, commit `f2b61c0`)

- Baseline di regression che precede il batch di fix approvati: 6 test in
  `test_training_runner_declass.py` pinnano il comportamento CORRENTE di
  `_run_training_cases_background` (routers/training.py L303-452), sorgente
  intoccato. Runner chiamato sincrono, FakeOrch su `fast_app.Orchestrator`
  (pattern test_state_persistence), job registrato come `/api/training/run`.
  1. Scaffold pulito -> `auto_success` (tests dict: quality_gate /
     validators / gold_tests / gold_tampered; contatori job corretti).
  2. Gate `verified_failure` -> `auto_failure`, `"quality gate: <errori>"`.
  3. Validatori caso fail -> `auto_failure`, `"validatori caso: <reason>"`.
  4. Gold test sovrascritto -> `auto_failure`, `"gold test sovrascritti
     dal modello: <nome>"` (compare contenuto esatto vs `gold_expected`).
  5. Eccezione scaffold -> `runner_error` (infra, fuori review queue),
     job finisce comunque.
  6. **KNOWN HOLE pinnato**: crash di `validate_case` ->
     `{"overall": "unknown"}` NON declassa, attempt resta `auto_success`
     (fail-open). Il fix (2) sotto ribalta questa assert.
- Suite: **274 -> 280 passed, 1 skipped**.
- **Coda fix approvati** (prossimi slice, un commit per fix + flip pin):
  1. Gold-skip guard: i gold test iniettati nel sandbox vengono raccolti
     da pytest ma il conftest `collect_ignore` puo' bypassarli — gate
     verde possibile SENZA aver eseguito i gold.
  2. Validator-crash fail-closed: crash -> declass, non `unknown` verde
     (ribalta il pin #6).
  3. `tests_or_mocks` tightening: oggi basta un hint `mock` nel codice
     per passare senza alcun test eseguito.
  4. `add_attempt` status validation: lo store accetta qualunque stringa
     come status attempt.
  5. `skip_attempted` non deve contare `runner_error` come "attempted"
     (un caso morto per infra deve poter rigirare).
  6. `pending_review` non deve svuotare la review queue.
  7. Web-search cap reset per run (quirk `_web_searches_done`
     per-lifetime, gia' pinnato in slice 3 — decisione owner presa:
     reset per run).

## Handoff per nuova chat (2026-07-18, sera)

Punto di ripresa dopo chat lunga (~2h di lavoro goal-driven). **Nessun lavoro
della sessione hardening e' rimasto non committato**: tutti i 12 commit
(`05fff69`..`f812fa7`) sono nel branch.

⚠️ **La nota "tree sporco pre-esistente" e' SUPERATA** (slice 14, vedi
"Aggiornamento sera"): tutto committato in 6 commit tematici. L'unico
untracked voluto e' `Prompt.txt`.

### Come riprendere il goal

1. Leggere questo doc + `Prompt.txt` (obiettivo completo) + `AGENTS.md`
   (regole ambiente: repo reale in WSL `Ubuntu`, test con
   `venv/bin/python -m pytest -q --capture=no`).
2. Verificare baseline: suite deve dare `145 passed, 1 skipped`.
3. Riprendere dalla coda sotto, una fetta alla volta, con la disciplina:
   baseline → debolezza → cambio minimo → regression test → full suite →
   commit → nota qui.

### Coda prioritaria (prossime fette)

1. ~~Test retry/backoff `ai/client.py`~~ — FATTO (slice 13, `e725941`, 29 test).
2. ~~Commit del tree sporco pre-esistente~~ — FATTO (slice 14, 6 commit tematici).
3. **Split `fast_app.py`** (~3500 righe / 85 endpoint) in router: debito
   strutturale maggiore, multi-sessione, solo a piccoli passi con test verdi
   a ogni passo. **Baseline + piano pronti in `docs/FAST_APP_SPLIT_PLAN.md`**
   (2026-07-18): mappa degli 85 endpoint, stato mutabile condiviso, ordine di
   estrazione (1. knowledge_misc+sandbox, 2. explorer dietro state module,
   3. training CRUD), rischi silent-break (test che monkeypatchano privati di
   fast_app, literal nel frontend). Prossima fetta: estrazione knowledge_misc.
4. ~~Recall locale con embedding~~ — FATTO in forma fuzzy (slice 18, `6d5fb98`:
   trigrammi, non embedding vero; valutare embedding solo se il fuzzy
   risultasse insufficiente su dati reali).
5. ~~No-progress guard esteso a loop A,B,A,B~~ — FATTO (slice 15, `a269702`).
6. ~~Copertura test `engine/patcher.py`, `engine/runner.py`,
   `core/context_engine.py`, `core/loop_runner.py`~~ — FATTO (slice 16-17,
   `7f554a5` + `c980b79`: 37 + 14 + 4 test).

### Decisioni di direzione (2026-07-18, confermate dall'utente)

- **Il rig e' solo server** (modelli/AutoMem/web search): MAI fast_app sul
  rig. Backend in WSL sul PC, frontend Tauri -> backend localhost:5000,
  backend -> rig via LAN (settings.json). `rig_self_hosted=false` resta la
  configurazione supportata e testata. Gap noto da fare solo quando serve:
  URL backend configurabile nella shell Tauri (ora hardcoded
  `127.0.0.1:5000/app`) per backend su altra macchina.
- **Accesso da fuori (mobile)**: NON un'app nativa (duplicherebbe il
  frontend). Strada approvata: (1) PWA-ificare la SPA `/app` (manifest +
  service worker + pass responsive), (2) raggiungibilita' via Tailscale/
  WireGuard, non porte aperte, (3) Telegram resta il canale push/comandi
  rapidi.
- **Shared-secret token gate su fast_app**: ~~APPROVATO, da implementare
  piu' avanti~~ — IMPLEMENTATO (2026-07-18, commit `f0e5e8c`): segreto
  condiviso in settings/env, middleware che lo richiede quando l'host non
  e' loopback; loopback senza token. Dettagli e limiti noti nella entry
  datata in coda. Resta aperto solo il lato rig ("the right one" anche li')
  e la progettazione congiunta con la PWA.
- **Goal amendment (2026-07-18)**: "token gate + PWA: procedere a mia
  discrezione; dove DEVIN puo' essere reso piu' affidabile / 'piu' simile
  al modo di lavorare di Kimi' (evidence-first, separazione dei ruoli,
  loop bounded), farlo".

### Non fare

- Non riattivare il resume automatico: e' esplicito per design (endpoint
  `/api/run/resume`); il vecchio comportamento era il bug hijack.
- Non introdurre pickle per cache progetto-locali: formato JSON versionato.
- Non "promuovere" memorie: i validatori/training possono solo declassare;
  auto_* resta review-only.
- Non loggare i fail-soft documentati interni (`client.py:537`,
  `web_search.py:231`): valutato, e' rumore.

## Training runner — fix A: validator crash fail-closed (2026-07-18, commit `18d3cfe`)

- Root cause: in `_run_training_cases_background` un crash di `validate_case`
  veniva catturato in `{"overall": "unknown", "error": ...}`; la catena di
  declass reagisce solo a `overall == "fail"`, quindi il crash lasciava
  l'attempt `auto_success` (fail-open, pin #6 del baseline `f2b61c0`).
- Fix: il crash viene registrato in `validator_crash` e la catena declassa
  ad `auto_failure` SUBITO dopo la guardia tamper, prima del gate.
  error_reason: **`"validatori in errore: <exc>"`** (prefisso coerente con
  `"quality gate: "` / `"validatori caso: "` / `"gold test sovrascritti dal
  modello: "`, troncato a 600 char). L'evidenza `tests.validators` resta
  `{"overall": "unknown", "error": "validator crash: ..."}`.
- Test: pin #6 ribaltato — `test_runner_validator_crash_fails_open_known_hole`
  rinominato **`test_runner_validator_crash_fails_closed`** (auto_failure,
  reason esatta, evidenza preservata, contatori job).
- Suite: **280 passed, 1 skipped** (invariata, flip interno al pin).

## Training runner — fix B: gold test devono essere DAVVERO eseguiti (2026-07-18, commit `215ac5a`)

- Root cause (buco conftest-bypass): la guardia anti-manomissione confronta
  solo i BYTE dei gold file. Un `conftest.py` scritto dal modello con
  `collect_ignore = ["test_gold_*.py"]` (o fixture autouse di skip, o hook di
  collezione) esclude i gold dal run pytest -> gate verde -> `auto_success`
  su codice mai controllato dai gold.
- Dettaglio implementativo scoperto (decisione di design): passare i gold
  file ESPLICITAMENTE a pytest NON chiude il buco — un path esplicito bypassa
  `collect_ignore` (verificato empiricamente: exit 0, 1 passed). Serve la
  raccolta da directory (quella che il conftest ostile sabota).
- Fix: nuova `_verify_gold_tests_executed(project_path, gold_names,
  timeout=180)` in routers/training.py. Rigira pytest BARE sul sandbox
  (`sys.executable -m pytest -q -p no:cacheprovider --maxfail=20
  --junitxml=<tmp>`, cwd=sandbox, `PYTHONDONTWRITEBYTECODE=1`, timeout —
  stessa igiene di `_run_pytest_gate`) e dal report junitxml esige che OGNI
  gold file abbia >=1 testcase passato (no failure/error/skipped, match su
  classname = stem del modulo). Chiamata nella catena SOLO a would-be
  `auto_success` con `gold_expected` non vuoto (dopo tamper/crash/gate/
  validatori). Fallimento -> `auto_failure`,
  error_reason: **`"gold test non eseguiti: <detail>"`** (troncato a 800
  char; detail: `"pytest exit 5: nessun test raccolto (conftest esclude i
  gold?)"` / `"non raccolti/passati: <nomi>"` / `"timeout dopo Ns"` /
  `"pytest exit N a gold verdi (suite instabile)"`).
- Evidenza: nuova chiave `tests.gold_executed` ({executed, command,
  exit_code, missing, detail, output}).
- Test (test_training_runner_declass.py, 6 -> 10): harness `_run_sync` ora
  patcha di default `_verify_gold_tests_executed` con fake verde (i pin
  esistenti NON fanno pytest reali, specchio del gate finto via result dict);
  `gold_check="real"` usa la funzione vera.
  1. `test_runner_gold_not_executed_auto_failure` — fake rossa -> declass,
     reason esatta, evidenza registrata.
  2. `test_runner_gold_conftest_collect_ignore_auto_failure` — HOLE REPRO
     con pytest REALE: conftest `collect_ignore` -> pre-fix l'attempt restava
     `auto_success` (verificato: il test FALLIVA prima del wiring), post-fix
     `auto_failure`, exit_code 5, missing=[gold].
  3. `test_runner_gold_conftest_autouse_skip_auto_failure` — variante autouse
     skip (pytest REALE): exit 0 ma gold skipped -> declass.
  4. `test_runner_gold_real_verification_clean_auto_success` — controparte
     positiva end-to-end (pytest REALE): sandbox pulita -> auto_success.
  Pin #1 esteso: `tests["gold_executed"]["executed"] is True`.
- Suite: **280 -> 284 passed, 1 skipped**.
- Coda fix approvati RESTANTE (da `f2b61c0`, aggiornata):
  1. ~~Gold-skip guard~~ — FATTO (fix B, `215ac5a`).
  2. ~~Validator-crash fail-closed~~ — FATTO (fix A, `18d3cfe`).
  3. `tests_or_mocks` tightening: oggi basta un hint `mock` nel codice per
     passare senza alcun test eseguito.
  4. `add_attempt` status validation: lo store accetta qualunque stringa
     come status attempt.
  5. `skip_attempted` non deve contare `runner_error` come "attempted".
  6. `pending_review` non deve svuotare la review queue.
  7. Web-search cap reset per run (`_web_searches_done` per-lifetime,
     decisione owner: reset per run).


## Training/store hardening batch + web cap reset (2026-07-18, commit `345a8fd`, `531c8dc`, `cc8cc65`, `c1fd77a`, `139c61b`)

Batch dei 5 fix approvati rimasti in coda (voci 3-7 della lista sopra). Ogni
fix col suo commit e i suoi regression test; disciplina pin-then-flip dove
esisteva un pin del comportamento corrente.

1. **`tests_or_mocks` tightening (`345a8fd`).** Root cause: `MOCK_HINT_RE`
   matchava la parola "mock" (case-insensitive) in QUALUNQUE file scritto —
   README, commenti, TODO — quindi un completamento superficiale senza test
   ne' mock reali passava il segnale `tests_or_mocks`. Fix: le regex
   `MOCK_IMPORT_RE` (veri import `unittest.mock`/`from unittest import mock`/
   `import mock`) e `MOCK_USAGE_RE` (`Mock(`/`MagicMock(`/`AsyncMock(`/
   `PropertyMock(`, `patch(`, `monkeypatch`, `responses.*`, `respx.`)
   vengono cercate SOLO nei file `.py`. Decisione di policy: questo check
   non ha convenzione fail-soft (gia' falliva con motivo esplicito), quindi
   tightening a `fail` con dettaglio chiaro — approvato dall'owner.
   Test (test_training_quality_gate.py):
   `test_validator_mock_word_in_comment_does_not_pass`,
   `test_validator_mock_word_in_readme_does_not_pass`,
   `test_validator_real_unittest_mock_usage_passes`. Il test esistente
   `test_validator_accepts_official_endpoint_with_mocks` resta verde
   (passa sia sul ramo tests_run sia sul nuovo ramo mock reali).

2. **`add_attempt` status validation (`531c8dc`).** Root cause: lo store
   accettava qualunque stringa come status; un typo ("auto_succes") produceva
   un attempt invisibile a review_queue E summary. Fix: nuovo set canonico
   `ATTEMPT_STATUSES` (= SAFE_SUCCESS | FAILURE | AUTO_SUCCESS | AUTO_FAILURE
   | INFRA | {"pending_review"} — verificato contro TUTTI i producer:
   runner training L441-515, endpoint `/api/training/attempts`, test);
   `add_attempt` alza ValueError sugli sconosciuti, mirroring di
   `add_review`. L'endpoint ora cattura ValueError -> `{"error": ...}`
   (convenzione degli endpoint case/reviews) invece di 500.
   Test: `test_add_attempt_rejects_unknown_status`,
   `test_add_attempt_accepts_all_canonical_statuses` (pinna anche il set).

3. **`skip_attempted` non conta `runner_error` (`cc8cc65`).** Root cause:
   il filtro resume di `/api/training/run` saltava i casi con QUALUNQUE
   attempt, quindi un caso il cui unico attempt era `runner_error`
   (infrastruttura giu', modello mai partito) restava escluso per sempre
   dalla resume coverage. Fix: contano come "attempted" solo gli esiti
   reali (`auto_success`/`auto_failure` + verified/human statuses);
   `runner_error` (e `pending_review` senza esito) vengono ritentati.
   Test (test_training_runner_declass.py, pattern FakeRequest +
   `_training_store_for` monkeypatch + FakeThread, nessun run-core):
   `test_skip_attempted_retries_runner_error_cases`.

4. **Review `pending_review` non svuota la coda (`c1fd77a`).** Root cause:
   `review_queue` escludeva ogni attempt con QUALUNQUE review; una review
   `pending_review` (presa in carico senza verdetto) lo cancellava per
   sempre. Fix: nuovo set `VERDICT_REVIEW_STATUSES` (= REVIEW_STATUSES -
   {"pending_review"}); l'attempt esce dalla coda solo se l'ULTIMA review
   porta un verdetto — una nuova pending_review dopo un verdetto lo riapre
   (semantica "nuovo giro di review"). Test:
   `test_review_queue_keeps_attempt_with_pending_review_review`,
   `test_review_queue_verdict_review_clears_attempt`; i due
   `test_review_queue_*` esistenti restano verdi invariati.

5. **Web-search cap reset per run (`139c61b`).** Root cause (ex-quirk
   pinnato): `_web_searches_done` era un attributo d'istanza mai resettato
   da `run()`/`run_scaffold()` — il cap `max_per_run` valeva per la VITA
   dell'Orchestrator, contraddicendo il nome del config key. Fix: nuovo
   helper `_reset_web_search_budget()` chiamato come prima istruzione di
   `run()` e `run_scaffold()` (prima di qualunque early-return; nessun
   cambio di firma). FLIP del pin:
   `test_web_reference_cap_is_per_orchestrator_lifetime_not_per_run` ->
   `test_web_reference_cap_resets_per_run` (due run consecutivi sullo
   stesso oggetto ricevono ciascuno il budget pieno; cap intra-run
   invariato) + nuovo wiring test reale
   `test_run_scaffold_entry_resets_web_search_budget` (entry point vero
   via unbound-call, reset anche su early-return "no models").

- Suite: **284 -> 293 passed, 1 skipped** (+8 test nuovi fix 1-4, +2 fix 5
  di cui 1 flip rinominato). Nessun consumer inatteso emerso; nessun fix
  escluso.


## Memory write-path hardening (2026-07-18, sessione serale)

Anti-contamination policy applicata ai percorsi di SCRITTURA memoria:
niente diventa memoria permanente/recall-safe automaticamente — solo
verified/human-confirmed sono recall-safe, e i path automatici possono
coniare SOLO verified_*.

### Memory map completa (W1-W8, per il record)

- **W1** — eval/write path poteva coniare memoria recall-safe:
  `record_eval_result` accettava status arbitrari e mappava ogni non-failure
  a `polarity:positive`; `LocalMemoryStore.store` si fidava dei tag del
  caller; `validate_memory_tags` era dead code mai chiamato. **FIXED.**
- **W2** — `_remember_scaffold_outcome` senza `memory_key`/dedup: ogni run
  appendeva un record; failure ripetuti allagavano `local_memories.jsonl` e
  spingevano fuori le lezioni diverse dal top-3 recall. **FIXED.**
- **W3** — success memories sovrastimavano l'evidenza: gate verde + finding
  security MEDIUM → memoria "tested successful approach" senza menzione dei
  warning, richiamata poi come pattern da imitare. **FIXED.**
- **W4** — `_fallback_envelope` polarity incoerente: default positive per
  ogni status non-failure, inclusi `pending_review`/`quarantine`. **FIXED.**
- **W5** — mtime-hash staleness (VectorStore cache): in coda, non affrontato.
- **W6** — project-path normalization (nome progetto da path Windows/WSL):
  in coda, non affrontato.
- **W7** — dead artifacts (verificato 2026-07-18): `devin/memory/semantic_index.pkl`
  stale su disco (non tracked; il codice vivo usa `.devin_cache/semantic_index.json`),
  `devin/memory/brain.json` tracked ma referenziato solo da archive/old docs
  e dal tree comment del README (legacy), `devin/memory/stats.py` tracked,
  0 byte, non importato da nessuno. In coda (rimozione), non affrontato.
- **W8** — boundary tests per `is_operational_build_request` (soglie
  create/deliverable/explanation score): in coda, non affrontato.

### Fix applicati (4 commit)

1. **Write paths enforce taxonomy (`8fc2891`).** Root cause W1.
   `record_eval_result`: accetta as-is solo `verified_success`/`verified_failure`
   (gli status che puo' legittimamente produrre); qualunque altro status
   (human_confirmed, hypothesis, garbage) e' normalizzato a
   `status:pending_review` + `polarity:neutral` + `kind:raw_observation`, con
   lo status originale preservato nel content come evidenza; la polarity
   deriva SOLO dallo status normalizzato finale. Caller attuale
   (`chat.py` via `detect_chat_only_output`, status hardcoded
   `verified_failure`) invariato e verde. `LocalMemoryStore.store`: i record
   i cui tag falliscono `validate_memory_tags` vengono NORMALIZZATI a
   review-only (status/kind/visibility/promotion/polarity sostituiti con
   default sicuri) invece di rifiutati — scelta normalize-over-reject perche'
   i caller catturano eccezioni broad e un raise perderebbe evidenza
   silenziosamente; la violazione resta registrata nel campo record
   `taxonomy_violations`. Consumer check: return value di
   `record_eval_result` e' solo interpolato in un warning SSE; i record JSONL
   sono letti con `.get` — chiave extra tollerata.
2. **Scaffold outcome dedup by memory_key (`fd97e0b`).** Root cause W2.
   `_remember_scaffold_outcome` ora calcola `memory_key` con lo stesso
   `_memory_key`/`_existing_memory_keys` di `eval_recorder` (project name +
   eval_name `scaffold_quality_gate` + failure_type/status + task[:500]) e
   ritorna `"duplicate"` senza scrivere se la chiave esiste gia'. Consumer
   check: `run_scaffold` confronta solo `!= "not_recorded"` per il log e
   passa il valore nel result dict — `"duplicate"` tollerato; i fake Memory
   dei test esistenti non hanno `.local` → dedup bypassato, contratto
   invariato.
3. **Success memories carry security warnings (`32021d2`).** Root cause W3.
   Quando `quality["security_warnings"]` (lista di stringhe bandit MEDIUM+ da
   `_scaffold_quality_gate`) e' non vuota, il content della memoria include
   una sezione compatta `Security warnings (N finding MEDIUM+, scanner X): ...`
   (max 5 warning, 160 char cad.) e un success-with-warnings legge
   "a tested successful approach WITH security warnings (review the findings
   before imitating it)". Clean success invariato.
4. **Fallback envelope polarity da status (`ca4bc2a`).** Root cause W4.
   Il default di polarity in `_fallback_envelope` deriva dallo status:
   `verified_failure` → negative; status recall-safe rimanenti
   (`verified_success`, `human_confirmed`) → positive; tutto il resto
   (pending_review, quarantine, hypothesis, ...) → neutral. Un tag
   `polarity:` esplicito vince ancora (intent del caller); il test esistente
   di failure-semantics resta verde invariato.

### Test

Nuovo file `test_memory_write_path.py` (helper self-contained per repo
style: `_make_memory_client` HybridMemoryClient + tmp_path JSONL + remotes
disabilitati, `_CaptureMemory` fake, `_make_orchestrator` via `__new__` +
attribute injection): 20 test — normalizzazione record_eval_result
(human_confirmed/hypothesis/garbage → review-only, non recallabili;
verified_* invariati e recall-safe), unit test diretti dei rami errore di
`validate_memory_tags` (unknown status, unknown kind, quarantine+promoted),
store fail-safe con `taxonomy_violations`, dedup memory_key (secondo store
identico → duplicate, failure diverso → stored, success/failure chiavi
distinte), warning security nel content (success/failure/clean), polarity
envelope per status. File esistenti non toccati.

- Suite: **293 -> 313 passed, 1 skipped** (+20 test). Nessun consumer
  inatteso emerso; nessun fix escluso.

### Coda memoria rimanente

W5 (mtime-hash staleness), W6 (project-path normalization), W7 (rimozione
dead artifacts: semantic_index.pkl stale, brain.json legacy tracked,
stats.py vuoto), W8 (boundary tests is_operational_build_request).

---

## 2026-07-18 (sera) — Memory map leftovers chiusi: W5–W8

La mappa del sottosistema memoria e' ora **completamente processata**: la
coda residua (W5–W8) e' chiusa in 4 commit separati, suite intera verde
(**313 -> 335 passed, 1 skipped**, +22 test).

### W5 — VectorStore staleness: content hash, non solo mtime (`36962d4`)

**Root cause.** `_file_mtime_hash` usava solo path+mtime: una riscrittura
che preserva mtime (granularita' FS grossolana, `os.utime`, alcuni checkout
git) lasciava la cache "valida" e serviva embedding stale — contesto file
sbagliato al Coder, in silenzio.

**Fix.** `_staleness_key(path, content)` = md5 di
`path:mtime:size:md5(contenuto)`. Il contenuto e' GIA' in memoria per
l'embedding (`files[].content`), quindi l'hash e' quasi gratis — niente
re-read da disco. La chiave si calcola sul contenuto RAW pre-troncamento
(una modifica oltre i 4000 char deve invalidare). Per i path virtuali senza
file su disco (chunk `file#chunk-h`) lo stat fallisce e la chiave si appoggia
a path+contenuto.

**Cache-version decision.** Nessun bump di `_CACHE_FORMAT`: le vecchie
chiavi mtime-only non matchano mai le nuove → reindex una tantum al primo
giro, mai crash (il confronto e' pura string equality su JSON; la struttura
della cache e' invariata). Verificato da test dedicato con cache legacy
simulata.

**Test** (`test_vector_store_e2e.py`):
- `test_reindex_on_content_rewrite_with_preserved_mtime` — rewrite +
  `os.utime` al mtime esatto → reindex, nuovo contenuto in search.
- `test_old_mtime_only_cache_entries_invalidated_not_crashed` — cache con
  chiavi vecchio formato → invalidata pulitamente, search funzionante.

### W6 — search_semantic: project path normalizzati (`c209256`)

**Root cause.** Il filtro progetto era string equality secca
(`doc_project != str(project_path)`): caller con path relativo, trailing
slash o `..` ottenevano `[]` silenzioso — recall failure indistinguibile da
"nessun file rilevante".

**Fix.** `_normalize_project_path` = `normcase(normpath(abspath(p)))`
applicata a ENTRAMBI i lati al punto di confronto (lato query una sola
volta). NON resolve symlink: costo per-call e rischio di mismatch coi path
stored superano il caso residuo. Caller attuali (project_space,
understory_client, context_retriever) passano gia' `str(Path)` assoluti:
comportamento invariato per loro, fix solo per le varianti.

**Test** (`test_vector_store_e2e.py`):
- `test_search_semantic_project_path_normalized` — indicizza assoluto,
  cerca con trailing slash e variante `..` → hit presenti.
- `test_search_semantic_project_isolation_after_normalization` — nessun
  cross-project leakage (lato sicurezza del filtro) anche con varianti
  di path.

### W7 — Dead memory artifacts rimossi (`cf0452f`)

- `devin/memory/semantic_index.pkl` — untracked, stale (live:
  `.devin_cache/semantic_index.json`): cancellato da disco.
- `devin/memory/stats.py` — tracked, 0 byte, mai importato: `git rm`.
- `devin/memory/brain.json` — tracked, referenziato SOLO da
  `archive/old_docs/` (storico, lasciato) e dal tree comment di
  `README_DEVIN_AI_IDE.md`: comment aggiornato (ora elenca
  vector_store/eval_recorder/taxonomy) + `git rm`. Nessun riferimento live.
- Nuovo `test_memory_hygiene.py`: nessun `*.pkl` sotto `devin/memory/`
  (escluso `__pycache__`), i tre artifact restano gone; path risolti dal
  test file (verificato da cwd esterna).

### W8 — Boundary tests is_operational_build_request (`91594de`)

Regola pinnata (verificata in `eval_recorder.py` L25-32):
`create_score >= 1 AND deliverable_score >= 2 AND explanation_score == 0`,
con match per sottostringa sul lowercased (es. `test` matcha dentro
`tests.py`). 13 casi parametrizzati just-below/just-above per soglia create
e deliverable (IT+EN), 4 veti explanation, empty/generic; piu' `None`,
e 2 pin dei consumer (`detect_chat_only_output` skip su non-operational,
fire su operational chat-only). In `test_memory_write_path.py`.

### Suite

**335 passed, 1 skipped, 3 warnings** (warning preesistenti). Nessuno stop:
la migrazione cache W5 non ha richiesto piu' di una invalidazione tollerante.

## 2026-07-18 (sera) — W9: index_project multi-progetto (`c43a109`)

**Root cause.** `VectorStore.index_project` azzerava l'INTERO indice in
memoria a ogni chiamata (`self._index = []`), nonostante il commento
"rimuovi vecchi doc dello stesso progetto". Indicizzare il progetto B in
uno store che conteneva A cancellava silenziosamente gli embeddings di A →
`search_semantic` su A tornava `[]` — recall failure indistinguibile da
"nessun file rilevante". Il bug era gia' noto come workaround in un test
(W6 isolation: doc di B aggiunto a mano perche' "index_project azzera").

**Blast radius: LATENTE, non live.** Mappa caller: ogni istanza VectorStore
in produzione serve UN solo project path —
`_project_space_for` in `fast_app.py` cache-a un `ProjectSpace` per path
(due store suoi: knowledge + files), `Orchestrator` e' per-run/progetto,
`understory_client` per memory bundle, i `ProjectSpace` dei router sono
throwaway. Fix applicato comunque (low-risk): il mismatch
commento/comportamento e' di per se' un difetto, e il primo refactor che
condivida uno store tra progetti avrebbe perso recall in silenzio.

**Fix.** `_evict_project_docs(project_path)`: eviction per-progetto con
path normalizzati (stessa `_normalize_project_path` del filtro search; i
chunk virtuali `file#chunk-h` hanno `metadata.project` valorizzato).
`_load_from_cache` FONDE invece di sostituire (eviction stesso progetto +
extend; refit tfidf/keyword sull'indice fuso cosi' gli embeddings restano
nello stesso spazio). `_save_to_cache` scrive SOLO i doc del progetto
corrente: le cache restano file per-progetto. Error path di load
invalidano solo il progetto corrente. Nessun bump di `_CACHE_FORMAT`,
thread-safety invariata (riassegnazione lista, come prima).

**Test** (`test_vector_store_e2e.py`, +3):
- `test_index_project_keeps_other_projects_docs` — A e B nello stesso
  store entrambi cercabili dopo l'indicizzazione di B (pre-fix: FAIL).
- `test_reindex_refreshes_only_same_project` — reindex di A refresha i doc
  di A (nuovo token trovato, vecchio rimosso) senza toccare B; la cache di
  A contiene solo doc di A (pre-fix: FAIL su "B intatto").
- `test_project_cache_file_survives_other_project_reindex` — pin
  cache-layer: il file cache di A sopravvive al reindex di B e ricarica
  (passa anche pre-fix: la perdita era solo in-memoria, per la sessione).

Side observation (FIXED in sessione dedicata, vedi sezione sotto):
`Orchestrator` indicizzava `self.vector_store` ma cercava via
`self.context_retriever.store` — due istanze VectorStore diverse, la
seconda mai indicizzata → il contesto semantico dell'orchestrator
risultava sempre vuoto.

### Suite

**338 passed, 1 skipped, 3 warnings** (335 + 3 nuovi W9).

## 2026-07-18 (sera) — Orchestrator: UN VectorStore per indexing + retrieval

**Verifica del sospetto (confermato).** `Orchestrator.__init__` creava
`self.context_retriever` (orchestrator.py L118) e, separatamente,
`self.vector_store = VectorStore()` (L130). `ContextRetriever.__init__`
creava a sua volta il PROPRIO `VectorStore()` (context_retriever.py L7).
In `run()` la fase "Building context" chiamava
`self.vector_store.index_project(...)` (L1050) ma il retrieval passava da
`self.context_retriever.retrieve(task, project_path)` (L1054) →
`search_semantic` sullo store del retriever, mai indicizzato. Unico caller
di `ContextRetriever` in tutto il repo: l'Orchestrator. Nessun percorso
alternativo in `run_scaffold`/heal loop (nessun uso di ProjectSpace in
orchestrator.py — i ProjectSpace vivono in fast_app.py con store propri).

**Perche' silenzioso.** `VectorStore.search_semantic` su indice in-memoria
vuoto torna `[]` (stampa solo "[VectorStore] Indice vuoto"); il load da
cache JSON avviene SOLO dentro `index_project`, mai in search. Quindi il
blocco "# === FILE RILEVANTI SEMANTICAMENTE AL TASK ===" era sempre assente
e `prioritize()` riceveva "" — indistinguibile da "nessun file rilevante".
Il Coder non ha MAI ricevuto contesto semantico dei file da quando esiste
la fase vector store.

**Fix (minimo).** Dependency injection senza redesign:
- `ContextRetriever(enabled=True, store=None)`: `store` opzionale; default
  crea la propria istanza (retrocompatibilita', pinned da test).
- `Orchestrator.__init__`: `self.vector_store = VectorStore()` spostato
  PRIMA del retriever e passato come `store=self.vector_store`; rimossa la
  seconda creazione. Indexing e retrieval ora confluiscono su UN store.

**Test** (`test_orchestrator_semantic_wiring.py`, +3, fixture
autocontenute da test_stalled_guard.py con `semantic_search_enabled: True`):
- `test_run_indexes_the_same_store_the_retriever_searches` — integrazione
  comportamentale: dopo la fase di indicizzazione di run(), `retrieve()`
  trova il contenuto di calc.py (pre-fix: FAIL, `assert ''`).
- `test_context_retriever_uses_injected_store` — unit pin: lo store
  iniettato e' quello usato (pre-fix: TypeError sul kwarg).
- `test_context_retriever_default_store_is_own_instance` — default
  invariato senza injection (passa anche pre-fix).

**Suite: 341 passed, 1 skipped** (338 + 3 nuovi). py_compile pulito su
entrambi i file toccati.

## 2026-07-18 (sera) — Training: coverage finale CRUD endpoints + store exports

**Contesto.** Ultima fetta di coverage del sottosistema training: gli endpoint
CRUD di `devin/ui/routers/training.py` (cases/seed/attempts/reviews/
corrections/lessons/export/jobs/overview) e gli export di
`devin/training/store.py` (`export_teacher_packet`, `export_sft_dataset`,
`list_exports`) erano gli unici pezzi senza pin (`/api/training/run` e
`/api/training/reviews/structured` gia' coperti da
`test_training_runner_declass.py` e `test_understory_hybrid.py`).

**Lavoro (solo test, nessuna sorgente toccata).** Nuovo file
`test_training_endpoints_exports.py` (+20 test). Pattern riusati:
`TrainingStore(tmp_path)` diretto per lo store-level; FakeRequest + chiamata
async diretta all'handler con `_training_store_for` monkeypatchato sul ROUTER
(nome risolto a call-time, pattern test_understory_hybrid L732-765) per
l'endpoint-level — niente TestClient, non e' il pattern consolidato per
questo router. Fixture autouse di isolamento `_training_jobs` replicata da
test_training_runner_declass.

Pin principali:
- store: `add_case` ValueError su task vuoto, tombstone `retire_case`
  (escluso dal listing attivo, record `op: retire` nello storico),
  `add_review` status invalido/attempt sconosciuto, validazione
  `add_correction`/`add_lesson`, `latest_reviews_by_attempt` latest-per-
  attempt, `summary()` su store misto (conteggi esatti, pending_review fuori
  da ogni contatore di esito).
- teacher packet: contratto anti-contaminazione pinnato preciso —
  `packet_version: teacher_review_v1`, `promotion_policy.auto_promote is
  False`, liste status recall-safe/review-only ESATTE (e coerenti con i set
  canonici dello store), campi evidenza case/attempt, known_reviews/
  known_corrections.
- SFT export: shape `messages` (system/user/assistant, stringa system
  esatta); pin del comportamento reale — il dataset e' guidato dalle
  CORREZIONI, nessun filtro per status dell'attempt (correzione su
  auto_failure entra; correzione orfana -> user fallback "Fix the previous
  attempt."). `list_exports` rileva entrambi i formati.
- endpoint: round-trip cases/seed (seed idempotente), convenzione errore
  `{"error": str(exc)}` su attempts (status invalido, non 500) e su
  reviews/corrections/lessons, jobs con job_id sconosciuto (`{"job": {}}`),
  overview su store vuoto (chiavi + memory_policy + benchmark builtin) e
  surfacing delle source importate (fix dropdown 2026-07-15).

**Bug trovati:** nessuno — tutti i pin corrispondono al comportamento
attuale. Comportamenti "sorprendenti ma intenzionali" pinnati e annotati
nei docstring (SFT senza filtro status, correzioni orfane con fallback,
`{"job": {}}` per job sconosciuto).

**Suite: 361 passed, 1 skipped** (341 baseline + 20 nuovi). File nuovo da
solo: 20 passed in 0.67s.

**Code residue note:**
- Token gate shared-secret su fast_app: APPROVATO, ancora da implementare
  (vedi "Decisioni di direzione").
- PWA-ificazione SPA `/app`: approvata, differita (stessa sezione).
- Endpoint `/api/training/adapters/mbpp/import`: non pinnato in questa fetta
  (richiede download dataset ~5MB / mock pesante dell'adapter; candidato per
  una fetta dedicata con `import_mbpp_cases` monkeypatchato sul router).

## 2026-07-18 (notte) — Sweep finale di coverage: MBPP import endpoint + invarianti ProjectSpace

**Contesto.** Chiusura delle due voci test-only residue: l'endpoint MBPP
(rimandato nella fetta precedente per il download da mockare) e il pin
dell'invariante store-identity motivato dal bug live 27c4697 (orchestratore
che indicizzava uno store e recuperava da un altro).

**Item 1 — MBPP import endpoint (solo test, commit `9d6d3a1`).** Esteso
`test_training_endpoints_exports.py` (+3 test, sezione 12).
`import_mbpp_cases` e' importato a livello di modulo in
`devin/ui/routers/training.py`, quindi il patch va sul global del ROUTER
(risolto a call-time dentro `asyncio.to_thread`). Fake che seeda N casi
nello store REALE su tmp_path e ritorna la shape di summary vera
(adapters.py L163-170). Pinnato:
- happy path: risposta con `created/converted/source="mbpp"`, casi davvero
  nello store, reseed idempotente via endpoint;
- clamping del router: limit>1000 -> 1000, offset<0 -> 0, default 10;
- errore download simulato (fake che raise) -> convenzione
  `{"error": "import MBPP fallito: ..."}`, niente 500, niente casi parziali;
- payload invalido (limit non int): la `int()` di validazione e' DENTRO il
  try dell'handler -> stessa convenzione errore, import mai chiamato.

**Item 2 — invarianti ProjectSpace (solo test, commit `04a55d8`).** Nuovo
file `test_project_space_store_invariant.py` (+5 test). Lettura completa di
`devin/core/project_space.py`: esattamente DUE VectorStore lazy per space
(`_vector_store` knowledge L358-370, `_files_vector_store` file L486-497),
e per OGNI concern `index_project` e `search_semantic` passano dalla STESSA
istanza nella stessa chiamata — nessun mismatch live (il bug 27c4697 era
nell'orchestratore, non qui). Pinnato a livello comportamentale:
- knowledge: indicizzato -> ritrovato, istanza unica riusata, invalidate su
  `add_knowledge` ricrea l'istanza e la retrieve vede subito il contenuto
  nuovo (la classe di bug di 27c4697);
- files: stesso pin sull'istanza separata; zero cross-contaminazione tra i
  due concern sullo stesso ProjectSpace;
- `fast_app._project_space_for` (L495-499): stessa istanza cached per lo
  stesso path; trailing slash collassato da `resolve()` in
  `_safe_under_allowed` -> UNA sola entry in cache (commento nel test sul
  case: normalizzazione Windows-only, non pinnabile in WSL);
- path vuoto -> GENERAL_CHAT_PROJECT_KEY (cached anche lui); path fuori
  allowed roots -> 403 e cache intatta.
Fixture autouse di snapshot/restore per `_project_spaces` e `_ALLOWED_ROOTS`
(stato di modulo di fast_app).

**Bug trovati:** nessuno — entrambi gli item sono pin del comportamento
corrente, che risulta corretto. Nessuna sorgente toccata.

**Suite: 369 passed, 1 skipped** (364 dopo Item 1, +5 Item 2). File singoli:
23 passed (training exports esteso), 5 passed (project space invariant).

**Coda residua FINALE:**
- Token gate shared-secret su fast_app: IMPLEMENTATO (commit `f0e5e8c`,
  entry datata qui sotto).
- PWA-ificazione SPA `/app`: approvata; per il goal amendment 2026-07-18
  (vedi "Decisioni di direzione") si procede a discrezione del owner.
- Niente altro in coda: lo sweep di coverage e' chiuso.

## 2026-07-18 (notte) — Token gate shared-secret implementato (commit `f0e5e8c`)

**Contesto.** Unico item di codice residuo approvato: il gate a segreto
condiviso per fast_app. Threat model: con `ui.host=0.0.0.0` (LAN/Tailscale)
TUTTI gli endpoint — lettura file, avvio run, stop modelli — erano aperti a
chiunque raggiungesse la porta; il loopback (GUI desktop) doveva restare
senza attriti.

**Verifica SSE preliminare.** Confermato in
`devin/ui/static/js/codex_app.js`: lo stream eventi dei run usa
`new EventSource("/api/run/{id}/events/stream?...")` (L659-663) — nessun
header custom possibile; `/api/chat` usa fetch + `response.body.getReader()`
(fetch-streaming, L920-945). Il canale query+cookie e' quindi necessario e
sufficiente per zero modifiche al frontend (34+ fetch/EventSource hardcoded
non toccati in questa fetta).

**Implementazione.** Nuovo `devin/ui/token_gate.py`: middleware ASGI puro
(niente BaseHTTPMiddleware: zero buffering dei body, nessuna interferenza
con SSE/streaming), cablato in `fast_app.py` subito dopo la creazione
dell'app — copre TUTTE le route, `/static` incluso.

- **Config**: segreto da env `DEVIN_API_TOKEN` (precedenza) oppure
  `settings.json -> ui.api_token`. Risolto ad OGNI richiesta (cambi senza
  restart, test monkeypatchabili). Non configurato/vuoto -> gate
  DISABILITATO: comportamento precedente preservato, suite esistente verde
  senza saperlo.
- **Loopback esente**: `127.0.0.1`, `::1` (+ forma mapped `::ffff:127.0.0.1`)
  passano sempre. Client host mancante -> fail-closed (token richiesto).
- **Tre canali, ne basta uno**: `Authorization: Bearer <secret>`; cookie
  `devin_token` (HttpOnly, SameSite=Lax, Path=/; NIENTE flag Secure —
  uvicorn serve HTTP piano, vedi run_server); query `?token=<secret>`. Su
  auth via query la risposta IMPOSTA il cookie: la SPA fa bootstrap da un
  solo URL con `?token=` e ogni fetch/EventSource successivo passa via
  cookie.
- **Confronto** a tempo costante (`hmac.compare_digest`); il segreto non
  e' MAI loggato dal modulo. Fallimento -> `401` JSON
  `{"error": "unauthorized"}` (convenzione errore dell'app), nessun
  dettaglio.

**Test** (`test_token_gate.py`, 16 test, HTTP-level via TestClient — pattern
volutamente diverso dall'endpoint-level degli altri test: il gate vive
nello stack ASGI). Client non-loopback simulato con
`TestClient(app, client=("10.0.0.5", 5000))` (supportato da starlette
1.3.1; il default `("testclient", 50000)` NON e' loopback). Isolamento:
`token_gate.CONFIG_PATH` puntato a settings vuoto su tmp_path + env
monkeypatchata. Casi: gate disabilitato passthrough; 401 JSON senza token;
esenzione loopback v4/v6; i tre canali pass + 401 su token sbagliato;
bootstrap cookie da `?token=` (Set-Cookie HttpOnly/SameSite=Lax + richiesta
successiva cookie-only che passa; token sbagliato NON setta cookie);
`/api/chat/history` dietro il gate (401 poi pass); precedenza env >
settings; pin sorgente su `hmac.compare_digest` (niente test di timing,
flaky).

**Limiti noti (security-relevant, solo registrati — non fixati in questa
fetta):**
- L'access log di uvicorn (log_level="info" in run_server) registra le URL
  COMPLETE: con auth via query il token finisce nei log del processo.
  Mitigazione d'uso: bootstrap una-tantum con `?token=` poi solo cookie.
- L'IP client e' quello della connessione diretta: dietro un reverse proxy
  tutte le richieste apparirebbero dal proxy (se in loopback, gate
  bypassato). Supportate solo connessioni dirette — coerente col deploy
  approvato (Tailscale/WireGuard, niente porte aperte).
- Un solo segreto condiviso: niente token per-device, niente revoca, niente
  rate-limit sul 401. Il lato rig resta aperto (decisione owner: "the
  right one" anche li', da fare quando serve).

**Suite: 385 passed, 1 skipped** (369 baseline + 16 nuovi; file nuovo da
solo: 16 passed in 0.61s). py_compile OK sui tre file toccati.

## 2026-07-18 (notte) — PWA slice per /app (commit `d534f6f`)

Direzione owner: accesso mobile = **PWA + Tailscale**, niente app nativa.
Slice PWA minima e coerente completata.

**Cosa e' stato fatto.**
- `devin/ui/static/manifest.webmanifest`: name "DEVIN AI IDE", short_name
  "DEVIN", display `standalone`, start_url `/app`, theme/background
  `#151311` (palette calda del polish layer v2 in codex_app.css). Servito
  da route dedicata in `routers/pages.py` con content type
  `application/manifest+json` (mimetypes non conosce `.webmanifest`).
- Icone 192/512 generate con Pillow (`devin/ui/static/icons/`): gradiente
  scuro + tile accent `#d7a074` + monogramma "D". Nessun asset esistente
  toccato.
- `devin/ui/static/sw.js`: **precache shell-only** (route `/app`,
  codex_app.js/css, manifest, icone) in cache versionata
  `devin-shell-v1`; **network-only per TUTTE le `/api/*`** (contenuti
  memoria/chat MAI in cache — requisito privacy); cache-first per la
  shell con fallback di rete; cleanup cache vecchie su activate.
- **Problema scope risolto**: StaticFiles e' montato su `/static`, quindi
  un SW servito da li' controllerebbe solo `/static/*`. Nuova route
  root-scope `GET /sw.js` in `routers/pages.py` con header
  `Service-Worker-Allowed: /`, content type JS e `Cache-Control:
  no-cache` (il file SW deve sempre rivalidarsi).
- Registrazione SW: snippet inline guarded in `codex_app.html`
  (`if ('serviceWorker' in navigator)`); la pagina e' gia' autenticata e
  le richieste del SW portano il cookie di sessione same-origin
  (bootstrap cookie del token gate invariato).
- Meta tag in `codex_app.html`: manifest link, `theme-color`,
  `mobile-web-app-capable` / `apple-mobile-web-app-capable`,
  apple-touch-icon (viewport esisteva gia').
- Responsive pass (tutto additivo, dentro media query; desktop
  pixel-identico): stack a colonna singola <=1100px preesistente
  mantenuto; pannello Mind <=1100px diventa overlay on-demand (toggle in
  topbar); <=768px anche il pannello Workspace diventa overlay; target
  touch >=40px sui controlli primari; textarea composer 16px (niente
  auto-zoom iOS). Nessuna logica di toggle preesistente nel JS
  (verificato): aggiunta `setupPanelToggles()` minimale (toggle,
  chiusura su selezione, Esc).

**Test** (`test_pwa_assets.py`, 5 test, HTTP-level TestClient su loopback
+ pin source-level sulla policy del SW): manifest content type + chiavi
richieste; `/sw.js` root-scope headers + no-cache; precache shell-only +
branch `/api/` network-only (il check `/api/` precede qualunque
`cache.put` per costruzione); icone servite come PNG; meta tag +
registrazione nella shell `/app`.

**Suite: 390 passed, 1 skipped** (385 baseline + 5 nuovi).

**Resta da fare:**
- Token gate lato rig (decisione owner: "the right one" anche li', da
  fare quando serve) — invariato dalla nota precedente.
- **Convenzione version-bump SW**: ad OGNI deploy che tocca la shell
  (HTML/CSS/JS/icone), incrementare `CACHE_VERSION` in
  `devin/ui/static/sw.js` (es. `devin-shell-v1` -> `devin-shell-v2`),
  altrimenti i client PWA continuano a servire la shell vecchia dalla
  cache. Il file SW stesso e' no-cache, quindi l'update si propaga al
  primo reload dopo il bump.
- Verifica in browser reale (install prompt, standalone, overlay
  mobile): in questo ambiente non c'e' browser — la verifica e' solo
  HTTP-level + source-level.

## 2026-07-20 — Continuita' preventiva delle chat lunghe

Implementato un checkpoint `chat_continuity_v1` prima dell'espulsione dei
turni vecchi dalla finestra del modello. Il trigger usa sia stima conservativa
dei token sia numero messaggi; conserva una coda verbatim recente e compatta
solo la parte precedente. Il riassunto ha prompt evidence-only, limiti rigidi,
refresh incrementale e fallback deterministico se il modello non risponde.

Il checkpoint vive nel JSON della singola chat ed e' esplicitamente stato di
conversazione, non memoria long-term/recall-safe. `ChatPersistence.save()` lo
preserva atomicamente insieme a titolo e history. La risposta chat lo inietta
come system context separato e pubblica nel meta SSE se e' attivo.

La UI mostra `Continue` solo quando il checkpoint e' pronto: crea una nuova
chat vuota che eredita il handoff e registra `continued_from`, senza copiare lo
storico completo. Configurazione bounded in `chat.continuity` dentro
`config/settings.json`. Test dedicati coprono trigger, fingerprint, reuse,
refresh incrementale, edit detection, fallback, persistenza e trasferimento.
# Follow-up: generate-patch rispetta la cartella di lavoro

`/api/chat/generate_patch` ora mantiene chat e knowledge sul progetto DEVIN ma
instrada l'esecuzione sull'eventuale `work_dir` collegata, con la stessa
validazione allowlist usata da run/scaffold/resume. Prima validava soltanto il
progetto metadati e modificava quello, ignorando la cartella sorgente collegata.

Regression test: `test_generate_patch_workdir.py` copre sia il routing verso la
cartella collegata sia il comportamento compatibile senza `work_dir`.
