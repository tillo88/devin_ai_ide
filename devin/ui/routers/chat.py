"""Router chat: chat SSE, vision/document/search, history, upload in chat.

Quattordicesimo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md) — fetta chat core. Move puro: path e
comportamento identici, nessun rename.

Le dipendenze condivise RESTANO in fast_app e sono risolte con lazy import a
CALL TIME (dentro gli handler): `_validated_project_path`, `_get_launcher`,
`_get_ai_client`, `_get_automem`, `GENERAL_CHAT_PROJECT_KEY`,
`_build_project_context`. Cosi' i test che monkeypatchano `fast_app.*`
continuano a valere. `_get_automem` e' importato lazy una volta a livello
handler e catturato dalla closure SSE (nessun test lo patcha mid-stream).

`/api/chat` chiama `api_chat_scaffold(RunRequest(...))` DIRETTAMENTE
(handler->handler): import top-level da routers/runs_core (direzione
sicura router->router, nessun import top-level di fast_app nei router).
vision/document/search chiamano `api_chat(req)` come chiamata locale —
esattamente come avveniva dentro fast_app.

fast_app re-esporta (shim): `api_chat`, `ChatRequest`, `_is_scaffold_request`,
`_format_chat_upload_for_context`, `_requires_verified_web_sources`,
`_read_upload_limited` (quest'ultimo risolto lazy da routers/projects.py).

Fetta split 15 (FINALE, 2026-07-18): anche `/api/chat/generate_patch` vive
qui. Come da contratto originale NON registra `_run_events.start` e NON
chiama `_finish_run_events` (preservato verbatim). Dipendenze lazy da
fast_app: `LOG_DIR`/`_make_run_callback`/`_validated_project_path`
nell'handler; `Orchestrator`/`CONFIG_PATH`/`active_runs`/`runs_lock` dentro
la closure `_bg` (risolti a thread-run time, pattern runs_core).
"""

import asyncio
import base64
import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from devin.ai.document_extract import extract_text as extract_document_text
from devin.ai.web_search import (
    fetch_top_results,
    format_results_as_context,
    get_web_search_provider,
)
from devin.core.chat_persistence import ChatPersistence
from devin.core.chat_continuity import (
    build_checkpoint,
    checkpoint_needs_refresh,
    context_from_checkpoint,
    should_checkpoint,
)
from devin.memory.eval_recorder import (
    detect_chat_only_output,
    is_operational_build_request,
    record_eval_result,
)
from devin.ui.routers.runs_core import RunRequest, api_chat_scaffold, api_run

router = APIRouter()


def _detect_mode(message: str) -> str:
    """Rileva se la domanda richiede reasoning o coding."""
    msg_lower = message.lower()
    coding_keywords = [
        "code", "codice", "python", "function", "def ", "class ",
        "bug", "fix", "patch", "diff", "write a", "scrivi", "implementa",
        "crea una funzione", "crea una classe", "refactor", "debug",
        "syntax", "import ", "error", "exception", "traceback",
        "javascript", "html", "css", "sql", "api", "json", "xml",
        "loop", "array", "dict", "list", "tuple", "async", "await"
    ]
    reasoning_keywords = [
        "explain", "spiega", "why", "perche", "how does", "come funziona",
        "architecture", "design", "pattern", "best practice", "approccio",
        "strategia", "piano", "analizza", "compare", "confronta",
        "philosophy", "concept", "theory", "principle"
    ]

    coding_score = sum(1 for k in coding_keywords if k in msg_lower)
    reasoning_score = sum(1 for k in reasoning_keywords if k in msg_lower)

    if coding_score > reasoning_score:
        return "coder"
    elif reasoning_score > coding_score:
        return "reasoning"

    return "coder"


def _is_scaffold_request(message: str, project_path: str) -> bool:
    '''Euristica per il routing chat -> scaffolding.

    Caso leggero: progetto vuoto/mancante + verbo di creazione.
    Lo scaffold e' riservato a un progetto vuoto/mancante. Le richieste
    operative su un progetto che contiene gia' codice devono passare dal run
    di manutenzione, che preserva l'architettura esistente e usa la pipeline
    sandbox -> test -> diff -> approvazione.
    '''
    if not project_path:
        return False

    path = Path(project_path).expanduser()
    is_empty_or_missing = (not path.exists()) or (path.is_dir() and not any(path.rglob("*.py")))

    scaffold_verbs = [
        "crea un progetto", "crea una app", "crea un'app", "crea un'applicazione",
        "crea una applicazione", "scaffold", "build a project", "create a project",
        "genera un progetto", "starter", "boilerplate", "da zero"
    ]
    msg_lower = message.lower()
    has_scaffold_intent = any(v in msg_lower for v in scaffold_verbs)
    has_strong_operational_intent = is_operational_build_request(message)

    return is_empty_or_missing and (has_scaffold_intent or has_strong_operational_intent)


