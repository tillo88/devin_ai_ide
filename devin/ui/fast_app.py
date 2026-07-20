import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Carica .env dalla cartella di QUESTO file (devin/ui/.env), non dalla CWD del
# processo — stesso principio del fix su CONFIG_PATH qui sotto. Se il file non
# esiste, load_dotenv() non fa nulla (nessun errore): restano valide le vere
# variabili d'ambiente di sistema, se qualcuno le usa invece del .env.
from dotenv import load_dotenv
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

print(f"[STARTUP] .env: {'trovato in ' + str(_ENV_PATH) if _ENV_PATH.exists() else 'NON trovato in ' + str(_ENV_PATH)}")
print(f"[STARTUP] TINYFISH_API_KEY: {'presente' if os.getenv('TINYFISH_API_KEY') else 'ASSENTE — la web search TinyFish fallira con questo messaggio esatto'}")
# Bump manuale ad ogni consegna: se dopo un riavvio NON vedi questa riga con la
# build attesa, stai eseguendo una copia vecchia del file (cartella sbagliata?).
print("[STARTUP] build fast_app: 2026-07-10c (Progetti+debug_context+picker)")

# FIX: path assoluto, non piu' relativo alla CWD del processo (era la causa diretta
# di "[FATAL] [Errno 2] No such file or directory: 'config/settings.json'" quando
# il server veniva avviato da una directory diversa dalla root del progetto).
CONFIG_PATH = str(ROOT / "config" / "settings.json")

import json
import socket
import asyncio
import time
import threading
import webbrowser
import subprocess
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Orchestrator resta importabile da fast_app anche se non piu' usato qui:
# i router (runs_core/training/chat) lo risolvono con lazy import a call
# time e i test lo monkeypatchano su fast_app (test_state_persistence).
from devin.core.orchestrator import Orchestrator, LOG_DIR
from devin.core.project_space import ProjectSpace
from devin.core.run_events import RunEventStore
from devin.core.log_retention import LogRetentionPolicy, cleanup_logs
from devin.ai.hybrid_memory_client import HybridMemoryClient, LocalMemoryStore, project_tags
from devin.ai.client import AIClient
from devin.ai.local_model_launcher import LocalModelLauncher, MODELS, kill_server_on_port
from devin.ai.autocomplete import Autocomplete
from devin.memory.taxonomy import (
    EXCLUDED_RECALL_STATUSES,
    MEMORY_KINDS,
    MEMORY_SCHEMA_VERSION,
    RECALLABLE_MEMORY_STATUSES,
)

app = FastAPI(title="DEVIN AI IDE")

# TOKEN GATE (2026-07-18, design approvato dall'owner): segreto condiviso
# richiesto SOLO ai client non-loopback e SOLO se configurato (env
# DEVIN_API_TOKEN > settings.json ui.api_token; assente = gate disabilitato,
# comportamento precedente). Loopback sempre esente: la GUI desktop locale
# resta senza token. Dettagli/threat model: devin/ui/token_gate.py.
from devin.ui.token_gate import TokenGateMiddleware
app.add_middleware(TokenGateMiddleware)

# FIX: base.html (usato da history.html) referenzia url_for('static', ...) per
# css/js (devin/ui/static/css/style.css e js/app.js, gia' presenti e completi
# sul disco), ma non esisteva nessun app.mount("/static", ...) registrato ->
# ad ogni apertura di /history: 500 Internal Server Error
# (starlette.routing.NoMatchFound: No route exists for name "static").
_STATIC_DIR = ROOT / "devin" / "ui" / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# FIX: Disabilita cache Jinja2
from jinja2 import FileSystemLoader, Environment
jinja_env = Environment(
    loader=FileSystemLoader(str(ROOT / "devin/ui/templates")),
    auto_reload=True,
    cache_size=0
)
templates = Jinja2Templates(env=jinja_env)

# === RUNTIME STATE ===
active_runs = {}
runs_lock = threading.Lock()
_run_events = RunEventStore(LOG_DIR)
_model_launcher = None
_ai_client = None
_autocomplete = None


async def _startup_log_retention_cleanup():
    """Best-effort log cleanup: conservative, local-only, and never fatal."""
    try:
        with runs_lock:
            active = list(active_runs.keys())
        summary = await asyncio.to_thread(
            cleanup_logs,
            LOG_DIR,
            policy=LogRetentionPolicy.from_env(),
            active_run_ids=active,
        )
        if summary.get("deleted"):
            print(f"[LOG_RETENTION] deleted {summary['deleted']} old log files ({summary['bytes_deleted']} bytes)")
    except Exception as exc:
        print(f"[LOG_RETENTION] cleanup skipped: {exc}")


