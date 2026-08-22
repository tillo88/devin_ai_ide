"""P5 reviewed knowledge-exchange API. No raw role-memory endpoint exists."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Request

from devin.memory.knowledge_exchange import KnowledgeExchangeStore


router = APIRouter()


def _store_for(project_path: str = "") -> tuple[KnowledgeExchangeStore, str]:
    from devin.ui.fast_app import WORKSPACE_DIR, _validated_project_path

    if project_path:
        safe = _validated_project_path(project_path, allow_general=False)
        project_id = "project:" + hashlib.sha256(safe.encode("utf-8")).hexdigest()[:20]
        return KnowledgeExchangeStore(Path(safe) / ".devin" / "knowledge_exchange"), project_id
    return KnowledgeExchangeStore(WORKSPACE_DIR / "_knowledge_exchange"), "general"


@router.get("/api/knowledge-exchange/status")
async def api_knowledge_exchange_status(project_path: str = ""):
    store, _ = _store_for(project_path)
    return store.status()


@router.get("/api/knowledge-exchange/promoted")
async def api_knowledge_exchange_promoted(audience: str, project_path: str = ""):
    store, _ = _store_for(project_path)
    try:
        items = store.list_promoted(audience)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"schema": "knowledge_exchange_promoted_v1", "audience": audience, "artifacts": items}


@router.post("/api/knowledge-exchange/proposals")
async def api_knowledge_exchange_propose(request: Request):
    data = await request.json()
    store, project_id = _store_for(data.get("project_path", ""))
    try:
        artifact = store.propose(
            content=data.get("content", ""),
            source_role=data.get("source_role", "devin"),
            source_project_id=project_id,
            audience=data.get("audience", []),
            evidence=data.get("evidence", []),
            expires_at=data.get("expires_at"),
            kind=data.get("kind", "lesson"),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"artifact": artifact, "promotion": "not_performed"}


@router.post("/api/knowledge-exchange/reviews")
async def api_knowledge_exchange_review(request: Request):
    data = await request.json()
    store, _ = _store_for(data.get("project_path", ""))
    try:
        review = store.review(
            artifact_id=data.get("artifact_id", ""),
            decision=data.get("decision", ""),
            reviewer_role=data.get("reviewer_role", "human"),
            reason=data.get("reason", ""),
            evidence=data.get("evidence", []),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"review": review, "status": store.effective_status(review["artifact_id"])}