_RETRY_PHRASES = {"riprova", "riprova adesso", "riprova ora", "ritenta", "prova ancora",
                  "prova di nuovo", "di nuovo", "ancora", "retry", "try again", "riprova pure"}

# Segnali chiari di "mi serve il web": se il toggle 🌐 e' spento ma il messaggio
# contiene uno di questi, la ricerca si attiva DA SOLA (2026-07-10 — l'utente
# chiedeva il meteo col toggle spento e il modello, senza dati, si inventava
# finti curl). Lista volutamente conservativa: meglio un falso negativo che
# ricerche a sorpresa su ogni messaggio.
_WEB_INTENT_PHRASES = [
    "cerca su internet", "cercare su internet", "cerca sul web", "cerca online",
    "su internet", "sul web", "guarda online", "che tempo fa", "che tempo farà",
    "meteo", "previsioni", "notizie", "ultime news", "risultati di oggi",
    "risultati dei", "quanto costa", "prezzo attuale", "quotazione", "classifica di",
    # Focus coding (2026-07-10 — DEVIN non e' la chat generica, quello e' Hermes):
    "documentazione di", "docs di", "documentazione ufficiale", "ultima versione di",
    "versione attuale di", "changelog", "breaking changes", "come si installa",
    "come si usa la libreria", "esempi di utilizzo di", "api di", "release notes",
]


def _wants_web_search(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in _WEB_INTENT_PHRASES)


# Messaggi banali (saluti/ack): anche col web search acceso di default NON ha
# senso cercarli sul web — solo latenza sprecata e rumore nel contesto del
# modello. Lista volutamente stretta (match esatto): meglio cercare di più che
# saltare per errore una domanda vera.
_TRIVIAL_MESSAGES = {
    "ciao", "salve", "ehi", "hey", "hello", "buongiorno", "buonasera", "buonanotte",
    "grazie", "grazie mille", "ok", "okay", "perfetto", "va bene", "bene",
    "come stai", "come va", "test", "prova", "ci sei",
}


def _is_trivial_message(message: str) -> bool:
    m = message.strip().lower().rstrip("!?. ")
    return len(m) <= 2 or m in _TRIVIAL_MESSAGES


def _build_search_query(message: str, history: list) -> str:
    """Query di ricerca CONTESTUALE (2026-07-10): se il messaggio e' un follow-up
    corto/generico ("riprova adesso"), cerca l'argomento vero nell'ultimo
    messaggio utente sostanziale della conversazione — altrimenti si finirebbe
    a cercare letteralmente "riprova adesso" sul web."""
    msg = message.strip()
    normalized = msg.lower().strip("!?.,; ")
    meaningful_words = [w for w in normalized.split() if len(w) > 2]
    is_generic = normalized in _RETRY_PHRASES or len(meaningful_words) < 3
    if not is_generic:
        return msg
    for m in reversed(history or []):
        if m.get("role") != "user":
            continue
        prev = (m.get("content") or "").strip()
        prev_norm = prev.lower().strip("!?.,; ")
        if prev and prev_norm not in _RETRY_PHRASES and len(prev.split()) >= 3:
            # cap: nei messaggi storici possono esserci allegati interi
            return prev[:300]
    return msg


def _requires_verified_web_sources(message: str) -> bool:
    """True when proceeding without current sources would violate the request."""
    msg = (message or "").lower()
    strict_phrases = (
        "documentazione ufficiale", "fonti ufficiali", "esclusivamente documentazione",
        "solo fonti ufficiali", "verifica sul web", "verificati online",
    )
    return any(phrase in msg for phrase in strict_phrases)


async def _scaffold_web_reference(message: str, ai) -> str:
    """Collect compact real web evidence before a scaffold plan is generated."""
    provider = get_web_search_provider(ai.config)
    results = await asyncio.to_thread(provider.search, message, max_results=5)
    if not results:
        raise RuntimeError("nessun risultato web disponibile")

    web_context = format_results_as_context(results)
    ws_cfg = ai.config.get("web_search", {})
    if ws_cfg.get("fetch_pages", True):
        pages = await asyncio.to_thread(
            fetch_top_results,
            results,
            max_pages=ws_cfg.get("fetch_max_pages", 2),
            max_chars_per_page=ws_cfg.get("fetch_chars_per_page", 2500),
            engine=ws_cfg.get("fetch_engine", "requests"),
        )
        if pages:
            web_context = f"{web_context}\n\n{pages}"

    cap = int(ws_cfg.get("max_context_chars", 5000))
    return web_context[:cap]


# ============================================================
# API - CHAT (SSE VELOCE) + VISION + WEB SEARCH + SCAFFOLD ROUTING
# ============================================================