app.router.add_event_handler("startup", _startup_log_retention_cleanup)

# === ROUTER ESTRATTI (split 2026-07-18, docs/FAST_APP_SPLIT_PLAN.md) ===
# Split COMPLETO (fetta 15): ZERO route handler in questo modulo — solo app
# assembly, stato condiviso e helper. Path identici ai vecchi @app.* (il
# frontend ha gli URL hardcoded). I router stanno sotto devin/ui/routers/ e
# risolvono lo stato condiviso con lazy import da QUESTO modulo a call time.
from devin.ui.routers.knowledge_misc import router as knowledge_misc_router
from devin.ui.routers.explorer import router as explorer_router
# Fetta split 15: anche /api/training/run e il runner background sono
# rientrati nel router training (che possiede _training_jobs + lock);
# models_desktop legge lo snapshot dei job direttamente dal router.
from devin.ui.routers.training import router as training_router
from devin.ui.routers.workspace import router as workspace_router
from devin.ui.routers.models_desktop import (
    router as models_desktop_router,
    # Re-export shim (piano rischio 1): 6 test chiamano
    # fast_app.api_desktop_close_cleanup() e monkeypatchano gli helper su
    # fast_app; il router li risolve lazy a call time, quindi le patch valgono.
    api_desktop_close_cleanup,
    api_models_kill,
    api_models_status,
)
from devin.ui.routers.status import (
    router as status_router,
    # Re-export shim (come sopra): i test chiamano fast_app.api_desktop_readiness().
    api_desktop_readiness,
    api_health,
    api_mind_status,
    api_models_info,
)
from devin.ui.routers.diff import router as diff_router
from devin.ui.routers.autocomplete import router as autocomplete_router
from devin.ui.routers.plan_terminal import router as plan_terminal_router
from devin.ui.routers.pages import (
    router as pages_router,
    # Re-export shim: i test chiamano fast_app.codex_app_page(...).
    chat_page,
    codex_app_page,
    codex_diagnostics_page,
    favicon,
    history_page,
    index,
)
from devin.ui.routers.projects import (
    router as projects_router,
    # Re-export shim: test_state_persistence chiama fast_app.api_project_last_run.
    api_project_last_run,
)
from devin.ui.routers.runs_read import (
    router as runs_read_router,
    # Re-export shim: test_understory_hybrid chiama fast_app.api_run_events.
    api_run_events,
)
from devin.ui.routers.runs_core import (
    router as runs_core_router,
    # Re-export shim: i test usano fast_app.ResumeRequest/api_run_resume; il
    # router chat chiama api_chat_scaffold(RunRequest(...)) via import diretto
    # router->router (devin.ui.routers.chat, fetta split 14).
    ResumeRequest,
    RunRequest,
    ChangeDecisionRequest,
    api_chat_scaffold,
    api_run,
    api_run_changes_apply,
    api_run_changes_preview,
    api_run_changes_reject,
    api_run_changes_rollback,
    api_run_resume,
    api_stop,
)
from devin.ui.routers.chat import (
    router as chat_router,
    # Re-export shim (piano rischio 1): i test importano questi helper da
    # fast_app (test_understory_hybrid, test_scaffold_resilience); il router
    # projects risolve _read_upload_limited con lazy import da fast_app.
    ChatRequest,
    _format_chat_upload_for_context,
    _is_scaffold_request,
    _read_upload_limited,
    _requires_verified_web_sources,
    api_chat,
)
app.include_router(knowledge_misc_router)
app.include_router(explorer_router)
app.include_router(training_router)
app.include_router(workspace_router)
app.include_router(models_desktop_router)
app.include_router(status_router)
app.include_router(diff_router)
app.include_router(autocomplete_router)
app.include_router(plan_terminal_router)
app.include_router(pages_router)
app.include_router(projects_router)
app.include_router(runs_read_router)
app.include_router(runs_core_router)
app.include_router(chat_router)


def _write_run_log(log_path: Path, message: str, level: str = "info"):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{level.upper()}] {message}\n")


def _record_run_event(run_id: str, message: str, level: str = "info"):
    try:
        _run_events.append_log(run_id, message, level=level)
    except Exception as exc:
        print(f"[RunEvents] append failed: {exc}")


def _make_run_callback(run_id: str, log_path: Path):
    def _callback(msg, level):
        _write_run_log(log_path, msg, level)
        _record_run_event(run_id, msg, level)
    return _callback


