"""Federated Evidence Council — Fase 5: Capacity & Context Budgeter + dispatch.

Design: `docs/devin_federated_council_design_v1.md` §4.5.

Regole implementate:

- budget di **tempo** e **numero di review** per l'intero Council e per singolo
  reviewer (Colibri e' lento: unita' piccole, §11.5);
- un reviewer che **sfora, esplode o non risponde** viene degradato: il Council
  continua con gli altri invece di bloccarsi;
- **niente promozione se il budget non ha permesso la copertura minima**: le
  degradazioni sono registrate e diventano assi non coperti per l'Aggregator.

Onesta' sui limiti: questo budgeter **misura e smette di assegnare**, non uccide
una chiamata gia' partita (una `review()` sincrona non e' interrompibile senza
thread/processi). Il timeout duro appartiene al trasporto del reviewer remoto,
non a questo livello — qui si evita che UN reviewer lento consumi il Council.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from devin.core.council import ReviewVerdict
from devin.core.council_router import Assignment, RoutingPlan

# motivi di degrado (finiscono nella provenance)
DEGRADED_BUDGET_TOTAL = "budget_totale_esaurito"
DEGRADED_BUDGET_REVIEWER = "budget_reviewer_esaurito"
DEGRADED_ERROR = "reviewer_errore"
DEGRADED_TIMEOUT = "reviewer_troppo_lento"
DEGRADED_CONTRACT = "verdetto_non_conforme"


@dataclass
class Budget:
    """Tetti del Council. `None` = nessun limite su quella dimensione."""

    max_total_seconds: Optional[float] = None
    max_seconds_per_reviewer: Optional[float] = None
    max_reviews: Optional[int] = None
    max_reviews_per_reviewer: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_total_seconds": self.max_total_seconds,
            "max_seconds_per_reviewer": self.max_seconds_per_reviewer,
            "max_reviews": self.max_reviews,
            "max_reviews_per_reviewer": self.max_reviews_per_reviewer,
        }


@dataclass
class Degradation:
    """Una review che NON e' avvenuta (o non e' valida), con il motivo."""

    reviewer_id: str
    axis: str
    reason: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"reviewer_id": self.reviewer_id, "axis": self.axis,
                "reason": self.reason, "detail": self.detail}


@dataclass
class DispatchReport:
    """Cosa e' stato speso, cosa e' riuscito, cosa e' degradato."""

    verdicts: List[ReviewVerdict] = field(default_factory=list)
    degradations: List[Degradation] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    reviews_done: int = 0
    per_reviewer_seconds: Dict[str, float] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return bool(self.degradations)

    def uncovered_axes(self, plan: RoutingPlan) -> List[str]:
        """Assi rimasti senza NESSUN verdetto dopo il dispatch.

        Unisce gli assi che nessuno sapeva coprire (dal piano) con quelli persi
        per degrado: per l'Aggregator sono la stessa cosa — copertura mancante,
        quindi niente promozione.
        """
        got = {v.axis for v in self.verdicts}
        lost = [a for a in plan.covered_axes if a not in got]
        return list(plan.uncovered_axes) + lost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdicts": [v.to_dict() for v in self.verdicts],
            "degradations": [d.to_dict() for d in self.degradations],
            "elapsed_seconds": round(self.elapsed_seconds, 4),
            "reviews_done": self.reviews_done,
            "degraded": self.degraded,
            "per_reviewer_seconds": {k: round(v, 4) for k, v in self.per_reviewer_seconds.items()},
        }


class CouncilBudgeter:
    """Contabilita' del budget. Non esegue: decide se si puo' ancora spendere."""

    def __init__(self, budget: Optional[Budget] = None, *, clock: Callable[[], float] = time.monotonic):
        self.budget = budget or Budget()
        self._clock = clock
        self._start: Optional[float] = None
        self.reviews_done = 0
        self.per_reviewer_seconds: Dict[str, float] = {}
        self.per_reviewer_count: Dict[str, int] = {}

    def start(self) -> None:
        self._start = self._clock()

    @property
    def elapsed(self) -> float:
        return 0.0 if self._start is None else self._clock() - self._start

    def total_exhausted(self) -> Optional[str]:
        b = self.budget
        if b.max_reviews is not None and self.reviews_done >= b.max_reviews:
            return DEGRADED_BUDGET_TOTAL
        if b.max_total_seconds is not None and self.elapsed >= b.max_total_seconds:
            return DEGRADED_BUDGET_TOTAL
        return None

    def reviewer_exhausted(self, reviewer_id: str) -> Optional[str]:
        b = self.budget
        if (b.max_reviews_per_reviewer is not None
                and self.per_reviewer_count.get(reviewer_id, 0) >= b.max_reviews_per_reviewer):
            return DEGRADED_BUDGET_REVIEWER
        if (b.max_seconds_per_reviewer is not None
                and self.per_reviewer_seconds.get(reviewer_id, 0.0) >= b.max_seconds_per_reviewer):
            return DEGRADED_BUDGET_REVIEWER
        return None

    def record(self, reviewer_id: str, seconds: float, *, counted: bool = True) -> None:
        self.per_reviewer_seconds[reviewer_id] = self.per_reviewer_seconds.get(reviewer_id, 0.0) + seconds
        if counted:
            self.per_reviewer_count[reviewer_id] = self.per_reviewer_count.get(reviewer_id, 0) + 1
            self.reviews_done += 1

    def overran(self, seconds: float) -> bool:
        """La singola chiamata ha sforato il tetto per-reviewer?"""
        cap = self.budget.max_seconds_per_reviewer
        return cap is not None and seconds > cap


