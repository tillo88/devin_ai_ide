# DEVIN AI IDE — SPEC TECNICA COMPATTA
*Ultimo aggiornamento: 2026-07-02 11:53 | v22 | FASE 2 COMPLETATA*

---

## 0. STATO ATTUALE — FASE 2 ✅ COMPLETATA

| # | Task | Stato | File modificati |
|---|------|-------|-----------------|
| 11 | OOM + swap modelli | ✅ Completato | `local_model_launcher.py`, `orchestrator.py` |
| 12 | Retry backoff exp + Circuit Breaker | ✅ Completato | `client.py` |
| 13 | Persistenza stato | ✅ Completato | `orchestrator.py`, `state_persistence.py` |
| 15 | Vector store E2E | ✅ Completato | `vector_store.py`, `test_vector_store_e2e.py` |
| **16** | **Autocomplete inline (Monaco Editor)** | **✅ Completato** | **`autocomplete.py`, `fast_app.py`, `index.html`** |
| 18 | Hot-swap modello rig | 🔴 Posticipato | `client.py` — rig offline |

**Test E2E**: 5/5 passati ✅ (Fase 1)

---

## 1. ARCHITETTURA (3 layer)

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER UI          fast_app.py (FastAPI) — PRINCIPALE       │
│  (porta 5000)    web_app.py (Flask, dep), Tkinter legacy    │
│                    SSE streaming, file explorer, chat, runs   │
│                    **Monaco Editor + AI Inline Autocomplete** │
├─────────────────────────────────────────────────────────────┤
│  LAYER CORE        orchestrator.py — LOOP PRINCIPALE        │
│                    Planner→Coder→Patcher→Runner→Critic      │
│                    state_persistence.py — crash recovery      │
│                    context_engine.py + context_retriever.py   │
├─────────────────────────────────────────────────────────────┤
│  LAYER AI          client.py — routing rig→locale→OpenAI    │
│                    local_model_launcher.py — llama-server     │
│                    stream.py, autocomplete.py ⭐, router.py   │
├─────────────────────────────────────────────────────────────┤
│  LAYER ENGINE      patcher.py — git apply → patch → Python  │
│                    runner.py — sandbox exec + pip install     │
│                    sandbox.py — copia selettiva, no ricorsione│
│                    shell.py — comandi con timeout             │
│                    git_ops.py — auto-init + commit            │
├─────────────────────────────────────────────────────────────┤
│  LAYER MEMORY      vector_store.py — semantic search (ST→   │
│                    sklearn→keyword fallback)                  │
│                    brain.json — esperienze passate (legacy)   │
├─────────────────────────────────────────────────────────────┤
│  LAYER GRAPH       code_graph.py — AST funzioni per file    │
│                    semantic_graph.py — AST funzioni+classi    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. TASK 16 — AUTOCOMPLETE INLINE (COMPLETATO)

### 2.1 Overview
Integrazione di **Monaco Editor** (il motore di VS Code) nel file explorer della dashboard, con autocomplete AI inline stile Copilot. Il modello **Coder locale** (Qwen2.5-Coder-7B @ :8000) genera suggerimenti in streaming.

### 2.2 File modificati

#### `devin/ai/autocomplete.py`
```python
class Autocomplete:
    def suggest(self, code, language="python", cursor_position=None) -> str
    def suggest_stream(self, code, language="python", cursor_position=None) -> Iterator[str]
    def _build_prompt(self, code, language, cursor_position) -> str
```
- **Prompt ottimizzato**: "You are an expert {language} programmer..."
- **Contesto**: ultime 1500 chars prima del cursore
- **Regole**: solo codice, no markdown, no spiegazioni, 1-5 linee max
- **Streaming**: usa `AIClient.stream()` con `mode="coder"`

#### `devin/ui/fast_app.py`
Nuovi endpoint:
```python
POST /api/autocomplete        # Esistente — sincrono (fallback)
POST /api/autocomplete/stream # NUOVO — SSE streaming per Monaco
```
```python
class AutocompleteStreamRequest(BaseModel):
    code: str
    language: str = "python"
    cursor_position: int = None
```