def _finish_run_events(run_id: str, status: str, mode: str = "run"):
    try:
        _run_events.finish(run_id, status=status, mode=mode)
    except Exception as exc:
        print(f"[RunEvents] finish failed: {exc}")


def _scaffold_event_status(result: dict) -> str:
    """Stato evidence-aware per la timeline degli scaffold (2026-07-18).

    Uno scaffold consegnato ma SENZA test eseguibili non ha la stessa prova
    di uno con suite verde: la timeline mostra 'syntax_only' invece di un
    'success' indistinto. Il footer 'status: success|failed' del log resta
    invariato (contratto letto da /api/runs e dagli altri parser)."""
    explicit = result.get("status")
    if explicit == "awaiting_approval":
        return "awaiting_approval"
    if not result.get("success"):
        return "failed"
    if (result.get("quality_gate") or {}).get("status") == "verified_success":
        return "verified_success"
    return "syntax_only"


def _get_launcher():
    global _model_launcher
    if _model_launcher is None:
        try:
            _model_launcher = LocalModelLauncher.from_config(CONFIG_PATH)
        except Exception as e:
            print(f"[WARN] Could not init launcher: {e}")
    return _model_launcher


def _get_ai_client():
    global _ai_client
    if _ai_client is None:
        _ai_client = AIClient()
    return _ai_client


def _get_autocomplete():
    # FIX (bug 1.2 report): riusa il client/istanza singleton invece di ricrearla
    # ad ogni keystroke-trigger (ogni AIClient() nuovo fa 2x health-check + WOL).
    global _autocomplete
    if _autocomplete is None:
        _autocomplete = Autocomplete(ai_client=_get_ai_client())
    return _autocomplete



def _build_mind_status() -> Dict[str, Any]:
    """Structured, lightweight state for the future Codex-like right-side Mind panel.

    This endpoint must stay fast: it avoids initializing models or probing remote
    memory backends. Deep health checks remain available through dedicated
    endpoints such as /api/health and /api/models/status.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except Exception:
        config = {}

    launcher = _model_launcher
    launcher_status = None
    if launcher:
        try:
            launcher_status = launcher.get_status()
        except Exception:
            launcher_status = None

    local_memory = LocalMemoryStore(config).status()

    return {
        "agent": {
            "name": "DEVIN",
            "role": "coding/debugging agent",
            "target_experience": "Codex/Claude Desktop-like local coding workspace",
            "desktop_shell_target": "Tauri",
        },
        "loop": [
            "perceive", "frame", "plan", "act", "verify", "reflect", "remember", "surface"
        ],
        "capabilities": {
            "discussion": True,
            "project_context": True,
            "operational_scaffold_routing": True,
            "quality_gate": True,
            "eval_learning": True,
            "web_search": config.get("web_search", {}).get("provider", "none"),
        },
        "models": {
            "health": {"checked": False, "see": "/api/health"},
            "launcher_source": getattr(launcher_status, "model_source", "unavailable") if launcher_status else "unavailable",
            "local_running": getattr(launcher_status, "local_running", {}) if launcher_status else {},
            "vram": _get_vram_info(),
        },
        "memory": {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "local": local_memory,
            "backend": "local-first",
            "reachable": local_memory.get("enabled", False),
            "remote_checked": False,
            "recall_safe_statuses": sorted(RECALLABLE_MEMORY_STATUSES),
            "review_only_statuses": sorted(EXCLUDED_RECALL_STATUSES),
            "kinds": sorted(MEMORY_KINDS),
            "anti_contamination_rule": (
                "Only verified_success, verified_failure, and human_confirmed are recall-safe; "
                "hypotheses/pending/quarantine remain review-only until promoted."
            ),
        },
        "evals": {
            "active_detectors": [
                "scaffold_quality_gate",
                "chat_only_output_detector",
                "runtime_cuda_fallback_detector",
            ],
            "failure_policy": "Save failures as negative lessons with evidence and retry rules.",
        },
        "ui": {
            "current_surface": "FastAPI/Jinja web UI",
            "next_surface": "single-page Codex-like workspace",
            "future_shell": "Tauri",
            "panels": ["workspace", "conversation/work-stream", "mind/context"],
        },
    }


def _get_vram_info():
    """Ritorna VRAM info da nvidia-smi se disponibile."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if lines:
            parts = [p.strip() for p in lines[0].split(",")]
            return {
                "gpu_name": parts[0],
                "total_mb": int(float(parts[1])),
                "used_mb": int(float(parts[2])),
                "free_mb": int(float(parts[3]))
            }
    except Exception:
        pass
    return None