class ChatRequest(BaseModel):
    message: str
    mode: str = "auto"
    image_base64: Optional[str] = None
    project_path: Optional[str] = None
    use_web_search: bool = False
    history: Optional[list] = None  # [{"role": "user"/"assistant", "content": "..."}], gestito dal frontend
    chat_id: Optional[str] = None   # modalita' Progetti: conversazione specifica (.devin/chats/<id>.json)


@router.post("/api/chat")
async def api_chat(req: ChatRequest):
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        GENERAL_CHAT_PROJECT_KEY,
        _build_project_context,
        _get_ai_client,
        _get_automem,
        _get_launcher,
        _validated_project_path,
    )
    message = req.message.strip()
    if not message:
        return {"error": "empty message"}
    if req.project_path:
        req.project_path = _validated_project_path(req.project_path, allow_general=False)

    # Regola "Chat First": per lo scaffolding l'eventuale ricerca web deve avvenire
    # PRIMA del routing. Prima questo return anticipato saltava interamente Web Search.
    if req.project_path and _is_scaffold_request(message, req.project_path):
        scaffold_task = message
        web_requested = req.use_web_search or _wants_web_search(message)
        if web_requested:
            try:
                ai = _get_ai_client()
                reference = await _scaffold_web_reference(message, ai)
                scaffold_task = (
                    f"{message}\n\n"
                    "=== WEB REFERENCE RACCOLTA PRIMA DELLO SCAFFOLDING ===\n"
                    f"{reference}\n"
                    "=== REGOLE DI PROVENIENZA ===\n"
                    "Usa solo fatti sostenuti dalle fonti sopra. Conserva URL e limiti nel "
                    "progetto. Non sostituire endpoint mancanti con API o settori non richiesti. "
                    "Se le fonti non bastano, crea una matrice esplicita degli UNKNOWN invece "
                    "di inventare dati."
                )
            except Exception as exc:
                if _requires_verified_web_sources(message):
                    return {
                        "error": (
                            "Scaffolding bloccato correttamente: il task richiede fonti web "
                            f"verificate ma la ricerca non e' riuscita ({exc})."
                        )
                    }
                print(f"[Scaffold Web] ricerca non disponibile, proseguo senza: {exc}")
        return await api_chat_scaffold(RunRequest(path=req.project_path, task=scaffold_task))

    # Un progetto esistente non deve mai finire nello Zero-Shot Scaffolding:
    # quel percorso pianifica una nuova struttura. Le richieste che chiedono
    # modifiche reali vengono avviate come manutenzione e restano soggette a
    # sandbox, test e approvazione esplicita.
    if req.project_path and is_operational_build_request(message):
        return await api_run(RunRequest(path=req.project_path, task=message))

    # Vision rimosso da DEVIN (2026-07-09): nessun modello locale/rig di questo
    # progetto ha piu' --mmproj caricato. Un'immagine qui non verrebbe letta dal
    # modello (che risponderebbe a caso ignorandola) - meglio un errore chiaro
    # che una risposta silenziosamente sbagliata. Per immagini: usa Hermes.
    if req.image_base64:
        return {"error": "Vision non disponibile su DEVIN AI IDE — usa Hermes (ruolo dedicato del rig) per immagini."}

    selected_mode = _detect_mode(message) if req.mode == "auto" else req.mode

    launcher = _get_launcher()
    if launcher:
        # #10: ensure_models può avviare/attendere i server modelli (blocking) → thread
        await asyncio.to_thread(launcher.ensure_models)

    ai = _get_ai_client()

    # Persistenza caricata PRIMA del blocco web-search: serve a _build_search_query
    # per ricavare l'argomento della conversazione sui follow-up corti ("riprova").
    persistence_key = req.project_path or GENERAL_CHAT_PROJECT_KEY
    chat_persistence = ChatPersistence(persistence_key, chat_id=req.chat_id)
    persisted_history = chat_persistence.load()
    chat_persistence.append("user", message)  # subito, sopravvive anche a crash mid-stream

    # Auto-attivazione ricerca web su intento esplicito (toggle spento ma la
    # domanda chiede chiaramente dati dal web). L'utente viene avvisato via SSE.
    auto_web_enabled = False
    if not req.use_web_search and _wants_web_search(message):
        req.use_web_search = True
        auto_web_enabled = True

    # Web search acceso di default (toggle ON nel frontend): salta comunque la
    # ricerca sui messaggi banali (saluti/ack) — niente latenza né rumore su "ciao".
    if req.use_web_search and _is_trivial_message(message):
        req.use_web_search = False

    content = message
    web_search_error = None
    if req.use_web_search:
        try:
            provider = get_web_search_provider(ai.config)
            # FIX (2026-07-10): la query era il messaggio LETTERALE — con follow-up
            # tipo "riprova adesso" cercava... "riprova adesso" (risultati: Reverso,
            # gruppi Facebook di linguistica). Se il messaggio e' corto/generico,
            # la query viene costruita dall'ultimo messaggio utente sostanziale
            # della conversazione (l'argomento vero), + il messaggio corrente.
            search_query = _build_search_query(message, persisted_history)
            # #10: la ricerca web (rete) è bloccante → thread
            results = await asyncio.to_thread(provider.search, search_query, max_results=5)
            web_context = format_results_as_context(results)
            # Fetch del CONTENUTO dei top risultati (2026-07-10): senza, il modello
            # ha solo titolo+snippet e improvvisa (caso skysport/mondiali). Fail-soft:
            # pagine bloccate/JS-only vengono saltate, restano gli snippet.
            ws_cfg = ai.config.get("web_search", {})
            if ws_cfg.get("fetch_pages", True):
                # #10: fetch pagine (rete + eventuale Chromium) bloccante → thread
                page_content = await asyncio.to_thread(
                    fetch_top_results,
                    results,
                    max_pages=ws_cfg.get("fetch_max_pages", 2),
                    max_chars_per_page=ws_cfg.get("fetch_chars_per_page", 2500),
                    engine=ws_cfg.get("fetch_engine", "requests"))
                if page_content:
                    web_context = f"{web_context}\n\n{page_content}"
                else:
                    # Niente contenuto pagine (siti bloccati/timeout): dillo al
                    # modello, altrimenti riempie i buchi INVENTANDO risultati
                    # dettagliati (visto coi mondiali: partite mai giocate).
                    web_context += ("\n\n[NOTA: il contenuto delle pagine non era accessibile — "
                                    "hai SOLO i titoli/snippet qui sopra. Rispondi solo con cio' "
                                    "che gli snippet dicono davvero e DICHIARA che i dettagli "
                                    "non sono verificabili. NON inventare risultati, numeri o nomi.]")
            # Cap difensivo (2026-07-11): sul fallback locale il modello ha una
            # finestra piccola; un web_context troppo lungo faceva sforare il
            # contesto → HTTP 400 dal server. Limitiamo il totale iniettato.
            web_ctx_cap = ws_cfg.get("max_context_chars", 5000)
            if len(web_context) > web_ctx_cap:
                web_context = web_context[:web_ctx_cap] + "\n[...risultati web troncati per stare nel contesto...]"
            content = f"Risultati ricerca web:\n{web_context}\n\nDomanda utente: {message}"
        except Exception as e:
            # FIX: prima l'errore finiva "[Web search non disponibile: {e}]" DENTRO
            # il content mandato al modello — che lo ignorava e rispondeva a caso,
            # lasciando l'utente senza alcun segnale visibile del perche'. Ora e'
            # tracciato a parte e mandato come evento SSE distinto (vedi sotto),
            # il messaggio al modello resta quello originale (nessun rumore extra).
            web_search_error = str(e)

    # (audit #18, 2026-07-10: rimosso il ramo vision morto — image_base64 viene
    # gia' rifiutato con errore esplicito a inizio funzione, questo blocco era
    # irraggiungibile dal 2026-07-09, quando la vision e' stata tolta da DEVIN.)

    # Persistenza server-side dello storico: caricata piu' sopra (prima del blocco
    # web-search, a cui serve per costruire la query contestuale). Il server resta
    # la fonte di verita' — req.history del client viene ignorato.

    # Storico conversazione + system prompt configurabile (Regola: elastico, non hardcoded).
    # system_prompt vuoto di default -> comportamento chat generica invariato.
    chat_cfg = ai.config.get("chat", {})
    system_prompt = (chat_cfg.get("system_prompt") or "").strip()
    max_history = chat_cfg.get("max_history_messages", 20)

    # ---- Modalita' Progetti: contesto costruito da _build_project_context ----
    # (estratto in una funzione per il debug endpoint /api/project/debug_context;
    # log riassuntivo a ogni messaggio, cosi' i "non ho accesso" del modello si
    # distinguono subito da un contesto davvero mai iniettato).
    system_parts = []
    if system_prompt:
        system_parts.append(system_prompt)
    project_parts, ctx_debug = _build_project_context(
        message, persistence_key, req.project_path, current_chat_id=req.chat_id or "")
    system_parts.extend(project_parts)
    print(f"[ProjectSpace] contesto: {ctx_debug}")

    # Nota di capacita' SEMPRE presente (2026-07-10): senza, il modello si
    # inventava finti `curl`/`bash` in chat spacciandoli per eseguiti. Deve
    # sapere cosa puo' e cosa NON puo' fare in questo contesto.
    if req.use_web_search:
        system_parts.append(
            "CAPACITA': in questa risposta hai a disposizione risultati/contenuti web "
            "reali forniti nel messaggio. NON puoi eseguire comandi (bash/curl): non "
            "fingere output di comandi.")
    else:
        system_parts.append(
            "CAPACITA': in questa conversazione NON hai accesso a internet (interruttore "
            "🌐 spento) e NON puoi eseguire comandi (bash/curl): non fingere di farlo. "
            "Se servono dati dal web, chiedi all'utente di attivare l'interruttore 🌐.")

    # Lingua (fix 2026-07-10: rispondeva in inglese anche con istruzioni in italiano).
    # Nudge leggero e sempre presente, non hardcoda l'italiano: mirror della lingua utente.
    system_parts.append(
        "LINGUA: rispondi SEMPRE nella stessa lingua del messaggio dell'utente "
        "(se ti scrive in italiano, rispondi in italiano).")

    # Continuita' preventiva: prima che i turni vecchi escano dalla finestra,
    # crea un handoff strutturato e lo abbina sempre a una coda verbatim recente.
    # E' stato conversazionale per-chat, mai memoria recall-safe.
    continuity_cfg = chat_cfg.get("continuity", {})
    continuity_enabled = continuity_cfg.get("enabled", True)
    recent_messages = max(2, int(continuity_cfg.get("recent_messages", 8)))
    checkpoint = chat_persistence.get_continuity() if continuity_enabled else None
    local_cfgs = ai.config.get("models", {}).get("local_models", {})
    configured_contexts = [
        int(cfg.get("ctx_size")) for cfg in local_cfgs.values()
        if isinstance(cfg, dict) and str(cfg.get("ctx_size", "")).isdigit()
    ]
    context_size = min(configured_contexts) if configured_contexts else 8192
    fixed_context = "\n\n".join(system_parts)
    if continuity_enabled and should_checkpoint(
        persisted_history,
        context_size=context_size,
        fixed_context=fixed_context,
        trigger_ratio=float(continuity_cfg.get("trigger_ratio", 0.72)),
        max_history_messages=max_history,
        recent_messages=recent_messages,
        min_messages=int(continuity_cfg.get("min_messages", 12)),
    ) and checkpoint_needs_refresh(
        persisted_history,
        checkpoint,
        recent_messages=recent_messages,
        refresh_messages=int(continuity_cfg.get("refresh_messages", 6)),
    ):
        def _summarize_continuity(prompt: str):
            return ai.complete(
                prompt,
                max_tokens=int(continuity_cfg.get("summary_max_tokens", 1200)),
                temperature=0.0,
                mode="reasoning",
            )

        checkpoint = await asyncio.to_thread(
            build_checkpoint,
            persisted_history,
            existing=checkpoint,
            summarizer=_summarize_continuity,
            recent_messages=recent_messages,
            source_max_chars=int(continuity_cfg.get("source_max_chars", 24000)),
            summary_max_chars=int(continuity_cfg.get("summary_max_chars", 6000)),
        )
        if checkpoint:
            chat_persistence.set_continuity(checkpoint)

    continuity_context = context_from_checkpoint(checkpoint)
    if continuity_context:
        system_parts.append(continuity_context)

    messages = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    if persisted_history:
        # Tronca ai piu' recenti max_history messaggi: protegge da OOM/contesto
        # locale limitato su run prolungate (vincolo hardware locale).
        history_limit = recent_messages if continuity_context else max_history
        messages.extend(persisted_history[-history_limit:])
    messages.append({"role": "user", "content": content})

    model_name = (
        ai.local_reasoning_model
        if selected_mode == "reasoning"
        else ai.local_coder_model
    )

    config_key = "reasoning" if selected_mode == "reasoning" else "coder"
    model_cfg = ai.config.get("models", {}).get("local_models", {}).get(config_key, {})
    model_detail = {
        "name": model_name,
        "file": model_cfg.get("file", ""),
        "description": model_cfg.get("description", ""),
        "ctx_size": model_cfg.get("ctx_size", ""),
        "vision": model_cfg.get("vision", {}).get("enabled", False),
        "web_search_used": req.use_web_search,
        "continuity_checkpoint": bool(continuity_context),
        "continuity_summarized_messages": (
            int(checkpoint.get("summarized_messages") or 0) if checkpoint else 0
        ),
    }

    async def generate_sse(model_name: str, model_detail: dict):
        token_count = 0
        start_time = time.time()
        full_response = ""

        if auto_web_enabled:
            yield f"event: info\ndata: {json.dumps({'message': '🌐 Ricerca web attivata automaticamente per questa domanda'})}\n\n"

        if web_search_error:
            yield f"event: warning\ndata: {json.dumps({'message': f'Web search non disponibile: {web_search_error}'})}\n\n"

        yield f"event: meta\ndata: {json.dumps({'mode': selected_mode, 'model': model_name, 'detail': model_detail})}\n\n"

        try:
            for chunk in ai.stream(messages, mode=selected_mode):
                token_count += 1
                full_response += chunk
                yield f"data: {json.dumps({'token': chunk})}\n\n"
                await asyncio.sleep(0)

            elapsed = time.time() - start_time
            # #16 audit: token_count conta i CHUNK SSE. Con llama-server è ~1 token
            # per chunk, quindi tps ≈ token/s (approssimazione onesta, non esatta:
            # un chunk può contenere più token in altri backend).
            tps = round(token_count / elapsed, 1) if elapsed > 0 else 0
            yield f"event: done\ndata: {json.dumps({'tokens': token_count, 'tps': tps, 'elapsed': round(elapsed, 1)})}\n\n"

            if chat_persistence and full_response.strip():
                chat_persistence.append("assistant", full_response)
                if req.project_path:
                    chat_eval = detect_chat_only_output(message, full_response)
                    if chat_eval:
                        try:
                            outcome = record_eval_result(
                                _get_automem(),
                                project_path=req.project_path,
                                task=message,
                                eval_name="chat_only_output_detector",
                                status=chat_eval["status"],
                                failure_type=chat_eval["failure_type"],
                                reason=chat_eval["reason"],
                                evidence="chat_transcript_static_analysis",
                                retry_rule=chat_eval["retry_rule"],
                                extra_tags=["topic:chat", "topic:scaffold"],
                            )
                            yield f"event: warning\ndata: {json.dumps({'message': f'Ho salvato una memoria di fallimento operativo: {outcome}. Questa risposta contiene snippet ma non file reali.'})}\n\n"
                        except Exception as exc:
                            print(f"[MemoryEval] chat-only recorder failed: {exc}")

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_sse(model_name, model_detail),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# #14 audit: limiti di dimensione sugli upload in chat (prima illimitati:
# un file enorme veniva letto tutto in RAM prima di qualunque controllo).
MAX_IMAGE_BYTES = 15 * 1024 * 1024      # 15 MB
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024   # 25 MB
MAX_CHAT_UPLOAD_FILES = 12
MAX_CHAT_ATTACHMENT_CHARS = 16000
MAX_BINARY_PREVIEW_BYTES = 512
CHAT_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".json", ".jsonl", ".csv", ".log",
    ".yaml", ".yml", ".sh", ".bat", ".ps1", ".js", ".ts",
    ".tsx", ".jsx", ".html", ".css", ".scss", ".toml", ".ini",
    ".cfg", ".xml", ".sql", ".rs", ".go", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".php", ".rb", ".lua", ".dockerfile"
}



