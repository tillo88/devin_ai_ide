"""Router projects: CRUD ProjectSpace (workdir, overview, istruzioni,
descrizione, pin, knowledge, chat multiple, memory store, debug, export).

Undicesimo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

RESTANO in fast_app (lazy import a call time): la cache `_project_spaces` +
accessor `_project_space_for` (condivisi con la chat: stessa istanza per
coerenza retrieval), `_validated_project_path` + allowlist (single-owner di
sicurezza), `_read_upload_limited` (condiviso con vision/document),
`_get_automem`, `GENERAL_CHAT_PROJECT_KEY`, `_build_project_context` /
`_detect_linked_projects` (usati dalla chat), `active_runs`/`runs_lock`.
`_validate_public_url` (guardia SSRF) si sposta QUI: la usano solo
from_url/crawl; test_security_regressions aggiornato nella stessa fetta.
fast_app re-esporta `api_project_last_run` (shim: i test lo chiamano).
"""

import asyncio
import ipaddress
import json
import socket
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile

from devin.ai.crawl_ingestion import crawl4ai_status, crawl_url_to_knowledge
from devin.ai.hybrid_memory_client import project_tags
from devin.core.project_space import MAX_KNOWLEDGE_FILE_BYTES, ProjectSpace

router = APIRouter()


@router.post("/api/project/workdir")
async def api_project_workdir(request: Request):
    """Collega/scollega la CARTELLA DI LAVORO di un progetto (epic "Progetti
    come Claude"): il progetto workspace tiene chat/knowledge, i run lavorano
    sulla cartella collegata. work_dir vuoto = scollega. La cartella deve
    essere consentita (workspace o linkata col picker: stessa allowlist di
    crawl/sandbox)."""
    from devin.ui.fast_app import _validated_project_path  # lazy
    data = await request.json()
    project_path = _validated_project_path(data.get("project_path", ""), allow_general=False)
    work_dir = (data.get("work_dir") or "").strip()
    ps = ProjectSpace(project_path)
    if not work_dir:
        ps.set_work_dir("")
        return {"work_dir": "", "status": "unlinked"}
    validated = _validated_project_path(work_dir, allow_general=False)  # 403 se non consentita
    if Path(validated).resolve() == Path(project_path).resolve():
        return {"error": "la cartella di lavoro coincide col progetto stesso: inutile"}
    ps.set_work_dir(validated)
    return {"work_dir": validated, "status": "linked"}


@router.get("/api/project/overview")
async def api_project_overview(project_path: str = "", lite: bool = False):
    """Project overview.

    lite=true is used by the desktop sidebar when switching project: it avoids
    file/knowledge scans that made project selection feel sluggish. Full details
    stay available for dedicated knowledge/file panels.
    """
    from devin.ui.fast_app import _get_automem, _project_space_for  # lazy
    ps = _project_space_for(project_path)
    payload = {
        "project": str(ps.project_path),
        "is_general": not project_path,
        "description": ps.get_description(),
        "instructions": ps.get_instructions(),
        "work_dir": ps.get_work_dir(),
        "chats": ps.list_chats(),
    }
    if lite:
        return payload
    payload.update({
        "knowledge": ps.list_knowledge(),
        "pins": ps.list_pins(),
        "files": ps.list_files(max_items=300),
        "automem": _get_automem().status(),
    })
    return payload


