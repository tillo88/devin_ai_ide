# DEVIN AI IDE — Stato del Progetto
**Ultimo aggiornamento:** 2026-07-17

📚 **Indice completo della documentazione: [`docs/INDEX.md`](docs/INDEX.md)**

Punti chiave attuali:

- target prodotto: app desktop **Tauri** stile Codex/Claude Desktop; `/app` è la superficie web/dev; verso l'**eseguibile Windows** (vedi [`docs/PACKAGING-ROADMAP.md`](docs/PACKAGING-ROADMAP.md));
- training: **quality gate multi-livello implementato** (pytest reale + gold test + tree-sitter + bandit + validator semantici), review Teacher/umana, niente promozione automatica di materiale non verificato — dettaglio in [`docs/TRAINING.md`](docs/TRAINING.md);
- baseline test suite: ~122 passed, 1 skipped; baseline MBPP col gate severo ~53% (numero da battere post-LoRA);
- UI Tkinter e web_app Flask **archiviati** in `archive/legacy/` (2026-07-17): l'unico entry vivo è `devin/ui/fast_app.py`;
- repo DEVIN corretto: `/home/tillo/devin_ai_ide` su WSL `Ubuntu`; non confondere con ISO rig su `Ubuntu-24.04`.
- log operativo datato: `docs/CONTINUITY_*.md`.

---

## Cos'è
Agente AI che automatizza lo sviluppo software: legge il progetto, pianifica, genera patch in formato unified diff, le applica in sandbox, esegue i test, e itera fino a risoluzione. Architettura multi-agente con routing automatico tra modelli locali (workstation) e rig esterno (~51GB VRAM).

**Flusso:** Planner → Coder → Patcher → Runner → Critic → loop (max 3 retry)

---

## Infrastruttura Hardware

### Macchina primaria (WSL2 Ubuntu)
- **GPU:** NVIDIA GeForce RTX 5070 Ti 16GB (Blackwell, CUDA 12.8, SM 12.0)
- **Ruolo:** IDE locale, llama-server per modelli locali, Web UI FastAPI
- **Modelli locali:**
  - Qwen2.5-Coder-7B Q4_K_M → porta 8000 (coder)
  - Qwen3.5-14B-A3B MoE / Qwen3-14B Q4_K_M fallback → porta 8001 (reasoning/planner)
  - Vision supportato con mmproj Q8_0

### Rig esterno — gestito da ai-rig-iso-build (fonte di verità: vedi ~/ai-rig-iso-build/README.md)
- **IP fisso:** 192.168.1.100, porta 8080 (un solo ruolo attivo alla volta, boot triplo)
- **CPU:** Intel i9-10900X (X299), 32GB DDR4 (espandibile a 64GB) — *corretto: non è un i5-9600K, refuso della prima stesura*
- **GPU:** 2× GTX 1080 8GB, 1× GTX 1080Ti 11GB, 1× RTX A2000 6GB, 2× GTX 1660Super 6GB, 1× GTX 1660Ti 6GB → ~51GB VRAM totale (solo la A2000 ha Tensor Core reali: le 1660 sono TU116, senza)
- **Ruolo usato da DEVIN AI IDE:** `devin` — Ornith-1.0-35B-A3B (MoE, coding+reasoning nella stessa istanza), vedi `config/roles/devin.env` nel progetto ai-rig-iso-build
- **Gestione:** llama-server nativo (beellama/mainline intercambiabili), WOL abilitato, switch di ruolo via bot Telegram (`/devin`) o `grub-reboot devin && reboot`
- ⚠️ **Vincolo importante:** il rig esegue UN SOLO ruolo alla volta (devin/hermes/teacher). Se il rig è in ruolo `hermes` o `teacher` (es. mentre usi ForgeStudio), DEVIN AI IDE non trova il modello su :8080 e va in fallback locale — serve passare a `/devin` prima di lavorare qui.

---

## Struttura Progetto