def _looks_textual(raw: bytes) -> bool:
    if not raw:
        return True
    sample = raw[:4096]
    if b"\x00" in sample:
        return False
    decoded = sample.decode("utf-8", errors="replace")
    if not decoded:
        return False
    replacement_ratio = decoded.count("�") / max(1, len(decoded))
    control_count = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\r\n\t")
    return replacement_ratio < 0.05 and control_count / max(1, len(decoded)) < 0.05


def _truncate_attachment_text(text: str, limit: int = MAX_CHAT_ATTACHMENT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[troncato, {len(text)} caratteri totali nell_allegato]"


def _format_chat_upload_for_context(filename: str, raw: bytes) -> str:
    safe_name = Path(filename or "unnamed").name or "unnamed"
    ext = Path(safe_name).suffix.lower()
    digest = hashlib.sha256(raw).hexdigest()
    header = f"[Allegato: {safe_name} | size={len(raw)} bytes | sha256={digest[:16]}...]"

    if ext in {".pdf", ".docx", ".xlsx", ".pptx"}:
        extracted = extract_document_text(safe_name, raw)
        return f"{header}\n```text\n{_truncate_attachment_text(extracted)}\n```"

    if ext in CHAT_TEXT_EXTENSIONS or _looks_textual(raw):
        text = raw.decode("utf-8", errors="replace")
        return f"{header}\n```text\n{_truncate_attachment_text(text)}\n```"

    preview = raw[:MAX_BINARY_PREVIEW_BYTES].hex(" ")
    binary_note = "File binario o non testuale: contenuto raw non iniettato nel prompt. Uso metadati, hash e preview esadecimale per analisi/debug."
    return f"{header}\n{binary_note}\n```hex\n{preview}\n```"


async def _read_upload_limited(upload: UploadFile, max_bytes: int):
    """Legge un UploadFile a chunk abortendo appena supera max_bytes (non carica
    in RAM file oltre il limite). Ritorna (bytes|None, error_msg|None)."""
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return None, f"file troppo grande (max {max_bytes // (1024 * 1024)} MB)"
        chunks.append(chunk)
    return b"".join(chunks), None


@router.post("/api/chat/vision")
async def api_chat_vision(message: str = Form(""), image: UploadFile = File(None),
                           mode: str = Form("auto"), project_path: str = Form(""),
                           use_web_search: bool = Form(False)):
    image_b64 = None
    if image:
        contents, err = await _read_upload_limited(image, MAX_IMAGE_BYTES)
        if err:
            return {"error": err}
        image_b64 = base64.b64encode(contents).decode("utf-8")

    # FIX: project_path non veniva mai inoltrato qui -> ogni messaggio con
    # immagine allegata saltava la persistenza server-side, anche con un
    # progetto impostato (finiva sempre in ChatPersistence(None) = disattivata).
    req = ChatRequest(message=message, mode=mode, image_base64=image_b64,
                       project_path=project_path or None, use_web_search=use_web_search)
    return await api_chat(req)


@router.post("/api/chat/document")
async def api_chat_document(message: str = Form(""), document: UploadFile = File(None),
                             files: Optional[List[UploadFile]] = File(None),
                             mode: str = Form("auto"), project_path: str = Form(""),
                             use_web_search: bool = Form(False), chat_id: str = Form("")):
    """Allegati chat multi-file. Estrae testo dai formati noti e, per file
    strani o binari, inietta una scheda tecnica sicura invece di rifiutarli."""
    uploads = []
    if document and document.filename:
        uploads.append(document)
    if files:
        uploads.extend([item for item in files if item and item.filename])

    if len(uploads) > MAX_CHAT_UPLOAD_FILES:
        return {"error": f"troppi allegati ({len(uploads)}), max {MAX_CHAT_UPLOAD_FILES}"}

    attachment_blocks = []
    for upload in uploads:
        raw, err = await _read_upload_limited(upload, MAX_DOCUMENT_BYTES)
        if err:
            return {"error": f"{upload.filename}: {err}"}
        block = await asyncio.to_thread(_format_chat_upload_for_context, upload.filename, raw)
        attachment_blocks.append(block)

    content = message
    if attachment_blocks:
        content = ("\n\n".join(attachment_blocks) + "\n\n" + (message or "Analizza gli allegati.")).strip()

    req = ChatRequest(message=content, mode=mode, project_path=project_path or None,
                       use_web_search=use_web_search, chat_id=chat_id or None)
    return await api_chat(req)


@router.post("/api/chat/search")
async def api_chat_search(req: ChatRequest):
    """Endpoint esplicito: forza sempre la ricerca web indipendentemente da euristiche."""
    req.use_web_search = True
    return await api_chat(req)


@router.get("/api/chat/history")
async def api_chat_history_get(project_path: str = "", chat_id: str = ""):
    """Storico persistito per un progetto — il frontend lo chiama al caricamento
    pagina o al cambio di project_path, cosi' la conversazione sopravvive a
    refresh/chiusura del browser. updated_at serve al bot Telegram per il check
    'nessuna risposta da N ore' senza dover tracciare timestamp per-messaggio.
    project_path vuoto -> chat generale, sotto GENERAL_CHAT_PROJECT_KEY (prima
    ritornava sempre vuoto: la chat senza progetto non era mai recuperabile).
    chat_id (modalita' Progetti): conversazione specifica in .devin/chats/."""
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        GENERAL_CHAT_PROJECT_KEY,
        _validated_project_path,
    )
    persistence_key = (_validated_project_path(project_path, allow_general=False)
                       if project_path else GENERAL_CHAT_PROJECT_KEY)
    cp = ChatPersistence(persistence_key, chat_id=chat_id or None)
    checkpoint = cp.get_continuity()
    return {
        "history": cp.load(),
        "updated_at": cp.last_updated(),
        "continuity_ready": bool(context_from_checkpoint(checkpoint)),
        "continuity_summarized_messages": (
            int(checkpoint.get("summarized_messages") or 0) if checkpoint else 0
        ),
    }