# Chiave riservata per la persistenza della chat "generale" (nessun project_path
# scelto dall'utente). Prima d'ora, in questo caso la history non veniva mai
# salvata server-side: tornando da Dashboard/History a Chat, la conversazione
# spariva (era solo nella variabile JS della pagina precedente, azzerata dal
# reload). Usata SOLO per la persistenza qui sotto — non cambia in nulla il
# routing verso lo Zero-Shot Scaffolding, che continua a girare sul
# project_path originale mandato dal client (vuoto = mai scaffolding, invariato).
# FIX (2026-07-09, modalita' Progetti): path ASSOLUTO ancorato a ROOT — prima era
# relativo alla CWD del processo (sfuggito al giro dei fix sui path assoluti):
# lanciando il server da devin/ui/ la chat generale finiva in
# devin/ui/workspace/_general_chat invece che in <root>/workspace/_general_chat.
GENERAL_CHAT_PROJECT_KEY = str(ROOT / "workspace" / "_general_chat")

_automem_client = None


def _get_automem():
    global _automem_client
    if _automem_client is None:
        _automem_client = HybridMemoryClient(_get_ai_client().config)
    return _automem_client


def _get_model_detail(alias: str, info: dict) -> dict:
    """Costruisce dettaglio completo di un modello.

    FONTE DI VERITA': local_model_launcher.MODELS (li' vive la logica reale di
    selezione file/fallback/jinja/mmproj). settings.json e' usato SOLO per la
    descrizione testuale — prima invece il nome file veniva letto da settings.json,
    che poteva divergere da cio' che e' davvero in esecuzione (es. mostrava il
    vecchio 'qwen coder' mentre girava Ornith).
    """
    client = _get_ai_client()
    config = client.config.get("models", {})
    local_models = config.get("local_models", {})
    config_key = "reasoning" if alias in ("planner", "reasoning") else "coder"
    model_cfg = local_models.get(config_key, {})

    detail = {
        "alias": alias,
        "port": info.get("port", "N/A"),
        "status": info.get("status", "unknown"),
        "online": info.get("status") == "running",
        "description": model_cfg.get("description", ""),
        "ctx_size": model_cfg.get("ctx_size", "N/A"),
        "is_fallback_active": False,
        "vision_enabled": False,
    }

    # Leggi il file REALE dal launcher (non da settings.json)
    try:
        from devin.ai import local_model_launcher as lml
        launcher_cfg = lml.MODELS.get(alias, {})
        real_file = launcher_cfg.get("file")
        if real_file is not None:
            detail["file"] = real_file.name
            detail["active_file"] = real_file.name
            detail["ctx_size"] = launcher_cfg.get("ctx", detail["ctx_size"])
            # Vision reale = mmproj presente sul disco per questo alias
            mmproj = launcher_cfg.get("mmproj")
            detail["vision_enabled"] = bool(mmproj and mmproj.exists())
            if detail["vision_enabled"]:
                detail["vision_mmproj"] = mmproj.name
            detail["jinja"] = bool(launcher_cfg.get("jinja"))
            # "Fallback attivo" = il file scelto NON e' il primario preferito.
            # Per il coder: primario = Ornith; per il planner: primario = MoE.
            if alias == "coder":
                detail["is_fallback_active"] = (real_file != lml.CODER_ORNITH)
            elif alias == "planner":
                detail["is_fallback_active"] = (real_file != lml.PLANNER_MOE)
    except Exception as e:
        # Fallback alla vecchia lettura da settings.json se il launcher non e' importabile
        detail["file"] = model_cfg.get("file", "unknown")
        detail["active_file"] = detail["file"]
        print(f"[_get_model_detail] warning: impossibile leggere dal launcher ({e})")

    return detail


# NOTA (split 2026-07-18): _scan_project_files e _read_file_content sono stati
# spostati in devin/ui/routers/explorer.py insieme agli endpoint /api/explore,
# /api/file e /api/file/save (move puro). La guardia _safe_under_allowed resta
# qui: condivisa da projects/workspace/chat/runs/training e importata dai test.


# NOTA (split 2026-07-18, fetta 14): la sezione CHAT completa (ChatRequest,
# /api/chat SSE, /api/chat/vision, /api/chat/document, /api/chat/search,
# /api/chat/history x3), gli helper chat-only (_detect_mode,
# _is_scaffold_request, _wants_web_search, _is_trivial_message,
# _build_search_query, _requires_verified_web_sources,
# _scaffold_web_reference) e il blocco upload (costanti MAX_*,
# _looks_textual, _truncate_attachment_text, _format_chat_upload_for_context,
# _read_upload_limited) sono stati spostati in devin/ui/routers/chat.py
# (move puro, path invariati). Le dipendenze condivise restano qui e sono
# risolte dal router con lazy import a call time; fast_app re-esporta gli
# helper usati dai test e da routers/projects.py (vedi blocco include).
# Anche /api/chat/generate_patch (fetta 15) vive ora in routers/chat.py:
# questo modulo non contiene piu' NESSUN route handler.