```
devin_ai_ide/
├── config/
│   └── settings.json              # Configurazione modelli, porte, path
├── devin/
│   ├── agents/
│   │   ├── planner.py             # Genera piano step-by-step (reasoning)
│   │   ├── coder.py               # Genera unified diff (coder)
│   │   ├── critic.py              # Analizza errori e propone correzione
│   │   └── prompts.py             # System prompts per tutti gli agenti (inglese)
│   ├── ai/
│   │   ├── client.py              # AIClient: routing rig → locale → OpenAI
│   │   ├── stream.py              # Generatore streaming token-by-token
│   │   ├── stream_console.py      # Console interattiva con streaming
│   │   ├── autocomplete.py        # Suggerimenti codice inline
│   │   ├── router.py              # Selezione modello per task/complexity
│   │   └── local_model_launcher.py # Avvio/health-check llama-server locale + OOM fallback
│   ├── core/
│   │   ├── orchestrator.py          # Loop principale: Planner→Coder→Patcher→Runner→Critic
│   │   ├── context_engine.py      # Raccolta e scoring file progetto
│   │   ├── context_retriever.py   # Ricerca semantica via VectorStore
│   │   └── workspace.py           # Gestione directory workspace
│   ├── engine/
│   │   ├── patcher.py             # Applica diff: git apply → patch → Python fallback (strict + fuzzy)
│   │   ├── runner.py              # Esegue progetto in sandbox (pip + python3)
│   │   ├── sandbox.py             # Crea sandbox isolata, esclude workspace/venv/.git
│   │   ├── shell.py               # Esecuzione comandi shell con timeout
│   │   └── git_ops.py             # Init repo, commit automatico
│   ├── graph/
│   │   ├── code_graph.py          # AST graph: funzioni per file
│   │   └── semantic_graph.py      # AST graph: funzioni + classi
│   ├── memory/
│   │   ├── vector_store.py        # Embedding engine (sentence-transformers → sklearn → keyword)
│   │   ├── eval_recorder.py       # Eval recorder + routing is_operational_build_request
│   │   └── taxonomy.py            # Tassonomia memorie (recall-safe vs review-only)
│   ├── ui/
│   │   ├── fast_app.py            # FastAPI app: dashboard IDE, API, SSE streaming ⭐ PRINCIPALE (unico entry)
│   │   ├── static/               # JS/CSS dashboard (codex_app.*, codex_diagnostics.*)
│   │   └── templates/             # HTML Jinja2 per Web UI
│   │   # UI Tkinter (app/main/editor/diff_viewer/stream_console) + web_app Flask -> archive/legacy/ (2026-07-17)
│   │       ├── base.html
│   │       ├── index.html         # Dashboard IDE (form task, log streaming, file explorer)
│   │       ├── chat.html          # Chat interattiva con streaming SSE
│   │       └── history.html       # Storico run
│   └── devin_models/              # Modelli GGUF locali
│       ├── qwen2.5-coder-7b-instruct-q4_k_m.gguf   (~4.4GB)
│       ├── qwen2.5-coder-7b-instruct-q5_k_m.gguf   (~5.1GB, fallback)
│       ├── qwen3-14b-q4_k_m.gguf                    (~8.5GB, fallback planner)
│       ├── Qwen3.5-14B-A3B-Claude-Opus-Reasoning-Distilled-4.6-MXFP4_MOE.gguf (~8.4GB, primary planner)
│       └── Qwen3.5-35B-A3B-Claude-Opus-Reasoning-Distilled-4.6-mmproj-q8_0.gguf (~585MB, vision)
├── workspace/                     # Progetti utente + sandbox runtime
├── logs/                          # Log llama-server (generati a runtime)
├── launcher.py                    # Launcher con sys.path fix
├── scripts/
│   ├── run.sh                     # Avvio Linux/macOS
│   └── run.bat                    # Avvio Windows
├── dump_progetto_ibrido.py        # Genera project_dump.txt per context LLM
├── clean_files.py                 # Pulizia Zone.Identifier + file duplicati
├── test_orchestrator_e2e.py      # Test E2E con modelli mockati
├── test_pipeline.py               # Test manuale Planner→Coder (legacy)
├── test_streaming.py              # Test unitari streaming
└── requirements.txt               # Dipendenze Python
```

---

## Dipendenze

```bash
# CUDA 12.8 (consigliato per RTX 5070 Ti / Blackwell SM 12.0)
# PyTorch con CUDA 12.8:
pip install torch==2.11.0+cu128 torchvision==0.26.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128

# Altre dipendenze:
pip install -r requirements.txt
# openai>=1.0.0 requests flask numpy scikit-learn fastapi uvicorn python-multipart
# Opzionali per semantic search: sentence-transformers
```

---

## Avvio

```bash
# 1. Avvio modelli locali (opzionale — fast_app.py li avvia automaticamente)
python devin/ai/local_model_launcher.py

# 2. Avvio Web UI (FastAPI, porta 5000)
python devin/ui/fast_app.py
# Apri http://localhost:5000

# 3. Avvio console streaming CLI
python -m devin.ai.stream_console
```

---

## Modalità Progetti (2026-07-09, stile Claude Projects)

Ogni progetto ha una cartella `.devin/` (viaggia col progetto) con:

- **Chat multiple** (`.devin/chats/<id>.json`) — sidebar in `/chat`: crea/rinomina/elimina conversazioni. Senza chat selezionata resta la vecchia sessione singola (`.devin_chat/session.json`, retrocompatibile).
- **Knowledge** (`.devin/knowledge/`) — file allegati permanenti (testo + PDF/DOCX/XLSX/PPTX via `document_extract.py`). NON iniettata intera (ctx 8192): retrieval semantico via `VectorStore` con budget caratteri (`settings.json → project_space`). `.devin/` è escluso da context_engine/sandbox: la knowledge non inquina il Coder.
- **Istruzioni** (`.devin/instructions.md`) — system prompt per-progetto, sommato a `chat.system_prompt` globale.
- **AutoMem** (`devin/ai/automem_client.py`) — recall automatico + bottone "💾 ricorda" per lo store manuale. Tag `devin` + `project:<nome>`, fail-soft a rig spento. ⚠️ Nomi campi API da verificare al primo uso col rig acceso (nota nel modulo).
- **Export harness** (`.devin/export/dataset_*.jsonl`) — bottone in sidebar: tutte le chat del progetto in JSONL formato OpenAI (system = istruzioni progetto), pronto per LoRA/harness futuro.

File nuovi: `devin/core/project_space.py`, `devin/ai/automem_client.py`. Modificati: `chat_persistence.py` (param `chat_id`), `fast_app.py` (endpoint `/api/project/*`, iniezione contesto, fix `GENERAL_CHAT_PROJECT_KEY` relativo alla CWD), `chat.html` (sidebar), `context_engine.py`/`sandbox.py` (esclusione `.devin`), `config/settings.json` (sezioni `automem` e `project_space`).

## Cosa è stato fatto

| # | Fix | File | Dettaglio |
|---|-----|------|-----------|
| 1 | Fix ricorsione infinita sandbox | `sandbox.py` | `shutil.copytree(project_path, sandbox_path)` dove sandbox_path è dentro project_path → ricorsione infinita. Fix: copia selettiva di primo livello, esclude workspace/, venv/, .git/, __pycache__/ |
| 2 | Fix SyntaxError launcher | `local_model_launcher.py` | `LauncherStatus` nel ramo `else` di `ensure_models()` mancava virgola dopo `local_running={...}` → SyntaxError all'import |
| 3 | Fix LOG_DIR e signature | `orchestrator.py` | LOG_DIR esportato; signature `run()` aggiornata con `entrypoint`, `max_attempts`, `max_seconds`, `run_id` |
| 4 | Fix applicazione patch | `patcher.py` | Verifica hash MD5 pre/post patch. Se nessun file cambia, fallback a `patch -p1`, poi fallback Python con fuzzy matching (cerca sottostringhe) |
| 5 | Fix sync sandbox→progetto | `orchestrator.py` | `_sync_sandbox_to_project()` copia file .py modificati dalla sandbox al progetto originale prima del git commit |
| 6 | Prompt in inglese + rigidi | `prompts.py` | Prompt in inglese (modelli code-trained ragionano meglio), regole esplicite su match carattere-per-carattere, esempio con commento incluso nella linea `-`, output SOLO diff senza markdown |
| 7 | Estrazione diff robusta | `coder.py` | `_extract_diff()` con regex che cerca blocchi markdown, pattern `diff --git`, o `---/+++`. Log di debug con conteggio linee |
| 8 | Streaming reale | `client.py` | `stream()` con `requests.post(stream=True)`, parsing SSE `data:` token-by-token, fallback automatico rig→locale |
| 9 | Fix import Web UI + Tkinter | `web_app.py`, `app.py`, `main.py` | Usano `Orchestrator` con context manager invece di `run` modulo |
| 10 | Test E2E con modelli reali | — | Bug calc.py fixato in ~81s, 1 attempt |
| 11 | Dashboard IDE completa | `index.html` + `fast_app.py` | Form task, log streaming SSE in tempo reale, file explorer con syntax highlight, lista run recenti con azioni |
| 12 | Chat con streaming SSE | `chat.html` + `fast_app.py` | Modalità auto/reasoning/coder, badge modello con tooltip, stats TPS/tokens/tempo |
| 13 | File explorer API | `fast_app.py` | Endpoint `/api/explore` e `/api/file` per navigare e visualizzare file progetti |

---

## Roadmap — In Sospeso

> ⚠️ **Le tabelle qui sotto sono lo stato storico di inizio luglio.** Da allora sono
> stati completati: quality gate multi-livello, teacher review queue, gold test,
> loop/self-heal, docs cache internet-first, adapter MBPP, tree-sitter + bandit.
> Stato corrente e prossimi passi: [`docs/INDEX.md`](docs/INDEX.md), [`docs/TRAINING.md`](docs/TRAINING.md),
> [`docs/PACKAGING-ROADMAP.md`](docs/PACKAGING-ROADMAP.md) e i `CONTINUITY_*`.

### 🔥 FASE A — Robustezza