@router.post("/api/chat/history/clear")
async def api_chat_history_clear(request: Request):
    """Reset della conversazione persistita per un progetto (bottone 'Nuova conversazione')."""
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        GENERAL_CHAT_PROJECT_KEY,
        _validated_project_path,
    )
    data = await request.json()
    project_path = data.get("project_path", "")
    chat_id = data.get("chat_id") or None
    persistence_key = (_validated_project_path(project_path, allow_general=False)
                       if project_path else GENERAL_CHAT_PROJECT_KEY)
    ChatPersistence(persistence_key, chat_id=chat_id).clear()
    return {"status": "cleared"}


@router.post("/api/chat/history/delete_message")
async def api_chat_history_delete_message(request: Request):
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        GENERAL_CHAT_PROJECT_KEY,
        _validated_project_path,
    )
    data = await request.json()
    project_path = data.get("project_path", "")
    chat_id = data.get("chat_id") or None
    index = data.get("index")
    if not isinstance(index, int):
        return {"error": "index non valido"}
    persistence_key = (_validated_project_path(project_path, allow_general=False)
                       if project_path else GENERAL_CHAT_PROJECT_KEY)
    cp = ChatPersistence(persistence_key, chat_id=chat_id)
    history = cp.load()
    if index < 0 or index >= len(history):
        return {"error": "messaggio non trovato"}
    removed = history.pop(index)
    cp.save(history)
    return {"status": "deleted", "removed_role": removed.get("role"), "remaining": len(history)}


