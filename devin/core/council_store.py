"""Ponte fra il Federated Evidence Council e il training store.

Chiude l'unico debito reale del Council: prima esisteva ma non lo chiamava
nessuno. Qui il Council legge la coda di review dello store, giudica e riscrive
le sue conclusioni come review **append-only**, con provenance completa.

Tre scelte che vale la pena conoscere:

1. **Evidenza registrata, non ri-derivata.** Il packet porta i `validators` e il
   `quality_gate` salvati *al momento del run*. Rieseguire i controlli oggi, su
   un workspace che nel frattempo puo' essere cambiato, descriverebbe un altro
   attempt. E' la stessa regola degli archivi di calibrazione: l'evidenza e'
   immutabile.

2. **Cieco.** Il packet non porta le review precedenti (anti-anchoring), anche
   se lo store le ha.

3. **Niente ri-review infinita.** Il Council scrive quasi sempre
   `pending_review`, che per costruzione NON chiude la coda dello store (serve
   un verdetto umano/Teacher). Senza una guardia, ogni batch rigiudicherebbe gli
   stessi attempt in eterno: qui si saltano quelli gia' visti dal Council, salvo
   `force=True`.

**Non promuove nulla.** Lo store marca `promotion="eligible"` solo per gli status
safe-success, che il Council non emette mai: la promozione resta gated dal rerun.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from devin.core.council import ReviewerAdapter, ReviewPacket
from devin.core.council_aggregator import AXES
from devin.core.council_arbiter import Arbiter
from devin.core.council_budget import Budget
from devin.core.council_run import CouncilRun, run_council

COUNCIL_REVIEWER_ID = "federated-council"


def packet_from_attempt(
    attempt: Dict[str, Any],
    case: Optional[Dict[str, Any]] = None,
    *,
    project_path: str = "",
) -> ReviewPacket:
    """Costruisce un packet CIECO da un attempt dello store.

    Estrae l'evidenza registrata (`tests.validators`, `tests.quality_gate`) e
    NON include alcuna review precedente.
    """
    attempt = attempt or {}
    case = case or {}
    tests = attempt.get("tests") or {}
    attempt_id = str(attempt.get("attempt_id") or "").strip()
    if not attempt_id:
        raise ValueError("attempt senza attempt_id: impossibile identificare la review")

    return ReviewPacket(
        packet_id=attempt_id,
        case={
            "case_id": case.get("case_id") or attempt.get("case_id"),
            "title": case.get("title"),
            "task": case.get("task") or attempt.get("prompt"),
            "kind": case.get("kind"),
            "tags": case.get("tags") or [],
            "expected_signals": case.get("expected_signals") or [],
            "metadata": case.get("metadata") or {},
        },
        attempt={
            "attempt_id": attempt_id,
            "run_id": attempt.get("run_id"),
            "status": attempt.get("status"),
            "created_at": attempt.get("created_at"),
            "prompt": attempt.get("prompt"),
            "response": attempt.get("response"),
            "error_reason": attempt.get("error_reason"),
            "artifacts": attempt.get("artifacts") or [],
        },
        result={"files_written": list(attempt.get("artifacts") or [])},
        project_path=project_path,
        validation=dict(tests.get("validators") or {}),
        quality_gate=dict(tests.get("quality_gate") or {}),
    )


@dataclass
class CouncilBatchReport:
    """Esito di un giro di Council sulla coda."""

    reviewed: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        by_outcome: Dict[str, int] = {}
        for item in self.reviewed:
            key = item.get("outcome", "?")
            by_outcome[key] = by_outcome.get(key, 0) + 1
        return by_outcome

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewed": len(self.reviewed),
            "skipped": len(self.skipped),
            "errors": len(self.errors),
            "by_outcome": self.counts,
            "details": self.reviewed,
            "skipped_details": self.skipped,
            "error_details": self.errors,
        }


def already_reviewed_by_council(store, attempt_id: str) -> bool:
    """True se il Council ha gia' lasciato una review su questo attempt."""
    for review in store.list_reviews(attempt_id=attempt_id, limit=1000):
        if (review.get("reviewer") or "") == COUNCIL_REVIEWER_ID:
            return True
    return False