#### `devin/ui/templates/index.html`
- **Monaco Editor** via CDN (`monaco-editor@0.45.0`)
- **Tema custom** `devin-dark` con colori matching l'IDE
- **`inlineSuggest: { enabled: true }`** — CRITICO per ghost text
- **Inline Completions Provider** (`registerInlineCompletionsProvider`) — API nativa Copilot-style
- **Ghost text fallback** (`deltaDecorations`) — compatibile v0.45
- **Cache locale** suggerimenti (max 8 entry, TTL 10s)
- **Trigger automatico** su: `.`, `(`, `[`, `{`, `=`, `:`, `,`, spazio dopo parola
- **Trigger manuale**: `Ctrl+Space` o bottone "✨ AI Complete"
- **Accept**: `Tab` (comportamento nativo Monaco)
- **Status indicator** in basso a destra: idle / loading / ready / error
- **Bottone 🧪 Test** — verifica connessione backend AI
- **Debug logging** — console F12 per troubleshooting

### 2.3 Flusso Autocomplete

```
Utente digita nel Monaco Editor
    ↓
[Trigger char?] → Debounce 400ms → fetchAICompletionInternal()
    ↓
POST /api/autocomplete/stream → SSE streaming
    ↓
Backend: Autocomplete.suggest_stream() → AIClient.stream(mode="coder")
    ↓
Modello Coder locale genera tokens in streaming
    ↓
Frontend riceve, forma suggestion, mostra ghost text
    ↓
  ├─ Via Inline Provider (se supportato)
  └─ Via deltaDecorations (fallback manuale)
    ↓
Utente preme Tab → suggestion inserita
```

### 2.4 Configurazione Monaco (CRITICO)
```javascript
monaco.editor.create(container, {
    theme: 'devin-dark',
    fontFamily: 'Fira Code, Consolas, monospace',
    minimap: { enabled: true },
    bracketPairColorization: { enabled: true },
    quickSuggestions: false,  // Disabilitato: usiamo AI custom

    // CRITICO: senza questo, ghost text non appare MAI
    inlineSuggest: {
        enabled: true,
        showToolbar: 'onHover',
        mode: 'subword',
        suppressSuggestions: false,
    },
});

// Provider inline completions
monaco.languages.registerInlineCompletionsProvider('*', {
    provideInlineCompletions: async (model, position, context, token) => {...}
});
```

### 2.5 Language Support
| Estensione | Linguaggio Monaco |
|---|---|
| `.py` | `python` |
| `.js` | `javascript` |
| `.ts` | `typescript` |
| `.html` | `html` |
| `.css` | `css` |
| `.json` | `json` |
| `.yaml/.yml` | `yaml` |
| `.md` | `markdown` |
| `.sh` | `shell` |
| `.sql` | `sql` |
| `.cpp/.c/.h` | `cpp` / `c` |
| `.java` | `java` |
| `.go` | `go` |
| `.rs` | `rust` |
| Altri | `plaintext` |

### 2.6 Troubleshooting

| Problema | Soluzione |
|---|---|
| Ghost text non appare | Verifica `inlineSuggest.enabled: true` in console (F12) |
| Provider non registrato | Controlla log `[DEVIN][INLINE] Provider registered` |
| Backend non risponde | Clicca 🧪 Test, verifica modello Coder sia running |
| Cache browser vecchia | **Ctrl+F5** (hard refresh) |
| Monaco non si carica | Verifica connessione internet (CDN required) |

---

## 3. HARDWARE & MODELLI (invariato dalla Fase 1)

### Workstation primaria (WSL2)
- **GPU:** RTX 5070 Ti 16GB (Blackwell, CUDA 12.8, SM 12.0)
- **VRAM totale:** ~14.5/16GB con entrambi i modelli → **SERIALIZZAZIONE ATTIVA**
- **Modelli locali:**
  - Coder: Qwen2.5-Coder-7B Q4_K_M @ :8000 (~5.2GB VRAM) ← **Usato per autocomplete**
  - Reasoning: Qwen3.5-14B-A3B MoE @ :8001 (~5.5GB VRAM)
  - Vision: mmproj Q8_0 per Qwen3.5-14B