@router.post("/api/project/instructions")
async def api_project_instructions(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ps.set_instructions(data.get("instructions", ""))
    return {"status": "saved", "chars": len(ps.get_instructions())}


@router.post("/api/project/description")
async def api_project_description(request: Request):
    """'About' del progetto (scopo/stack): header + contesto persistente."""
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ps.set_description(data.get("description", ""))
    return {"status": "saved", "chars": len(ps.get_description())}


@router.post("/api/project/pins/add")
async def api_project_pins_add(request: Request):
    """★ Appunta un file del progetto: sarà SEMPRE nel contesto dell'agente."""
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ok = ps.add_pin(data.get("path", ""))
    return {"status": "pinned" if ok else "error",
            "error": None if ok else "file non valido o fuori dal progetto",
            "pins": ps.list_pins()}


@router.post("/api/project/pins/remove")
async def api_project_pins_remove(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ps.remove_pin(data.get("path", ""))
    return {"status": "unpinned", "pins": ps.list_pins()}


def _validate_public_url(url: str) -> None:
    """Reject URLs resolving to loopback, private, link-local or reserved IPs."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("schema URL non consentito")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("hostname URL mancante")
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"hostname non risolvibile: {exc}") from exc
    for info in infos:
        address = info[4][0]
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"destinazione di rete non consentita: {address}")


@router.post("/api/project/knowledge/from_url")
async def api_project_knowledge_from_url(request: Request):
    """Aggiunge alla knowledge il testo estratto da un URL (es. doc di una
    libreria): niente download manuale. Usa fetch_page_text (UA browser +
    estrazione testo stdlib), in thread perché è rete."""
    from devin.ui.fast_app import _project_space_for  # lazy
    from devin.ai.web_search import fetch_page_text
    from urllib.parse import urlparse
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    url = (data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "URL non valido (serve http:// o https://)"}
    try:
        await asyncio.to_thread(_validate_public_url, url)
    except ValueError as exc:
        return {"error": f"URL non consentito: {exc}"}
    try:
        text = await asyncio.to_thread(fetch_page_text, url, 20000, 15)
    except Exception as e:
        return {"error": f"fetch fallito: {e}"}
    if not (text or "").strip():
        return {"error": "nessun testo estratto (pagina JS-only o bloccata?)"}
    parsed = urlparse(url)
    base = (parsed.netloc + parsed.path).strip("/").replace("/", "_") or "pagina"
    fname = base[:60] + ".md"
    header = f"# Fonte: {url}\n\n"
    return ps.add_knowledge(fname, (header + text).encode("utf-8"))


@router.get("/api/project/last_run")
async def api_project_last_run(project_path: str = ""):
    """Stato dell'ultimo run del progetto (badge nel pannello). Legge lo stato
    per-progetto (.devin_state via StatePersistence.load_latest)."""
    from devin.ui.fast_app import active_runs, runs_lock  # lazy: run-core
    from devin.core.state_persistence import StatePersistence
    if not project_path:
        return {"has_run": False}
    pp = str(Path(project_path).expanduser().resolve())
    try:
        st = await asyncio.to_thread(lambda: StatePersistence(pp).load_latest())
    except Exception:
        st = None
    if not st:
        return {"has_run": False}
    run_id = st.get("_run_id")
    # Riconciliazione con active_runs (2026-07-18): uno stato senza final_status
    # puo' essere un run VIVO (salva tra uno step e l'altro) — prima il badge
    # lo mostrava "interrotto" e persino riprendibile mentre stava girando.
    with runs_lock:
        is_active = run_id in active_runs
    final_status = st.get("final_status")
    # resumable rispetta gli stessi limiti del resume endpoint: retry non esauriti
    resumable = (
        not final_status
        and not is_active
        and st.get("attempt", 0) < st.get("max_retries", 3)
    )
    return {
        "has_run": True,
        "run_id": run_id,
        "status": "running" if is_active else (final_status or "interrotto"),
        "final": bool(final_status),
        "running": is_active,
        "resumable": resumable,
        "saved_at": st.get("_saved_at"),
        "task": (st.get("task") or "")[:200],
    }


@router.post("/api/project/knowledge/crawl")
async def api_project_knowledge_crawl(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    url = (data.get("url") or "").strip()
    mode = data.get("mode", "auto")
    max_chars = int(data.get("max_chars", 50000) or 50000)
    if not url.startswith(("http://", "https://")):
        return {"error": "URL non valido (serve http:// o https://)"}
    try:
        await asyncio.to_thread(_validate_public_url, url)
    except ValueError as exc:
        return {"error": f"URL non consentito: {exc}"}
    try:
        record = await crawl_url_to_knowledge(url, mode=mode, max_chars=max_chars)
    except Exception as exc:
        return {"error": f"crawl fallito: {exc}", "adapter": crawl4ai_status().__dict__}
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base = (parsed.netloc + parsed.path).strip("/").replace("/", "_") or "crawl"
    fname = base[:60] + f".{record.source}.md"
    result = ps.add_knowledge(fname, record.as_knowledge_markdown().encode("utf-8"))
    result["adapter"] = {"source": record.source, "crawl4ai": crawl4ai_status().__dict__}
    result["url"] = url
    return result


@router.get("/api/project/knowledge/crawl/status")
async def api_project_knowledge_crawl_status():
    return crawl4ai_status().__dict__


@router.post("/api/project/knowledge/upload")
async def api_project_knowledge_upload(project_path: str = Form(""),
                                        file: UploadFile = File(...)):
    from devin.ui.fast_app import _project_space_for, _read_upload_limited  # lazy
    ps = _project_space_for(project_path)
    try:
        raw = await _read_upload_limited(file, MAX_KNOWLEDGE_FILE_BYTES)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result = ps.add_knowledge(file.filename, raw)
    return result


@router.post("/api/project/knowledge/delete")
async def api_project_knowledge_delete(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ok = ps.delete_knowledge(data.get("filename", ""))
    return {"status": "deleted" if ok else "not_found"}


@router.post("/api/project/chats/new")
async def api_project_chats_new(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    from devin.core.chat_continuity import CHECKPOINT_SCHEMA
    from devin.core.chat_persistence import ChatPersistence
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    continue_from = (data.get("continue_from_chat_id") or "").strip()
    continuity = None
    if continue_from:
        continuity = ChatPersistence(
            str(ps.project_path), chat_id=continue_from
        ).get_continuity()
        if not continuity or continuity.get("schema") != CHECKPOINT_SCHEMA:
            return {"error": "continuity checkpoint non disponibile per la chat sorgente"}
    chat_id = ps.new_chat(
        data.get("title", ""), continuity=continuity,
        continued_from=continue_from,
    )
    return {"chat_id": chat_id, "continued_from": continue_from or None,
            "continuity_ready": bool(continuity)}


@router.post("/api/project/chats/rename")
async def api_project_chats_rename(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ok = ps.rename_chat(data.get("chat_id", ""), data.get("title", ""))
    return {"status": "renamed" if ok else "not_found"}


@router.post("/api/project/chats/delete")
async def api_project_chats_delete(request: Request):
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    ok = ps.delete_chat(data.get("chat_id", ""))
    return {"status": "deleted" if ok else "not_found"}


@router.post("/api/project/memory/store")
async def api_project_memory_store(request: Request):
    """Store manuale su AutoMem (bottone 'salva in memoria'). Fail-soft: se il
    rig e' spento torna stored=false con motivo leggibile, mai un 500."""
    from devin.ui.fast_app import GENERAL_CHAT_PROJECT_KEY, _get_automem  # lazy
    data = await request.json()
    project_path = data.get("project_path", "") or GENERAL_CHAT_PROJECT_KEY
    content = data.get("content", "")
    automem = _get_automem()
    if not automem.enabled:
        return {"stored": False, "queued": False, "reason": "AutoMem disabilitato in settings.json"}
    outcome = automem.store(content, tags=project_tags(project_path) + [
        "source:devin", "domain:software-engineering",
        "status:human_confirmed", "polarity:positive", "visibility:shared",
    ])
    return {
        "stored": outcome == "stored",
        "queued": outcome == "queued",
        "reason": {
            "stored": None,
            "queued": "rig spento: memoria in coda locale, si sincronizza da sola appena il rig risponde",
            "failed": "salvataggio fallito (nemmeno l'outbox locale e' scrivibile?)",
        }[outcome],
    }


@router.get("/api/project/debug_context")
async def api_project_debug_context(q: str, project_path: str = ""):
    """DEBUG: mostra esattamente cosa verrebbe iniettato nel contesto per il
    messaggio `q` — stessa funzione usata dalla chat vera. Aprire nel browser:
    /api/project/debug_context?q=qual e il codice segreto nel progetto test_project"""
    from devin.ui.fast_app import (  # lazy: condivisi con la chat
        GENERAL_CHAT_PROJECT_KEY,
        _build_project_context,
        _detect_linked_projects,
    )
    persistence_key = project_path or GENERAL_CHAT_PROJECT_KEY
    parts, dbg = _build_project_context(q, persistence_key, project_path or None)
    return {
        "query": q,
        "persistence_key": persistence_key,
        "detected_linked_projects": _detect_linked_projects(q, persistence_key),
        "debug": json.loads(dbg),
        "injected_parts": parts,
    }


@router.post("/api/project/export_dataset")
async def api_project_export_dataset(request: Request):
    """Esporta tutte le chat del progetto in JSONL (formato OpenAI chat) in
    .devin/export/ — pronto per l'harness/LoRA futuro. Ritorna il path."""
    from devin.ui.fast_app import _project_space_for  # lazy
    data = await request.json()
    ps = _project_space_for(data.get("project_path", ""))
    out = ps.export_dataset()
    if out is None:
        return {"status": "empty", "path": None}
    return {"status": "exported", "path": str(out)}