# ============================================================
# API - MODALITA' PROGETTI (istruzioni + knowledge + chat multiple + AutoMem)
# ============================================================

# Cache delle istanze ProjectSpace: il VectorStore dentro puo' tenere caricato
# il modello di embedding (sentence-transformers ~80MB) — ricrearlo ad ogni
# messaggio costerebbe secondi di latenza per niente. Le istanze sono condivise:
# un upload/delete di knowledge resetta l'indice sulla STESSA istanza che la
# chat usa per il retrieval (coerenza automatica).
_project_spaces: Dict[str, ProjectSpace] = {}


def _validated_project_path(project_path: str, allow_general: bool = True) -> str:
    if not project_path and allow_general:
        return str(Path(GENERAL_CHAT_PROJECT_KEY).expanduser().resolve())
    safe = _safe_under_allowed(project_path)
    if safe is None:
        roots = sorted(str(root) for root in _ALLOWED_ROOTS)
        raise HTTPException(
            status_code=403,
            detail={
                "error": "project_path non consentito",
                "hint": "Usa un progetto in workspace/ oppure collega la cartella dal picker prima di usare crawl/sandbox.",
                "project_path": project_path,
                "allowed_roots": roots,
            },
        )
    return str(safe)


def _project_space_for(project_path: str) -> ProjectSpace:
    key = _validated_project_path(project_path)
    if key not in _project_spaces:
        _project_spaces[key] = ProjectSpace(key)
    return _project_spaces[key]


WORKSPACE_DIR = ROOT / "workspace"

# === SICUREZZA (#8 audit) — path traversal su /api/explore e /api/file ===
# Senza guardia, ?path=/etc/passwd (o ?path=../../..) legge file arbitrari del
# sistema. Root consentiti: workspace/ (progetti interni) + le cartelle che
# l'utente COLLEGA esplicitamente dal picker "Sfoglia". resolve() normalizza
# '..' e risolve i symlink, quindi blocca anche i link che puntano fuori.
_ALLOWED_ROOTS = {WORKSPACE_DIR.resolve()}
_LINKED_PROJECT_ROOTS: list[Path] = []


def _register_allowed_root(path_str: str) -> bool:
    """Autorizza una cartella collegata dall'utente (dal picker) come root
    leggibile via /api/explore e /api/file per la durata del processo.

    Ritorna True se la root e' stata registrata. FIX (2026-07-18): prima i
    rifiuti erano ingoiati in silenzio (`except: pass`), quindi una cartella
    scelta dal picker risultava "collegata" ma ogni lettura finiva in 403
    senza alcuna traccia. Ora ogni rifiuto e' loggato con il motivo."""
    try:
        p = Path(path_str).expanduser().resolve()
        if not p.is_dir():
            print(f"[SECURITY] allowed-root NON registrata (non e' una directory esistente): {path_str}")
            return False
        _ALLOWED_ROOTS.add(p)
        if p != WORKSPACE_DIR.resolve() and p not in _LINKED_PROJECT_ROOTS:
            _LINKED_PROJECT_ROOTS.append(p)
        return True
    except Exception as e:
        print(f"[SECURITY] allowed-root NON registrata ({type(e).__name__}: {e}): {path_str}")
        return False


def _safe_under_allowed(path_str: str) -> Optional[Path]:
    """Ritorna il path risolto SOLO se cade sotto un root consentito, altrimenti
    None. Da usare come gate in tutti gli endpoint che leggono file su path
    fornito dal client."""
    if not path_str:
        return None
    try:
        p = Path(path_str).expanduser().resolve()
    except Exception:
        return None
    for root in _ALLOWED_ROOTS:
        if p == root or root in p.parents:
            return p
    return None


_CROSS_CHAT_PHRASES = [
    "altra chat", "altre chat", "altra conversazione", "altre conversazioni",
    "chat precedente", "chat di prima", "avevamo detto", "abbiamo detto",
    "ne avevamo parlato", "come dicevamo", "nella conversazione", "guarda nelle chat",
]


def _wants_cross_chat(message: str) -> bool:
    msg = (message or "").lower()
    return any(p in msg for p in _CROSS_CHAT_PHRASES)