### Rig esterno (192.168.1.100) — OFFLINE
- **Stato:** WOL disabilitato in config fino a riattivazione
- **Task 18 posticipato**

---

## 4. NOTE CRITICHE AGGIORNATE (Fase 2)

1. **patcher.py fuzzy limitato:** usa `strip()` → cambi indentazione non matcha → Critic deve guidare Coder a diff esatta
2. **coder.py extraction:** se diff malformata → ritorna "" → orchestrator salta al Critic
3. **sandbox anti-ricorsione:** copia solo primo livello, esclude `workspace/`
4. **state atomic write:** `write(.tmp) → rename(.json)` per evitare stati corrotti
5. **WOL throttling:** `_last_wol_time` → min 5 min tra WOL
6. **Circuit breaker:** dopo 3 fallimenti rig → 60s ban → fallback immediato a locale
7. **VRAM locale totale:** ~14.5/16GB. `serialize_vram_heavy_models: true` evita OOM
8. **Context max:** 100k chars, 60 file, 12k chars per file
9. **GitOps:** auto-init repo se mancante, commit con messaggio "Devin Auto-Commit: {task}"
10. **VectorStore cache:** persistenza in `.devin_cache/semantic_index.pkl`
11. **TF-IDF config:** `min_df=1`, `stop_words=None` per testi corti e multilingual
12. **VRAM Watchdog:** thread daemon, 30s interval
13. **⚡ Autocomplete inline:** richiede `inlineSuggest.enabled: true` nelle opzioni Monaco
14. **⚡ Autocomplete ghost text:** usa `deltaDecorations` (compatibile v0.45), non `createDecorationsCollection`
15. **⚡ Autocomplete trigger:** smart — `.`, `(`, `[`, `{`, `=`, `:`, `,`, spazio, newline, o Ctrl+Space
16. **⚡ Autocomplete accept:** `Tab` per accettare, `Esc` per dismiss
17. **⚡ Autocomplete debug:** F12 → Console per log dettagliati `[DEVIN][*]`

---

## 5. AVVIO RAPIDO (aggiornato)

```bash
# Modelli locali (opzionale, fast_app li avvia auto)
python devin/ai/local_model_launcher.py

# Web UI (FastAPI, porta 5000)
python devin/ui/fast_app.py
# → http://localhost:5000
# → File Explorer → seleziona progetto → clicca file .py
# → Monaco Editor con AI Autocomplete:
#    - Scrivi codice → aspetta 400ms dopo trigger char
#    - O premi Ctrl+Space per forzare
#    - Ghost text grigio appare → Tab per accettare

# Test E2E mock (nessun server)
python test_orchestrator_e2e.py
python test_vector_store_e2e.py
```

---

## 6. ROADMAP — PROSSIMA FASE

| # | Task | Stato | File target | Note |
|---|------|-------|-------------|------|
| 16 | **Autocomplete inline** | ✅ **Completato** | `autocomplete.py`, `fast_app.py`, `index.html` | Monaco + AI streaming |
| 18 | Hot-swap modello rig | 🔴 Posticipato | `client.py`, `fast_app.py` | Rig offline, riattivare WOL |
| 19 | Pipeline fine-tuning | 🔴 Manca | `finetune/dataset_builder.py` | LoRA da esperienze passate |
| — | UI Optimization | 🔴 Manca | templates, static/js/css | Alpine.js/HTMX, dark mode |
| — | File save API | 🔴 Manca | `fast_app.py` | POST /api/file/save per edit in-place |

**Rig esterno:** tornerà online tra ~1 settimana. Quando pronto:
- Aggiornare `rig_mac` in settings.json
- Riattivare `wol_enabled: true`
- Testare Task 18 (Hot-swap)

---

*Fine spec Fase 2. Per domande su implementazione specifica, indicare file:linea.*