def dispatch_plan(
    plan: RoutingPlan,
    *,
    budget: Optional[Budget] = None,
    clock: Callable[[], float] = time.monotonic,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> DispatchReport:
    """Esegue un `RoutingPlan` rispettando il budget e degradando sui problemi.

    `on_event` e' l'heartbeat: viene chiamato prima e dopo ogni review, cosi' un
    Council lento e' osservabile invece che silenzioso.
    """
    budgeter = CouncilBudgeter(budget, clock=clock)
    budgeter.start()
    report = DispatchReport()

    def emit(kind: str, payload: Dict[str, Any]) -> None:
        if on_event:
            on_event(kind, payload)

    for assignment in plan.assignments:
        stop = budgeter.total_exhausted()
        if stop:
            report.degradations.append(Degradation(
                assignment.reviewer_id, assignment.axis, stop,
                f"budget Council esaurito dopo {budgeter.reviews_done} review",
            ))
            continue

        stop = budgeter.reviewer_exhausted(assignment.reviewer_id)
        if stop:
            report.degradations.append(Degradation(
                assignment.reviewer_id, assignment.axis, stop,
                "quota del singolo reviewer esaurita",
            ))
            continue

        reviewer = assignment.reviewer
        if reviewer is None:
            report.degradations.append(Degradation(
                assignment.reviewer_id, assignment.axis, DEGRADED_ERROR,
                "assegnazione senza istanza di reviewer",
            ))
            continue

        emit("review_start", {"reviewer_id": assignment.reviewer_id, "axis": assignment.axis})
        started = clock()
        try:
            verdict = reviewer.review(assignment.packet, assignment.axis)
        except Exception as exc:  # il Council non si blocca per un reviewer rotto
            spent = clock() - started
            budgeter.record(assignment.reviewer_id, spent, counted=False)
            report.per_reviewer_seconds = dict(budgeter.per_reviewer_seconds)
            report.degradations.append(Degradation(
                assignment.reviewer_id, assignment.axis, DEGRADED_ERROR, str(exc)[:300],
            ))
            emit("review_error", {"reviewer_id": assignment.reviewer_id,
                                  "axis": assignment.axis, "error": str(exc)[:300]})
            continue

        spent = clock() - started
        budgeter.record(assignment.reviewer_id, spent)
        report.per_reviewer_seconds = dict(budgeter.per_reviewer_seconds)
        report.reviews_done = budgeter.reviews_done

        if not isinstance(verdict, ReviewVerdict) or verdict.axis != assignment.axis:
            report.degradations.append(Degradation(
                assignment.reviewer_id, assignment.axis, DEGRADED_CONTRACT,
                "il reviewer ha restituito un verdetto non conforme o su un altro asse",
            ))
            continue

        if budgeter.overran(spent):
            # Il verdetto e' arrivato ma fuori budget: si tiene (e' evidenza
            # reale) e si registra il degrado, cosi' il reviewer lento non
            # ottiene altro lavoro.
            report.degradations.append(Degradation(
                assignment.reviewer_id, assignment.axis, DEGRADED_TIMEOUT,
                f"{spent:.3f}s oltre il tetto di {budgeter.budget.max_seconds_per_reviewer}s",
            ))

        report.verdicts.append(verdict)
        emit("review_done", {"reviewer_id": assignment.reviewer_id,
                             "axis": assignment.axis, "verdict": verdict.verdict,
                             "seconds": round(spent, 4)})

    report.elapsed_seconds = budgeter.elapsed
    return report
