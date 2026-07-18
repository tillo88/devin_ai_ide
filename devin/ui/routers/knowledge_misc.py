"""Router knowledge_misc: YouTube transcript, docs cache, sandbox prepare.

Primo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md): scelto perche' a ZERO stato mutabile condiviso.
Path e comportamento identici al codice pre-estrazione — move puro, nessun
refactor. WORKSPACE_DIR e' importato lazy da fast_app per evitare l'import
circolare (fast_app include questo router).
"""

import asyncio

from fastapi import APIRouter, Request

from devin.engine.project_sandbox import ProjectSandboxPolicy, prepare_project_sandbox

router = APIRouter()


def _docs_cache() -> "DocsCache":
    from devin.core.docs_cache import DocsCache
    from devin.ui.fast_app import WORKSPACE_DIR  # lazy: evita import circolare
    return DocsCache(WORKSPACE_DIR)


@router.post("/api/youtube/transcript")
async def api_youtube_transcript(request: Request):
    """Trascrizione di un video YouTube (sottotitoli, non 'guarda' i frame).
    Opzionale: salva in docs cache come fonte web se save_to_docs=true."""
    from devin.ai.youtube_tools import get_transcript
    data = await request.json()
    result = await asyncio.to_thread(get_transcript, data.get("url", ""))
    if result.get("text") and data.get("save_to_docs"):
        try:
            _docs_cache().add_doc(
                title=f"youtube: {result['video_id']}",
                content=result["text"],
                keys=[k for k in (data.get("keys") or []) if k] or [result["video_id"]],
                source_url=f"https://youtu.be/{result['video_id']}",
                source="web",
            )
            result["saved_to_docs"] = True
        except Exception as exc:
            result["saved_to_docs"] = False
            result["docs_error"] = str(exc)
    return result


@router.get("/api/docs_cache/list")
async def api_docs_cache_list():
    return {"docs": _docs_cache().list_docs()}


@router.post("/api/docs_cache/add")
async def api_docs_cache_add(request: Request):
    """Aggiunge una doc ufficiale alla cache condivisa (anti endpoint/firme
    inventati). keys = termini che la attivano nel task/errore."""
    data = await request.json()
    try:
        entry = _docs_cache().add_doc(
            title=data.get("title", ""),
            content=data.get("content", ""),
            keys=data.get("keys") or [],
            source_url=data.get("source_url", ""),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"doc": entry}


@router.post("/api/docs_cache/remove")
async def api_docs_cache_remove(request: Request):
    data = await request.json()
    return {"removed": _docs_cache().remove_doc(data.get("slug", ""))}


@router.post("/api/sandbox/prepare")
async def api_sandbox_prepare(request: Request):
    data = await request.json()
    project_path = data.get("project_path") or data.get("source_path") or ""
    if not project_path:
        return {"error": "project_path is required"}
    policy = ProjectSandboxPolicy(
        include_venv=bool(data.get("include_venv", False)),
        link_venv=bool(data.get("link_venv", False)),
        include_secrets=bool(data.get("include_secrets", False)),
        include_large_binaries=bool(data.get("include_large_binaries", False)),
        max_file_size_mb=int(data.get("max_file_size_mb", 50) or 50),
        extra_skip_names=list(data.get("extra_skip_names", []) or []),
        extra_skip_patterns=list(data.get("extra_skip_patterns", []) or []),
    )
    try:
        return {"sandbox": prepare_project_sandbox(project_path, policy=policy)}
    except Exception as exc:
        return {"error": str(exc)}