def review_attempt(
    store,
    attempt: Dict[str, Any],
    reviewers: Sequence[ReviewerAdapter],
    *,
    case: Optional[Dict[str, Any]] = None,
    project_path: str = "",
    critical: bool = False,
    axes: Sequence[str] = AXES,
    budget: Optional[Budget] = None,
    arbiter: Optional[Arbiter] = None,
    persist: bool = True,
) -> CouncilRun:
    """Giudica UN attempt e (opzionale) scrive la review nello store."""
    packet = packet_from_attempt(attempt, case, project_path=project_path)
    run = run_council(
        packet, reviewers, critical=critical, axes=axes, budget=budget, arbiter=arbiter
    )
    if persist:
        payload = run.to_review_payload()
        store.add_review(
            attempt_id=run.packet_id,
            status=payload["status"],
            rationale=payload["rationale"],
            reviewer=COUNCIL_REVIEWER_ID,
            confidence=_outcome_confidence(run),
            tags=["council", run.outcome.outcome],
            evidence=payload["council"],
            next_action=_next_action(run),
        )
    return run


def review_queue(
    store,
    reviewers: Sequence[ReviewerAdapter],
    *,
    limit: int = 20,
    project_path: str = "",
    critical: bool = False,
    axes: Sequence[str] = AXES,
    budget: Optional[Budget] = None,
    arbiter: Optional[Arbiter] = None,
    force: bool = False,
    persist: bool = True,
) -> CouncilBatchReport:
    """Fa girare il Council sulla coda di review dello store.

    Salta gli attempt gia' giudicati dal Council (evita la ri-review infinita,
    dato che `pending_review` non chiude la coda). `force=True` li rigiudica.
    """
    report = CouncilBatchReport()
    cases = {c.get("case_id"): c for c in store.list_cases(limit=10000, include_retired=True)}
    attempts = {a.get("attempt_id"): a for a in store.list_attempts(limit=10000)}

    for entry in store.review_queue(limit=limit):
        attempt_id = entry.get("attempt_id")
        attempt = attempts.get(attempt_id)
        if not attempt:
            report.skipped.append({"attempt_id": attempt_id, "reason": "attempt non trovato"})
            continue
        if not force and already_reviewed_by_council(store, attempt_id):
            report.skipped.append({"attempt_id": attempt_id, "reason": "gia' giudicato dal Council"})
            continue
        try:
            run = review_attempt(
                store, attempt, reviewers,
                case=cases.get(attempt.get("case_id")),
                project_path=project_path, critical=critical, axes=axes,
                budget=budget, arbiter=arbiter, persist=persist,
            )
        except Exception as exc:  # un attempt malformato non ferma il batch
            report.errors.append({"attempt_id": attempt_id, "error": str(exc)[:300]})
            continue
        report.reviewed.append({
            "attempt_id": attempt_id,
            "outcome": run.outcome.outcome,
            "store_status": run.outcome.store_status,
            "degraded": run.dispatch.degraded,
            "arbitrated": run.arbitrated,
        })
    return report


def _outcome_confidence(run: CouncilRun) -> float:
    """Confidenza aggregata: media dei verdetti conclusivi, 0 se non decide nulla."""
    conclusive = [v.confidence for v in run.verdicts if v.conclusive]
    if not conclusive:
        return 0.0
    return round(sum(conclusive) / len(conclusive), 3)


def _next_action(run: CouncilRun) -> str:
    incomplete = run.outcome.incomplete_axes()
    if run.outcome.axes_needing_arbitration():
        return "Risolvere la discordanza con un esperimento eseguito nel gate deterministico."
    if run.outcome.failed_axes():
        return "Correggere le violazioni indicate e rieseguire i soli segnali falliti."
    if incomplete:
        return (
            "Coprire gli assi non decisi ("
            + ", ".join(r.axis for r in incomplete)
            + ") con reviewer semantici o evidenza aggiuntiva."
        )
    return "Rerun deterministico di conferma prima di qualunque promozione."