def _build_project_context(message: str, persistence_key: str,
                            req_project_path: Optional[str],
                            current_chat_id: str = "") -> tuple:
    """Costruisce i blocchi di contesto della modalita' Progetti (istruzioni,
    knowledge, file, progetti collegati, AutoMem). Ritorna (parts, debug_str).
    Fail-soft: qualsiasi errore degrada a lista vuota, mai eccezioni.
    Usata da api_chat E da /api/project/debug_context (stessa identica logica:
    quello che vedi nel debug e' quello che riceve il modello)."""
    parts = []
    dbg = {"instructions": 0, "description": 0, "pinned": 0, "knowledge": 0, "files": 0, "linked": [], "automem": 0, "errors": []}
    ai = _get_ai_client()
    try:
        ps = _project_space_for(persistence_key)
        ps_cfg = ai.config.get("project_space", {})

        description = ps.get_description()
        if description:
            parts.append(f"DESCRIZIONE DEL PROGETTO (scopo/stack):\n{description}")
            dbg["description"] = len(description)

        instructions = ps.get_instructions()
        if instructions:
            parts.append(f"ISTRUZIONI DEL PROGETTO:\n{instructions}")
            dbg["instructions"] = len(instructions)

        # FILE PINNATI (★): SEMPRE nel contesto, contenuto attuale (troncato). A
        # differenza della knowledge (retrieval per rilevanza) questi ci sono
        # SEMPRE — servono a non far "dimenticare" al modello com'è fatto un file
        # chiave (modulo principale, spec, contratto API). Esattamente il buco
        # visto nel run Pint (self.display_text allucinato).
        try:
            pinned = ps.read_pinned(max_chars_per_file=ps_cfg.get("pin_max_chars", 4000))
            if pinned:
                blocks = [f"### {p['path']}\n{p['content']}" for p in pinned]
                parts.append("FILE PINNATI (★ sempre nel contesto, contenuto attuale):\n"
                             + "\n\n".join(blocks))
                dbg["pinned"] = len(pinned)
        except Exception as e:
            dbg["errors"].append(f"pinned: {e}")

        knowledge = ps.retrieve_context(
            message,
            top_k=ps_cfg.get("knowledge_top_k", 4),
            max_chars=ps_cfg.get("knowledge_retrieve_chars", 3500),
        )
        if knowledge:
            parts.append(
                "CONOSCENZA DEL PROGETTO (estratti rilevanti alla domanda, "
                f"potrebbero essere parziali):\n{knowledge}")
            dbg["knowledge"] = len(knowledge)

        # FILE del progetto corrente: SEMPRE consultati insieme alla knowledge
        # (2026-07-10: prima era un fallback "solo se knowledge vuota" — bastava
        # una knowledge irrilevante, es. un CHECKSUMS.txt caricato per prova,
        # per oscurare del tutto i file veri del progetto).
        if req_project_path:
            files_ctx = ps.retrieve_from_files(message, top_k=3, max_chars=1500)
            if files_ctx:
                parts.append(
                    f"CONTENUTO DAI FILE DEL PROGETTO (estratti rilevanti):\n{files_ctx}")
                dbg["files"] = len(files_ctx)

        # Progetti connessi (citati per nome nel messaggio)
        for linked_path in _detect_linked_projects(message, persistence_key):
            linked_name = Path(linked_path).name
            try:
                linked_ps = _project_space_for(linked_path)
                # Knowledge E file, COMBINATI (2026-07-10): con l'`or` di prima una
                # knowledge irrilevante (es. CHECKSUMS.txt) faceva corto circuito e
                # i file veri (note_progetto.txt) non venivano mai letti.
                linked_blocks = []
                kn = linked_ps.retrieve_context(message, top_k=2, max_chars=800)
                if kn:
                    linked_blocks.append(kn)
                fl = linked_ps.retrieve_from_files(message, top_k=2, max_chars=800)
                if fl:
                    linked_blocks.append(fl)
                linked_ctx = "\n\n---\n\n".join(linked_blocks)
                if linked_ctx:
                    parts.append(
                        f"CONOSCENZA DAL PROGETTO COLLEGATO '{linked_name}' "
                        f"(citato nel messaggio):\n{linked_ctx}")
                    dbg["linked"].append(f"{linked_name}:kn{len(kn)}+file{len(fl)}ch")
                else:
                    file_list = linked_ps.list_files()
                    if file_list:
                        parts.append(
                            f"IL PROGETTO COLLEGATO '{linked_name}' (citato nel messaggio) "
                            f"contiene questi file: {', '.join(file_list)}. "
                            "Nessun estratto rilevante alla domanda e' stato trovato nel loro contenuto; "
                            "puoi elencare i file all'utente e chiedere quale approfondire.")
                        dbg["linked"].append(f"{linked_name}:lista-{len(file_list)}file")
                    else:
                        parts.append(
                            f"IL PROGETTO COLLEGATO '{linked_name}' (citato nel messaggio) "
                            "esiste ma e' vuoto o non contiene file di testo leggibili.")
                        dbg["linked"].append(f"{linked_name}:vuoto")
            except Exception as le:
                dbg["errors"].append(f"linked {linked_name}: {le}")

        # CROSS-CHAT (epic Progetti, 2026-07-16): su richiesta esplicita
        # ("cosa avevamo detto nell'altra chat") DEVIN recupera snippet
        # rilevanti dalle ALTRE conversazioni del progetto. Gated da frase per
        # non iniettare rumore ad ogni messaggio; esclude la chat corrente.
        if req_project_path and _wants_cross_chat(message):
            try:
                cross = ps.build_cross_chat_context(
                    message, exclude_chat_id=current_chat_id or "", max_chars=1500)
                if cross:
                    parts.append(cross)
                    dbg["cross_chat"] = len(cross)
            except Exception as ce:
                dbg["errors"].append(f"cross_chat: {ce}")

        automem = _get_automem()
        memories = automem.recall(message, tags=project_tags(persistence_key), limit=3)
        if memories:
            mem_budget = ai.config.get("automem", {}).get("recall_max_chars", 800)
            mem_text = "\n- ".join(m.strip() for m in memories)[:mem_budget]
            parts.append(f"MEMORIE RILEVANTI (AutoMem):\n- {mem_text}")
            dbg["automem"] = len(mem_text)
    except Exception as e:
        dbg["errors"].append(str(e))

    # Elenco progetti del workspace SEMPRE nel contesto (2026-07-10): senza,
    # il modello nega l'esistenza di progetti che non hanno prodotto estratti
    # ("non ho nessun test_project") — e noi non distinguiamo un retrieval
    # fallito da un progetto davvero inesistente.
    try:
        names = [d.name for d in WORKSPACE_DIR.iterdir()
                 if d.is_dir() and not d.name.startswith(("_", ".")) and d.name != "sandbox"]
        if names:
            parts.append("PROGETTI DI CODICE NEL WORKSPACE (NON è il limite di ciò che sai: "
                         "la KNOWLEDGE e i risultati WEB sono fonti separate, non ristrette a "
                         "questa lista): " + ", ".join(sorted(names))
                         + ". Se l'utente ne cita uno, i suoi estratti (se trovati) sono qui sopra.")
            dbg["workspace_projects"] = names
    except Exception as e:
        dbg["errors"].append(f"lista workspace: {e}")

    # Preambolo anti-"non ho accesso": se c'e' QUALSIASI contesto di progetto,
    # dillo esplicitamente al modello — i modelli piccoli tendono a rispondere
    # "non posso accedere ai tuoi file" anche con gli estratti davanti.
    if parts:
        parts.insert(0,
            "Hai davanti diverse FONTI di contesto qui sotto: file dei progetti, documenti "
            "di KNOWLEDGE del progetto, file PINNATI e (se presenti) risultati dal WEB. Usa "
            "tutto ciò che è rilevante e, se puoi, di' da quale fonte viene. NON dire che non "
            "hai accesso quando il contesto rilevante è presente qui sotto. Se un'informazione "
            "NON è in nessuna fonte qui, dillo onestamente invece di inventarla — e non "
            "contraddire ciò che hai appena affermato basandoti sul contesto.")
    return parts, json.dumps(dbg, ensure_ascii=False)


