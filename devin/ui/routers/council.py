"""Router Federated Evidence Council: espone la review multi-asse via HTTP.

Il Council gira DENTRO il backend (come Goal Mode e Context Steward), non come
demone separato. Questo router e' un guscio sottile sopra
`devin.core.council_store`: costruisce i reviewer dalla configurazione, lancia
il giro e restituisce l'esito con la provenance.

Scelte deliberate:

- **Nessun modello hardcodato.** I reviewer-modello si attivano solo se la
  configurazione li dichiara (`council.model_reviewers`): il modello operativo
  e' un placeholder finche' la Model Evaluation Suite non sceglie il candidato.
- **Default deterministico e offline.** Senza configurazione parte il solo
  `LocalDeterministicReviewer`: nessuna chiamata a modelli, nessuna VRAM, quindi
  l'endpoint e' sicuro anche mentre il rig sta calibrando o benchmarkando.
- **Non promuove.** L'esito e' una raccomandazione con evidenza; lo status
  scritto nello store resta gated (`pending_review` salvo fallimento conclusivo).
- **Model-consuming solo se configurato**: quando ci saranno reviewer-modello,
  questo endpoint diventa una superficie da proteggere con il calibration
  interlock (HTTP 423), come `/api/goal/run` e `/api/training/run`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from devin.core.council import AXES, LocalDeterministicReviewer, ReviewerAdapter
from devin.core.council_budget import Budget

router = APIRouter()


class CouncilReviewRequest(BaseModel):
    project_path: str = ""
    attempt_id: str = ""            # vuoto = processa la coda
    limit: int = 20                 # solo per la coda
    critical: bool = False
    axes: Optional[List[str]] = None
    force: bool = False             # rigiudica anche se il Council l'ha gia' visto
    persist: bool = True
    max_reviews: Optional[int] = None
    max_total_seconds: Optional[float] = None


def _store_for(project_path: str = ""):
    from devin.training.store import TrainingStore
    from devin.ui.fast_app import WORKSPACE_DIR, _validated_project_path  # lazy
    if project_path:
        safe = _validated_project_path(project_path, allow_general=False)
        return TrainingStore(Path(safe) / ".devin" / "training")
    return TrainingStore(WORKSPACE_DIR / "_training")


def _load_config() -> Dict[str, Any]:
    try:
        import json
        from devin.ui.fast_app import CONFIG_PATH  # lazy
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except Exception:
        return {}


def build_reviewers(config: Optional[Dict[str, Any]] = None) -> List[ReviewerAdapter]:
    """Costruisce i reviewer dalla config. Deterministico sempre, modelli opzionali.

    Config attesa (tutta opzionale):
    ```json
    "council": {
      "strict_coverage": true,
      "model_reviewers": [
        {"reviewer_id": "semantico-1", "family": "<famiglia-del-MODELLO>",
         "axes": ["correttezza_concettuale", "robustezza", "qualita"],
         "mode": "reasoning", "include_mechanical_status": false}
      ]
    }
    ```
    Nota: `family` deve seguire il MODELLO, non il ruolo. Se si cambia modello
    lasciando la vecchia family, la regola no-duplicati-di-famiglia del router
    smette di proteggere dagli errori correlati senza dare alcun errore.
    """
    cfg = (config or {}).get("council", {}) or {}
    reviewers: List[ReviewerAdapter] = [
        LocalDeterministicReviewer(strict_coverage=bool(cfg.get("strict_coverage", True)))
    ]
    for entry in cfg.get("model_reviewers", []) or []:
        try:
            from devin.core.council_model_reviewer import (
                ModelReviewSpec,
                build_model_reviewer,
            )
            spec = ModelReviewSpec(
                reviewer_id=str(entry.get("reviewer_id") or "").strip(),
                family=str(entry.get("family") or "").strip(),
                supported_axes=tuple(entry.get("axes") or ()),
            )
            reviewers.append(build_model_reviewer(
                spec,
                mode=str(entry.get("mode") or "reasoning"),
            ))
        except Exception:
            # Un reviewer mal configurato non deve impedire il giro: il Council
            # degrada sulla copertura, non si blocca.
            continue
    return reviewers


def _budget(payload: CouncilReviewRequest) -> Optional[Budget]:
    if payload.max_reviews is None and payload.max_total_seconds is None:
        return None
    return Budget(max_reviews=payload.max_reviews, max_total_seconds=payload.max_total_seconds)


@router.post("/api/council/review")
async def api_council_review(payload: CouncilReviewRequest):
    """Fa girare il Council su un attempt o sulla coda di review."""
    from devin.core.council_store import review_attempt, review_queue

    axes = tuple(payload.axes) if payload.axes else AXES
    unknown = [a for a in axes if a not in AXES]
    if unknown:
        return {"error": f"assi sconosciuti: {unknown}", "valid_axes": list(AXES)}

    try:
        store = _store_for(payload.project_path)
    except Exception as exc:
        return {"error": f"project_path non valido: {exc}"}

    reviewers = build_reviewers(_load_config())
    budget = _budget(payload)

    if payload.attempt_id:
        attempts = {a.get("attempt_id"): a for a in store.list_attempts(limit=10000)}
        attempt = attempts.get(payload.attempt_id)
        if not attempt:
            return {"error": f"attempt sconosciuto: {payload.attempt_id}"}
        cases = {c.get("case_id"): c for c in store.list_cases(limit=10000, include_retired=True)}
        try:
            run = review_attempt(
                store, attempt, reviewers,
                case=cases.get(attempt.get("case_id")),
                project_path=payload.project_path,
                critical=payload.critical, axes=axes, budget=budget,
                persist=payload.persist,
            )
        except Exception as exc:
            return {"error": str(exc)[:400]}
        return {"mode": "single", "attempt_id": run.packet_id, "council": run.to_review_payload()}

    report = review_queue(
        store, reviewers, limit=max(1, int(payload.limit)),
        project_path=payload.project_path, critical=payload.critical,
        axes=axes, budget=budget, force=payload.force, persist=payload.persist,
    )
    return {"mode": "queue", "report": report.to_dict()}


@router.get("/api/council/status")
async def api_council_status(project_path: str = ""):
    """Chi e' in coda, chi ha gia' visto il Council, con quali reviewer attivi."""
    from devin.core.council_store import COUNCIL_REVIEWER_ID

    try:
        store = _store_for(project_path)
    except Exception as exc:
        return {"error": f"project_path non valido: {exc}"}

    reviewers = build_reviewers(_load_config())
    covered: set = set()
    for reviewer in reviewers:
        covered.update(reviewer.supported_axes)

    queue = store.review_queue(limit=200)
    council_seen = {
        review.get("attempt_id")
        for review in store.list_reviews(limit=10000)
        if (review.get("reviewer") or "") == COUNCIL_REVIEWER_ID
    }
    pending = [item for item in queue if item.get("attempt_id") not in council_seen]

    return {
        "queue_total": len(queue),
        "pending_for_council": len(pending),
        "already_reviewed": len(queue) - len(pending),
        "reviewers": [
            {"reviewer_id": r.reviewer_id, "family": r.family, "axes": list(r.supported_axes)}
            for r in reviewers
        ],
        "axes_covered": sorted(covered),
        "axes_uncovered": [a for a in AXES if a not in covered],
        "promotion_policy": "il Council non promuove: la promozione resta gated dal rerun",
    }