# ============================================================
# /api/chat/generate_patch (fetta split 15, 2026-07-18)
# ============================================================


@router.post("/api/chat/generate_patch")
async def api_chat_generate_patch(request: Request):
    """
    'Genera patch da questa conversazione e riprova': prende la conversazione
    chat persistita per il progetto e la usa come piano (salta il Planner),
    poi Coder->Patcher->Runner->Critic come nel Mantenimento normale. Stesso
    streaming SSE via /stream/{run_id} gia' usato da /api/run e /api/chat/scaffold.
    """
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        LOG_DIR,
        ProjectSpace,
        _make_run_callback,
        _validated_project_path,
    )
    data = await request.json()
    project_path = data.get("project_path", "")
    if not project_path:
        return {"error": "missing project_path — imposta un progetto prima di generare codice dalla chat"}
    project_path = _validated_project_path(project_path, allow_general=False)

    # La conversazione e la knowledge restano associate al progetto DEVIN,
    # mentre l'esecuzione deve rispettare l'eventuale cartella di lavoro
    # collegata, esattamente come /api/run e /api/chat/scaffold.
    execution_path = project_path
    work_dir = ProjectSpace(project_path).get_work_dir()
    if work_dir:
        execution_path = _validated_project_path(work_dir, allow_general=False)
        print(f"[WORKDIR] generate_patch instradato sulla cartella di lavoro: {execution_path}")

    # chat_id (modalita' Progetti, 2026-07-10): usa la conversazione SELEZIONATA
    # in sidebar, non piu' solo la sessione legacy.
    history = ChatPersistence(project_path, chat_id=data.get("chat_id") or None).load()
    if not history:
        return {"error": "nessuna conversazione salvata per questo progetto/chat"}

    conversation_text = "\n\n".join(f"[{m['role'].upper()}]: {m['content']}" for m in history)

    # Progetto senza codice -> la cosa giusta e' lo Zero-Shot Scaffolding dalla
    # conversazione (creare i file), non il ciclo di patch (che presuppone
    # codice esistente da modificare).
    _proj = Path(execution_path).expanduser()
    _is_empty_project = (not _proj.exists()) or not any(
        f for f in _proj.rglob("*.py")
        if not any(part in (".devin", ".devin_chat", "workspace", "venv", ".git", "__pycache__")
                   for part in f.relative_to(_proj).parts))
    mode = "scaffold" if _is_empty_project else "patch"

    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    log_path = LOG_DIR / f"{run_id}.log"
    log_path.write_text(f"{'Scaffold' if mode == 'scaffold' else 'Patch'} da conversazione: {run_id}\n",
                        encoding="utf-8")

    sse_callback = _make_run_callback(run_id, log_path)

    def _bg():
        from devin.ui.fast_app import (  # lazy: risolti a thread-run time
            CONFIG_PATH,
            Orchestrator,
            active_runs,
            runs_lock,
        )
        try:
            with Orchestrator(
                config_path=CONFIG_PATH,
                project_path=execution_path,
                sse_callback=sse_callback
            ) as orch:
                with runs_lock:
                    active_runs[run_id] = orch
                try:
                    if mode == "scaffold":
                        # Progetto vuoto: crea i file da zero usando la
                        # conversazione come specifica (stesso run_scaffold
                        # del routing "Chat First").
                        result = orch.run_scaffold(
                            task=("Realizza il progetto descritto in questa conversazione. "
                                  "Segui le decisioni prese e le correzioni piu' recenti.\n\n"
                                  + conversation_text),
                            project_path=execution_path,
                            run_id=run_id
                        )
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"status: {'success' if result.get('success') else 'failed'}\n")
                    else:
                        result = orch.run_from_conversation(
                            conversation_text=conversation_text,
                            project_path=execution_path,
                            run_id=run_id
                        )
                        # Niente scrittura qui: run_from_conversation() scrive gia' il
                        # footer 'status: X' internamente in ogni return path.
                finally:
                    with runs_lock:
                        active_runs.pop(run_id, None)
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[FATAL] {e}\nstatus: failed\n")

    threading.Thread(target=_bg, daemon=True).start()
    return {"run_id": run_id, "status": "started", "mode": mode}