# NOTA (split 2026-07-18, fetta 15 — FINALE): TUTTA la superficie training
# (stato `_training_jobs` + lock, helper, 14 endpoint CRUD, `/api/training/run`
# e il runner background `_run_training_cases_background`) vive in
# devin/ui/routers/training.py. Il runner risolve le dipendenze del run-core
# (Orchestrator, LOG_DIR, _run_events, active_runs/runs_lock, ...) con lazy
# import da fast_app a thread-run time, come runs_core.


# NOTA (split 2026-07-18): gli endpoint /api/youtube/transcript, /api/docs_cache/*
# e /api/sandbox/prepare sono stati estratti in devin/ui/routers/knowledge_misc.py
# (move puro, path invariati). _docs_cache() vive ora in quel modulo.


def _detect_linked_projects(message: str, current_key: str) -> list:
    """Progetti 'connessi' (2026-07-10): se il messaggio nomina un altro progetto
    del workspace, la sua knowledge viene consultata insieme a quella corrente.
    Match per nome cartella (word-boundary, case-insensitive, nomi >= 4 char per
    evitare falsi positivi), max 2 progetti per non sfondare il contesto."""
    import re as _re
    linked = []
    if not WORKSPACE_DIR.exists():
        return linked
    current = Path(current_key).name.lower()
    msg_lower = message.lower()
    for d in WORKSPACE_DIR.iterdir():
        if len(linked) >= 2:
            break
        if not d.is_dir() or d.name.startswith(("_", ".")) or d.name == "sandbox":
            continue
        name = d.name.lower()
        if name == current or len(name) < 4:
            continue
        # match sul nome esatto o sul nome con separatori normalizzati ("mio_prog" ~ "mio prog")
        pattern = _re.escape(name).replace(r"\_", r"[\s_\-]").replace(r"\-", r"[\s_\-]")
        if _re.search(rf"(?<![\w]){pattern}(?![\w])", msg_lower):
            linked.append(str(d))
    return linked