| # | Task | Stato | Note |
|---|------|-------|------|
| 11 | Gestione OOM e swap modelli | 🟡 Parziale | VRAM check c'è (`_get_vram_mb` + `_resolve_model_file`), manca auto-switch a fallback in runtime |
| 12 | Retry con backoff esponenziale | 🟡 Parziale | Launcher ha backoff 2^attempt, ma AIClient riprova immediatamente su timeout |
| 13 | Persistenza stato orchestratore | 🔴 Manca | Se crash a metà loop, salva `state.json` con attempt corrente, ultimo errore, patch generata. Al riavvio riparte da dove era |

### 🚀 FASE B — Feature IDE

| # | Task | Stato | Note |
|---|------|-------|------|
| 14 | Web UI IDE funzionante E2E | ✅ Completato | `/api/run`, `/api/stop`, `/stream/{run_id}`, file explorer, log streaming in tempo reale |
| 15 | Vector store attivo | 🟡 Parziale | `index_project()` chiamato in orchestrator, ma verifica ricerca semantica non testata end-to-end |
| 16 | Autocomplete inline | 🔴 Manca | `Autocomplete` esiste ma non integrato in nessuna UI |

### 🔌 FASE C — Integrazione Rig

| # | Task | Stato | Note |
|---|------|-------|------|
| 17 | Health check rig con retry | 🔴 Manca | `AIClient.refresh()` fa singolo ping. Se rig in WOL sleep, fallback a locale è immediato |
| 18 | Hot-swap modello sul rig | 🔴 Manca | `switch-model-fixed.sh` esiste sul rig ma non chiamato da DEVIN |
| 19 | Pipeline fine-tuning | 🔴 Manca | Portare schema JSONL per LoRA da ForgeStudio |

---

## Test Suite

```bash
# Test unitari streaming (nessun server necessario)
python test_streaming.py

# Test E2E con modelli mockati (nessun server, nessuna GPU)
python test_orchestrator_e2e.py

# Test E2E con modelli reali (richiede llama-server su 8000/8001)
rm -rf workspace/test_project
mkdir -p workspace/test_project
cat > workspace/test_project/calc.py <<'EOF'
def add(a, b):
    return a - b  # BUG
EOF
cat > workspace/test_project/main.py <<'EOF'
from calc import add
assert add(2, 3) == 5
print("OK")
EOF
python -c "
from devin.core.orchestrator import Orchestrator
with Orchestrator(project_path='workspace/test_project') as orch:
    result = orch.run('Fix the bug in calc.py')
print('Success:', result['success'])
print('Duration:', round(result['duration'], 1), 's')
" 2>&1
```

---

## Note Tecniche

### Calibrazione patcher.py
Il fallback Python fuzzy usa `strip()` per il matching. Se un bug richiede cambiare spaziatura (es. indentazione), il fuzzy match potrebbe non trovarlo. In quel caso il fallback fallisce e il Critic deve guidare il Coder a generare una diff con contesto esatto.

### Limite coder.py
`_extract_diff()` cerca pattern `diff --git` o `---/+++`. Se il modello genera una diff malformata senza questi header (es. solo `+` e `-` senza header), l'estrazione fallisce e ritorna stringa vuota → orchestrator salta al Critic.

### VRAM stimata (locale)
| Modello | VRAM |
|---------|------|
| Qwen3-14B Q4_K_M | ~9.5 GB |
| Qwen2.5-Coder-7B Q4_K_M | ~5.0 GB |
| **Totale** | **~14.5 / 16 GB** |

Se OOM con Q5 del Coder, il launcher fa fallback automatico a Q4 via `_resolve_model_file()`.

### Istanziare orchestratore con SSE callback
```python
from devin.core.orchestrator import Orchestrator

def sse_callback(msg, level):
    print(f"[{level}] {msg}")

with Orchestrator(
    config_path="config/settings.json",
    project_path="workspace/my_project",
    sse_callback=sse_callback
) as orch:
    result = orch.run("Refactor main.py to use async/await")
```

---

## Changelog

- **2026-07-17** — Pulizia per packaging: UI Tkinter + web_app Flask archiviati (`archive/legacy/`), doc consolidati con `docs/INDEX.md`, training doc unificato in `docs/TRAINING.md`, IP rig aggiornato a 192.168.1.100, roadmap installer in `docs/PACKAGING-ROADMAP.md`.
- **2026-07-16** — Quality gate multi-livello (pytest reale + gold + tree-sitter + bandit + validator), teacher review queue, loop/self-heal, docs cache internet-first, GUI 3 colonne.
- **2026-07-02 06:38** — Dashboard IDE completa: form task, log streaming SSE, file explorer, run history. FastAPI come server principale. README aggiornato.
- **2026-07-02** — Fix sandbox ricorsione, patcher MD5+fallba ck, prompt inglese, streaming reale, test E2E
- **2026-06-28** — Setup iniziale progetto, architettura multi-agente, routing rig/locale
