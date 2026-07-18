# ROADMAP: DEVIN AI IDE — Unificazione & Modernizzazione UI

> **Stato attuale**: FastAPI WSL headless + Tauri desktop shell Windows-native, `/app` workspace pulita, Diagnostics a tab, legacy Dashboard/Chat ancora disponibili come fallback.  
> **Obiettivo**: app desktop Tauri stile Codex/Claude Desktop con piano visivo, diff viewer, terminale, notifiche e flusso training/Teacher validato.

## Desktop-first clarification

La direzione prodotto è una app desktop locale, non una Web UI da usare nel browser. Il backend FastAPI resta il motore locale/API layer. Tauri è già il flusso di test preferito via launcher Windows-native in `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd`; il browser resta fallback tecnico.

---

## FASE 0: Cleanup & Fondamenta (30-60 min)
**Obiettivo**: rimuovere legacy, stabilire baseline, documentare API.

| Task | File | Descrizione | Rischio |
|------|------|-------------|---------|
| **0.1** | `main.py` | Fix entry point: puntare a `devin.ui.fast_app.run_server()` invece di `devin.ui.app.start_ui()` (Tkinter legacy). | Basso |
| **0.2** | `devin/ui/diff_viewer.py` | Rimuovere/sostituire con endpoint `/api/diff` che restituisca JSON (non finestra Tkinter). | Basso |
| **0.3** | `devin/ui/web_app.py` | Verificare se è ancora usato; se Tkinter, deprecare. | Basso |
| **0.4** | Test suite | Eseguire `pytest` per avere baseline verde prima di toccare. | Medio |
| **0.5** | Audit API | Documentare tutti gli endpoint in `fast_app.py` (c'è già tantissimo, ma serve una tabella di marcia). | Basso |

---

## FASE 1: API Backend — Stato, Diff, Terminale (1-2h)
**Obiettivo**: dare al frontend i dati strutturati che mancano per una UI ricca.

| Task | Endpoint | Descrizione |
|------|----------|-------------|
| **1.0** | `GET /api/mind/status` | Cruscotto mentale leggero per sidebar destra: loop cognitivo, memoria, eval, modelli, direzione Tauri. **Completato.** |
| **1.1** | `GET /api/run/{run_id}/events` | **Completato primo contratto**: timeline JSON + SSE per eventi run strutturati (`plan`, `act`, `verify`, `memory`, `commit`, `run_finished`). Prossimo step: collegamento live completo nella SPA. |
| **1.2** | `POST /api/diff/apply` | Applica una patch proposta dal Coder con approvazione manuale (oggi il Patcher la applica automaticamente). |
| **1.3** | `POST /api/diff/reject` | Rigetta una patch e ritorna al Critic con feedback. |
| **1.4** | `POST /api/terminal` | Esegue comandi shell via `devin/engine/shell.py` e restituisce output JSON. Serve per il terminale integrato. |
| **1.5** | `GET /api/plan/{run_id}` | Restituisce lo stato corrente del piano (lista step con status: `waiting`/`running`/`done`/`failed`). |
| **1.6** | `POST /api/run/{run_id}/stop` | Interrompe un run in corso (oggi non c'è modo di killare un loop Orchestrator via API). |
| **1.7** | Config web search | Cambiare `use_web_search` da boolean a enum: `off` / `auto` / `force`. Aggiornare `ChatRequest` e la logica in `fast_app.py`. |

---

## FASE 2: UI SPA — La Grande Unificazione (3-4h)
**Obiettivo**: un'unica pagina con 3 pannelli, niente più reload tra Dashboard/Chat/History.

### Layout target (dalla chat precedente)
```
┌────────────────────────────────────────────────────────────────────┐
│ DEVIN    progetto ▾    Locale ●  Rig ○    VRAM 95%    ⌘ Comandi    │
├──────────────┬───────────────────────────────┬───────────────────┤
│ PROGETTI     │ CHAT / ATTIVITÀ               │ CONTESTO          │
│              │                               │                   │
│ Steam Check  │ Tu: crea il checker...        │ Piano             │
│ DEVIN IDE    │                               │ ✓ Ricerca fonti   │
│ ForgeStudio  │ DEVIN: sto analizzando...     │ ◌ Architettura    │
│              │                               │ ◌ Generazione     │
│ CHAT         │ [azioni e ragionamento]       │                   │
│ Nuova chat   │ [comandi eseguiti]            │ File modificati   │
│ Storico      │ [diff da approvare]           │ API consultate    │
│              │                               │ Memorie usate     │
├──────────────┴───────────────────────────────┴───────────────────┤
│ 📎  Chiedi a DEVIN…            Web [Auto ▾]   [Locale/Rig]  Invia │
└────────────────────────────────────────────────────────────────────┘
```

| Task | File | Descrizione |
|------|------|-------------|
| **2.1** | `devin/ui/templates/codex_app.html` | **Completato MVP `/app`**: shell a 3 pannelli local-first, con workspace/runs, work-stream e Mind panel. La vecchia dashboard resta intatta. |
| **2.2** | `devin/ui/static/css/codex_app.css` | **Completato MVP `/app`**: tema dark dedicato con variabili CSS e layout responsive; niente dipendenze font esterne. |
| **2.3** | `devin/ui/static/js/codex_app.js` | **Completato/ottimizzato**: modulo JS vanilla per workspace leggera; legge mind/projects/chat, mentre runs/training sono spostati in Diagnostics lazy tabs. |
| **2.4** | `devin/ui/static/js/status.js` | Header bar: polling `/api/models/info` + `/api/health`, badge Locale/Rig, barra VRAM. |
| **2.5** | `devin/ui/static/js/codex_app.js` | **MVP esteso**: left sidebar legge `/api/workspace/projects`, mostra conversazioni, crea chat progetto e passa `project_path` + `chat_id` alla chat. |
| **2.6** | `devin/ui/static/js/codex_app.js` | **MVP esteso**: composer chat nella `/app`, streaming SSE via `fetch`, mode selector, web toggle boolean, caricamento storico e allegato file via `/api/chat/document`. Mancano web toggle 3-stati e gestione allegati multipli. |
| **2.7** | `devin/ui/static/js/agent.js` | Center pane — modalità Agent: quando parte uno scaffold, mostra il piano step-by-step con icone di stato. |
| **2.8** | `devin/ui/static/js/editor.js` | Center pane — modalità Editor: Monaco integrato (reuso da `index.html`), file explorer, AI autocomplete. |
| **2.9** | `devin/ui/static/js/context.js` | Right sidebar: piano (step list), file letti, fonti web, memorie usate. |
| **2.10** | `devin/ui/static/js/history.js` | Integrazione History come tab dentro la SPA, non pagina separata. |
| **2.11** | `fast_app.py` | **Completato**: route `GET /app` renderizza la nuova shell; default `/` invariato per rollback semplice. |

---

## FASE 3: Interattività & Agent Mode (2-3h)
**Obiettivo**: trasformare la chat da "scambio di messaggi" a "sessione di lavoro guidata".

| Task | Descrizione |
|------|-------------|
| **3.1** | **Piano visivo**: quando il Planner genera un piano, la sidebar destra mostra ogni step con icona (⏳/⚙️/✅/❌). SSE da `/api/run/{run_id}/events` aggiorna in tempo reale. |
| **3.2** | **Diff preview MVP avviato**: `/app` include preview read-only via `/api/diff/preview`; apply/rifiuta restano da collegare con conferma esplicita. |
| **3.3** | **Apply diff MVP avviato**: `/app` collega `/api/diff/apply` solo dopo preview e conferma esplicita browser. Rifiuta/modifica restano futuri. |
| **3.4** | **Run log MVP avviato**: `/app` mostra output log read-only da `/api/terminal/output` per il run selezionato. Terminale interattivo resta futuro. |
| **3.5** | **Command palette MVP completato**: modal `Ctrl+K` per focus chat, refresh, nuova chat/progetto, navigazione Diagnostics/Training/Memory, selezione progetti e run recenti. Niente comandi shell. |
| **3.6** | **Drag & Drop**: trascinare file/cartelle sulla input bar per allegarli (usa già `/api/chat/document` ma rendere più visibile). |
| **3.7** | **Conferme esplicite**: prima di comandi distruttivi (es. `rm -rf`, `dropdb`) o accessi fuori progetto, mostrare modal di conferma. |

---

## FASE 4: Desktop Polish & Notifiche (1-2h)
**Obiettivo**: comportamento da app desktop pur restando web-based (per ora).

| Task | Descrizione |
|------|-------------|
| **4.1** | **Browser Notifications API**: quando un run lungo termina, invia notifica (`new Notification()`). Funziona su `localhost` senza HTTPS. |
| **4.2** | **System tray** (`pystray`): icona tray Windows che mostra stato modelli, permette "Apri DEVIN", "Stop run", "Esci". Avvia `fast_app` come thread. |
| **4.3** | **Auto-start**: script `.bat` o servizio Windows che lancia `python launcher.py` all'avvio. |
| **4.4** | **Hot reload CSS/JS**: la SPA usa `fetch()` per caricare JS/CSS con cache-busting; in dev si aggiunge `?v=rand()`. |

---

## FASE 5: Tauri 2 Wrapper (Opzionale, 4-6h)
**Obiettivo**: quando la SPA è solida, wrapparla in un'app desktop nativa Windows.

| Task | Descrizione |
|------|-------------|
| **5.1** | **MVP completato**: scaffold Tauri 2 brownfield (`package.json`, `src-tauri/*`) che punta alla `/app` FastAPI esistente. |
| **5.2** | **MVP completato**: Tauri carica `http://127.0.0.1:5000/app`; la SPA resta servita da FastAPI per evitare build step fragili. |
| **5.3** | **Completato MVP**: launcher Windows-native in AppData prepara host Tauri, avvia backend WSL headless e delega fuori dal path UNC. Documentato in `docs/TAURI_DESKTOP.md`. |
| **5.4** | **Notifiche native**, menu tray nativo, auto-updater Tauri. |
| **5.5** | WSL bridge: Tauri su Windows, FastAPI dentro WSL. Esponi `localhost:5000` via `wsl.exe` o SSH tunnel. |

---

## Dipendenze e Ordine

```
FASE 0 ──► FASE 1 ──► FASE 2 ──► FASE 3 ──► FASE 4 ──► FASE 5
  │          │            │           │          │          │
  ▼          ▼            ▼           ▼          ▼          ▼
Cleanup    API nuove    SPA layout   Piano/      Notifiche  Tauri
Baseline   SSE/Diff/    CSS/JS       Diff/       Tray       (opt)
           Terminal     unificati     Terminal
```

**Regola d'oro**: Fase 2 NON inizia prima che Fase 1 sia completa (il frontend ha bisogno delle API per essere utile).  
**Regola d'oro 2**: Fase 0 deve essere verde sui test prima di procedere.

---

## Calcoli & Stime

| Fase | Tempo stimato | Impatto UX | Rischio rottura |
|------|---------------|------------|-----------------|
| 0 | 30-60 min | Basso | Basso |
| 1 | 1-2h | Medio | Medio (cambio API) |
| 2 | 3-4h | **Altissimo** | Medio (nuova pagina, vecchia rimane) |
| 3 | 2-3h | Alto | Medio |
| 4 | 1-2h | Medio | Basso |
| 5 | 4-6h | Medio | Alto (nuovo stack) |

**Consiglio aggiornato**: Fase 5 non è più opzionale/futura; la desktop app Tauri è il percorso principale di test. Continuare a usare `/app` come superficie interna servita dal backend, ma validare in Tauri.  
**Priorità**: workspace polish → diagnostics/training validation → file explorer/editor → agent mode più visivo → installer/update path.

---

## File Legacy da Deprecare (dopo Fase 2 stabile)
- `devin/ui/app.py` (Tkinter)
- `devin/ui/web_app.py` (se Tkinter)
- `devin/ui/diff_viewer.py` (Tkinter)
- `devin/ui/templates/index.html` (vecchia dashboard)
- `devin/ui/templates/chat.html` (vecchia chat)
- `devin/ui/templates/history.html` (vecchia history)

---

## Decisioni Architetturali

1. **Restiamo su FastAPI + Vanilla JS**: Non introduciamo React/Vite nella Fase 2 per evitare build step. La SPA è puramente HTML/CSS/JS nativo. Se in futuro si passa a Tauri, il porting è banale (il 90% è già JS modulare).
2. **Monaco Editor resta**: CDN, niente bundle. Funziona bene in Tauri.
3. **xterm.js per terminale**: CDN, standard, funziona con qualsiasi backend.
4. **No WebSocket**: usiamo SSE per tutto (chat, run, log). SSE è più robusto con proxy/firewall e già usato per la chat.
5. **CSS unificato**: variabili CSS `--bg`, `--panel`, `--accent`, ecc. Tema dark unico, niente più override sparsi.
6. **Modulare**: ogni pannello è un modulo JS separato (`chat.js`, `explorer.js`, `plan.js`, ecc.) caricato con `<script type="module">`.

---

## Prossimo Passo
Prossimo step pratico: validare toolchain Node/Rust/Tauri sul Windows host, poi aggiungere sidecar WSL per avvio backend automatico.

## FASE 2.12: Main page cleanup / dedicated diagnostics

Decisione utente 2026-07-15: la pagina principale deve essere molto più pulita, stile app desktop Codex/Claude. Spostare fuori dalla main view:

- raw run log;
- Cognitive loop;
- Memory safety;
- Eval detectors;
- training dettagliato;
- memory audit dettagliato.

Nuova struttura desiderata per Tauri:

- `Workspace`: chat/progetto/work stream centrale, pulita;
- `Runs`: storico run, log, eventi strutturati;
- `Training`: seed, benchmark queue, attempts, Teacher review;
- `Memory`: ricerca, correzioni, promozioni, contaminazione;
- `Settings`: modelli, rig, Tauri/backend, CUDA/VRAM.

## FASE 6: Teacher / Colibrì batch review pipeline

Obiettivo: far crescere DEVIN con una pipeline evidence-based, senza contaminare memoria e baseline. Il rig resta pensato con i tre ruoli principali:

- **DEVIN**: coding agent operativo; genera patch/progetti, esegue run, prepara artifact.
- **TEACHER**: validatore/correttore locale principale, integrato con ForgeStudio o equivalente.
- **HERMES**: assistente generale/multimodale e supporto trasversale.

**Colibrì/GLM-5.2** è un componente extra, lento e profondo, richiamabile da DEVIN solo per review batch importanti. Non è un ruolo sempre acceso del rig e non sostituisce TEACHER.

### Pipeline target

```text
Benchmark ufficiali/community validati
↓
DEVIN esegue run in sandbox
↓
Log strutturati + artifact + test output
↓
Validator deterministici automatici
↓
TEACHER locale review/correzione
↓
Colibrì review profonda su casi importanti/dubbi
↓
Check finale opzionale OpenAI/Claude se autorizzato
↓
Promozione controllata in memoria/dataset
↓
Rerun degli stessi benchmark per misurare miglioramento reale
```

### Contratto log per Teacher/Colibrì

Ogni run deve produrre un pacchetto JSON/JSONL leggibile da modelli lenti:

- `case_id`, `benchmark_id`, `source`;
- prompt originale e vincoli;
- expected signals / rubric;
- file generati/modificati;
- diff o snapshot sintetico;
- stdout/stderr test;
- validator deterministici;
- stato runner: `runner_error`, `auto_success`, `auto_failure`;
- domanda esplicita al Teacher: classificare, trovare violazioni, correggere, proporre memoria.

### Output atteso dal reviewer

Formato desiderato:

```json
{
  "verdict": "verified_success | verified_failure | needs_human_review",
  "confidence": 0.0,
  "failure_type": "invented_endpoint | tests_fail | incomplete | unsafe | none",
  "evidence": ["..."],
  "correction": "...",
  "memory_lesson": "...",
  "promote_to_memory": false,
  "rerun_required": true
}
```

### Adapter esterni opzionali

Prevedere adapter opzionali per OpenAI/Claude come check finale o tie-breaker, ma solo con consenso esplicito e policy privacy:

- mai invio automatico di repo/file sensibili;
- redazione/selezione artifact prima dell'invio;
- log di cosa è stato mandato fuori macchina;
- risultato salvato come `external_review`, non come verità assoluta;
- promozione in memoria solo dopo validazione/rerun.

### Priorità implementative

1. Migliorare quality gate: eseguire pytest/test discovery, non solo syntax check.
2. Aggiungere export `teacher_packet.jsonl` per ogni training job.
3. Aggiungere pagina `Training Review` nella desktop app.
4. Implementare `reviewer_adapter` locale per TEACHER.
5. Implementare adapter Colibrì come processo/batch offline.
6. Implementare adapter OpenAI/Claude opzionale con consenso e redazione.
7. Rerun automatico post-correzione sugli stessi benchmark.



## Benchmark ingestion roadmap

Reference: [Training datasets and benchmarks](docs/TRAINING_DATASETS_AND_BENCHMARKS.md).

### 2026-07-15 cleanup implementation note

Implemented first main-page cleanup pass:

- removed raw run log panel from the main workspace;
- removed Cognitive loop, Memory safety, Eval detectors, and Training controls from the main right rail;
- added `/app/diagnostics` as a dedicated placeholder hub for Runs, Training Review, Memory Audit, and Settings;
- made `renderMind()` tolerant of missing diagnostic DOM nodes so the main page can stay clean.

Next UI step: turn `/app/diagnostics` placeholders into real tabs backed by existing APIs (`/api/runs`, `/api/training/overview`, memory status, run events).

### 2026-07-15 diagnostics hub implementation note

La pagina `/app/diagnostics` non è più solo placeholder: ora carica in read-only gli endpoint esistenti `/api/runs`, `/api/training/overview`, `/api/mind/status` e `/api/models/info`. Questo mantiene la home `/app` pulita e sposta run log, training review, memory audit e settings in una control room separata, più vicina alla futura app desktop Tauri.

Prossimo incremento consigliato: aggiungere azioni esplicite e sicure dentro Diagnostics (`run selected training benchmark`, `open run log`, `export teacher packet`) mantenendo conferme e separazione anti-contaminazione.

### 2026-07-15 diagnostics actions implementation note

Diagnostics ora include le prime azioni operative sicure: seed benchmark, avvio mini bench con conferma esplicita, preview log run e export locale. Aggiunto `TrainingStore.export_teacher_packet()` più endpoint `/api/training/export_teacher_packet`, pensato per TEACHER/Colibrì: JSONL evidence-heavy, nessuna promozione automatica, policy anti-contaminazione inclusa in ogni record.

Questo diventa il percorso consigliato per la crescita controllata: DEVIN genera attempts, Diagnostics esporta packet, TEACHER/Colibrì valuta, solo correzioni/verifiche approvate entrano in SFT o memoria recall-safe.

### 2026-07-15 export registry note

Aggiunto registro export read-only: `TrainingStore.list_exports()` e API `/api/training/exports`. Diagnostics mostra gli ultimi JSONL prodotti, formato (`teacher_review_v1` o SFT), righe, dimensione, data e path in tooltip. Questo rende più semplice passare i packet a TEACHER/Colibrì senza cercarli manualmente nel filesystem.

### 2026-07-15 append-only review note

Aggiunto registro review append-only (`reviews.jsonl`) per gli attempt training. Le review non sovrascrivono lo stato originale dell'attempt: un `auto_failure` resta tale, e sopra viene aggiunta una classificazione verificata (`verified_success`, `verified_failure`, `needs_correction`, ecc.) con rationale, reviewer, confidence e tags. Diagnostics permette review rapida sugli attempt recenti.

Questa è la base anti-contaminazione: la memoria non apprende dal risultato grezzo del runner, ma da review validate e tracciabili.

### 2026-07-15 method trace note

Le review append-only ora salvano anche `method_trace`, `failure_mode`, `next_action` e `lesson_candidate`. L'obiettivo non è memorizzare una catena di pensiero lunga, ma una spiegazione operativa verificabile: ipotesi, test eseguito, evidenza osservata, correzione/prossimo passo. Questo aiuta DEVIN a imparare il metodo e non solo la risposta finale.

### 2026-07-15 command palette MVP note

Aggiunta Command Palette in `/app` (`Ctrl/⌘+K`) con azioni sicure: focus composer, nuova chat/progetto, refresh, navigazione Diagnostics/Training/Memory, progetti e run recenti. Non esegue comandi shell e resta adatta al futuro wrapper Tauri/desktop.

### 2026-07-15 project sandbox note

Aggiunta base per Project Sandbox: copia isolata di progetti reali con manifest, skip default per venv/segreti/file pesanti, endpoint `/api/sandbox/prepare` e policy `auto_apply_to_source=false`. Questo prepara il terreno per azioni rischiose in ambienti dedicati e promozione solo via diff/review/test.

### 2026-07-15 linked dependency sandbox note

Aggiunta policy `link_venv` per sandbox leggere: invece di copiare venv enormi, crea symlink al venv originale e registra nel manifest un contratto read-only (`do_not_pip_install_into_linked_venv=true`). Utile per prove rapide; per install/mutazioni dipendenze resta preferibile un venv locale alla sandbox.

### 2026-07-15 Instructor/Crawl4AI adapter note

Aggiunti adapter prioritari senza dipendenze obbligatorie: `devin.ai.structured_contracts` definisce contratti Pydantic Instructor-ready per review/method trace/lesson/crawl records; `devin.ai.crawl_ingestion` aggiunge Crawl4AI opzionale con fallback basic e API `/api/project/knowledge/crawl`. DSPy e Outlines restano nella fase successiva dopo benchmark reali.

### 2026-07-15 structured review/crawl command note

Gli adapter installati ora sono operativi: `/api/training/reviews/structured` importa review validate stile Instructor, e la Command Palette può crawlare una URL nella knowledge del progetto corrente usando Crawl4AI/fallback.

### 2026-07-15 GUI test-ready diagnostics note

Diagnostics ora espone direttamente i flussi pronti per test: Training review, Knowledge crawl e Project sandbox. La home `/app` linka questi pannelli dalla Session sidebar. Questo riduce la dipendenza da endpoint/command palette durante i test manuali e prepara il passaggio a Tauri desktop.

### 2026-07-15 home run cleanup + desktop launcher next

Rimossi Runs/timeline dalla home `/app`: run history, log e diagnostics vivono in `/app/diagnostics#runs`. La home resta workspace/chat first. Prossimo step desktop: script launcher Windows/WSL che avvia FastAPI se necessario e poi apre Tauri su `/app`, per arrivare al doppio click reale.

### 2026-07-15 headless WSL launcher note

Aggiunto launcher PowerShell headless: `scripts/devin-tauri-dev.ps1` avvia FastAPI in WSL con `nohup` e log su `logs/fast_app_headless.log`, attende `/api/health`, poi apre Tauri dev. Aggiunti npm script `desktop:launch` e `backend:headless`.

### 2026-07-15 log retention note

Aggiunto autoclean conservativo dei log: cleanup allo startup backend, endpoint Diagnostics per preview/manual cleanup, policy configurabile con `DEVIN_LOG_AUTOCLEAN`, `DEVIN_LOG_RETENTION_DAYS`, `DEVIN_LOG_KEEP_RECENT_RUNS`. La logica tiene gli ultimi run e i run attivi, e registra il last-opened dei log in un sidecar runtime non versionato.

### 2026-07-15 first clickable desktop launcher note

Aggiunto primo launcher Windows doppio-click (`scripts/DEVIN Desktop.cmd`) per testare la GUI desktop: entra nel repo anche da UNC, avvia backend WSL headless e poi Tauri dev. Aggiunti script npm `desktop:preflight` e `desktop:open`. Prossimo step: build release/installabile con icona e gestione lifecycle backend.

### 2026-07-15 Windows-native desktop host decision

Scelta tecnica per la prima GUI desktop provabile: non lanciare Tauri/npm da `\wsl.localhost`. Aggiunto host Windows nativo in `%LOCALAPPDATA%\DEVIN\desktop-host`, sincronizzato dal repo WSL. Backend WSL resta headless, mentre Tauri gira da path Windows reale. Questo riduce prompt Windows Security e problemi con `.cmd` npm su UNC.


Il launcher consigliato dopo la prima prepare e `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd`, generato automaticamente dal desktop host sync.


### Desktop close cleanup note

La GUI Tauri ora invia `/api/desktop/close_cleanup` alla chiusura. Policy: kill solo dei model server locali gestiti da `LocalModelLauncher`; nessun controllo sui modelli del rig remoto. Env opt-out: `DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS=0`.


### Desktop host log note

Il desktop launcher ora salva transcript e Tauri output in `%LOCALAPPDATA%\DEVIN\logs`, così warning/errori del wrapper Windows-native sono diagnosticabili dopo la chiusura.


### Desktop readiness cockpit note

Aggiunta cockpit di validazione in `/app/diagnostics`: path launcher/log, stato close-cleanup, server locali DEVIN rilevati e azione manuale `Cleanup local models now`. Questo chiude il giro testabile desktop prima del cleanup legacy.


### Desktop validation checkpoints 1-6

Aggiunto `docs/DESKTOP_VALIDATION_CHECKPOINTS.md` come checklist operativa prima del legacy cleanup: launcher, close cleanup, UI polish, agent/diff, training/Teacher, sandbox. Validazione reale eseguita: server locali `coder`/`planner` rilevati e spenti via `/api/desktop/close_cleanup`, backend rimasto vivo.


## FASE 2.13: Workspace responsiveness / linked projects

Completato 2026-07-15:

- project switch usa overview lite, senza scansione file/knowledge;
- home non polla più runs/training: Diagnostics carica solo la tab attiva;
- aggiunto Workspace `Link` per autorizzare cartelle esterne via allowlist;
- crawl/sandbox spiegano il Forbidden e richiedono una cartella collegata.

Prossimo: persistere le linked folders in config locale, così ForgeStudio/altre cartelle restano collegate dopo riavvio backend.
