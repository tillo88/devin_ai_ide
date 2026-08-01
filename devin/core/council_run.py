"""Federated Evidence Council — orchestrazione end-to-end (Fasi 1-5 collegate).

```
route (chi guarda cosa, cieco)
  -> dispatch (budget + degrado, non si blocca)
  -> aggregate (per asse: concorde / discorde / non coperto)
  -> arbitra SOLO le discordanze (esperimento -> gate deterministico)
  -> ri-aggrega con i verdetti d'arbitrato
  -> esito + provenance completa
```

Vincoli strutturali:

- **un solo giro di arbitrato** (`max_arbitration_rounds=1`): se l'arbitro non
  risolve, l'asse diventa irrisolto e va a review umana. Niente loop silenziosi.
- **nessuna promozione qui.** L'esito e' una raccomandazione con evidenza; la
  promozione a `verified_*` resta gated dal rerun (§1 del design).
- **provenance integrale**: piano, degradazioni, verdetti, esperimenti ed esiti
  finiscono nel record, pronti per `reviews.jsonl` (append-only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from devin.core.council import ReviewerAdapter, ReviewPacket, ReviewVerdict
from devin.core.council_aggregator import (
    AXES,
    OUTCOME_NEEDS_ARBITRATION,
    Aggregator,
    CouncilOutcome,
)
from devin.core.council_arbiter import Arbiter, ArbitrationRecord
from devin.core.council_budget import Budget, DispatchReport, dispatch_plan
from devin.core.council_router import CouncilRouter, RoutingPlan


@dataclass
class CouncilRun:
    """Tutto cio' che e' successo in un giro di Council."""

    packet_id: str
    plan: RoutingPlan
    dispatch: DispatchReport
    outcome: CouncilOutcome
    arbitrations: List[ArbitrationRecord] = field(default_factory=list)
    preliminary_outcome: Optional[CouncilOutcome] = None

    @property
    def verdicts(self) -> List[ReviewVerdict]:
        return list(self.dispatch.verdicts)

    @property
    def arbitrated(self) -> bool:
        return bool(self.arbitrations)

    def to_review_payload(self) -> Dict[str, Any]:
        """Payload pronto per `store.add_review` (append-only, con provenance).

        `status` e' quello conservativo dell'Aggregator: un candidato pass resta
        `pending_review` finche' il rerun non lo conferma.
        """
        return {
            "attempt_id": self.packet_id,
            "status": self.outcome.store_status,
            "reviewer": "federated-council",
            "rationale": self.outcome.reason,
            "council": {
                "outcome": self.outcome.outcome,
                "promotable": self.outcome.promotable,
                "plan": self.plan.to_dict(),
                "dispatch": self.dispatch.to_dict(),
                "axis_results": [r.to_dict() for r in self.outcome.axis_results],
                "arbitrations": [a.to_dict() for a in self.arbitrations],
                "preliminary_outcome": (
                    self.preliminary_outcome.outcome if self.preliminary_outcome else None
                ),
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.to_review_payload()["council"]


def run_council(
    packet: ReviewPacket,
    reviewers: Sequence[ReviewerAdapter],
    *,
    critical: bool = False,
    axes: Sequence[str] = AXES,
    budget: Optional[Budget] = None,
    arbiter: Optional[Arbiter] = None,
    router: Optional[CouncilRouter] = None,
    aggregator: Optional[Aggregator] = None,
    max_arbitration_rounds: int = 1,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> CouncilRun:
    """Esegue un giro completo di Council su un pacchetto."""
    router = router or CouncilRouter(reviewers)
    aggregator = aggregator or Aggregator(required_axes=axes)

    plan = router.route(packet, critical=critical, axes=axes)
    report = dispatch_plan(plan, budget=budget, on_event=on_event)

    uncovered = report.uncovered_axes(plan)
    outcome = aggregator.aggregate(packet.packet_id, report.verdicts, uncovered_axes=uncovered)
    preliminary = outcome
    arbitrations: List[ArbitrationRecord] = []

    if (
        outcome.outcome == OUTCOME_NEEDS_ARBITRATION
        and arbiter is not None
        and max_arbitration_rounds > 0
    ):
        arbitrated_axes = set()
        for axis_result in outcome.axes_needing_arbitration():
            record = arbiter.arbitrate(axis_result, packet)
            arbitrations.append(record)
            arbitrated_axes.add(axis_result.axis)
            if on_event:
                on_event("arbitration", {
                    "axis": axis_result.axis,
                    "resolved": record.resolved,
                    "verdict": record.resolved_verdict,
                })

        # L'arbitrato SOSTITUISCE i verdetti discordi su quell'asse: il giro e'
        # bounded, non si ri-arbitra all'infinito.
        surviving = [v for v in report.verdicts if v.axis not in arbitrated_axes]
        resolved = [a.to_verdict() for a in arbitrations]
        outcome = aggregator.aggregate(
            packet.packet_id, surviving + resolved, uncovered_axes=uncovered
        )

    return CouncilRun(
        packet_id=packet.packet_id,
        plan=plan,
        dispatch=report,
        outcome=outcome,
        arbitrations=arbitrations,
        preliminary_outcome=preliminary if arbitrations else None,
    )