# ============================================================
# API - MODELS
# ============================================================


def _known_local_model_servers() -> dict[str, dict[str, Any]]:
    running: dict[str, dict[str, Any]] = {}
    for alias, config in MODELS.items():
        port = int(config.get("port", 0) or 0)
        if not port:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                running[alias] = {"alias": alias, "port": port, "source": "known_local_port"}
        finally:
            sock.close()
    return running


def _shutdown_known_local_model_servers() -> list[str]:
    killed: list[str] = []
    for alias, config in MODELS.items():
        port = int(config.get("port", 0) or 0)
        if not port:
            continue
        if alias in _known_local_model_servers():
            kill_server_on_port(port)
            killed.append(alias)
    return killed


def _rig_self_hosted() -> bool:
    """True se il backend gira SUL rig (settings.json -> models.rig_self_hosted).

    In quel caso la chiusura della GUI sul PC NON deve spegnere il backend:
    e' un servizio condiviso (dashboard, bot Telegram) che vive sul rig."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return bool((cfg.get("models") or {}).get("rig_self_hosted", False))
    except Exception:
        return False


# ============================================================
# MAIN + AUTO-OPEN
# ============================================================

def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def run_server():
    """
    Avvio completo del server (browser auto-open + uvicorn con shutdown pulito).
    Estratto in funzione richiamabile cosi' launcher.py puo' avviare QUESTA dashboard
    (non la vecchia UI Tkinter in devin/ui/app.py) con un semplice import + call.
    """
    import uvicorn

    URL = "http://localhost:5000"

    def open_browser():
        time.sleep(2)
        if _is_wsl():
            # L'interop WSL->Windows per aprire il browser (via rundll32.exe) e'
            # inaffidabile e scrive errori direttamente su stderr, non intercettabili
            # da un try/except Python. Piu' robusto saltarlo del tutto su WSL.
            print(f"\n-- WSL rilevato: apri manualmente {URL} nel browser")
            return
        print(f"\n-- Opening browser at {URL}")
        try:
            webbrowser.open(URL)
        except Exception as e:
            print(f"\n-- Impossibile aprire il browser automaticamente ({e}). Apri manualmente: {URL}")

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    # FIX: le connessioni SSE aperte (/stream/{run_id}, polling ogni 5s da piu' tab)
    # impedivano allo shutdown grazioso di uvicorn di completarsi entro tempi ragionevoli
    # su Ctrl+C (richiedeva pkill). timeout_graceful_shutdown basso + os._exit di
    # sicurezza garantiscono che il processo termini sempre entro ~3s.
    # FIX audit #7 (2026-07-10): default 127.0.0.1 — prima 0.0.0.0 esponeva a
    # TUTTA la LAN (senza auth) lettura file, avvio agenti, stop modelli. Per
    # l'accesso da altre macchine (es. dashboard sul rig raggiunta dalla
    # workstation) imposta "ui": {"host": "0.0.0.0"} in settings.json — scelta
    # esplicita, non default.
    try:
        _ui_cfg = json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8")).get("ui", {})
    except Exception:
        _ui_cfg = {}
    _host = _ui_cfg.get("host", "127.0.0.1")
    if _host == "0.0.0.0":
        print("[SECURITY] UI esposta su tutta la rete (ui.host=0.0.0.0 in settings.json): "
              "assicurati di essere su una LAN fidata.")
    config = uvicorn.Config(app, host=_host, port=int(_ui_cfg.get("port", 5000)),
                            log_level="info", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[SHUTDOWN] Chiusura forzata (i modelli locali/rig restano attivi in background)...")
        os._exit(0)


if __name__ == "__main__":
    run_server()
