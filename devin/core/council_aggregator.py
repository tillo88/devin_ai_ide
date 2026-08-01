"""Federated Evidence Council — Fase 3: Aggregator.

Design: `docs/devin_federated_council_design_v1.md` §4.3.

Raccoglie i verdetti per asse e decide **come procedere**, non promuove. Regole:

- concorde `pass` su tutti gli assi coperti -> *candidato* a `verified_success`,
  ma soggetto a rerun: qui NON si promuove nulla;
- concorde `fail` su un asse -> `verified_failure` con motivo;
- **discordanza** su un asse -> va all'arbitro. Non si decide a maggioranza
  cieca: due voti contro uno non sono una prova;
- copertura incompleta (asse senza verdetto conclusivo, o assi che nessuno sa
  coprire) -> niente promozione, serve review umana.

La mappatura verso lo status ladder esistente (`devin/training/store.py`) e'
esplicita e conservativa: un candidato pass diventa `pending_review`, **mai**
`verified_success`, finche' il rerun non lo conferma (anti auto-promozione, §1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from devin.core.council import (
    AXES,
    VERDICT_FAIL,
    VERDICT_PASS,
    ReviewVerdict,
)

# --- esito per asse -------------------------------------------------------
AXIS_PASS = "pass"
AXIS_FAIL = "fail"
AXIS_DISCORDANT = "discordant"
AXIS_UNRESOLVED = "unresolved"        # nessun verdetto conclusivo
AXIS_UNCOVERED = "uncovered"          # nessun reviewer sapeva coprirlo

# --- esito del Council ----------------------------------------------------
OUTCOME_PASS_CANDIDATE = "verified_success_candidate"
OUTCOME_FAILURE = "verified_failure"
OUTCOME_NEEDS_ARBITRATION = "needs_arbitration"
OUTCOME_NEEDS_HUMAN = "needs_human_review"

# Mappa conservativa sullo status ladder di store.py. Nota: il candidato NON
# diventa verified_success qui — solo il rerun puo' promuoverlo.
OUTCOME_TO_STORE_STATUS: Dict[str, str] = {
    OUTCOME_PASS_CANDIDATE: "pending_review",
    OUTCOME_FAILURE: "verified_failure",
    OUTCOME_NEEDS_ARBITRATION: "pending_review",
    OUTCOME_NEEDS_HUMAN: "pending_review",
}


@dataclass
class AxisResult:
    """Come si è chiuso un singolo asse."""

    axis: str
    status: str
    verdicts: List[ReviewVerdict] = field(default_factory=list)
    reason: str = ""

    @property
    def needs_arbitration(self) -> bool:
        return self.status == AXIS_DISCORDANT

    def conflicting_reasons(self) -> List[str]:
        """Ragioni contrastanti, materia prima dell'arbitro."""
        return [
            f"[{v.reviewer_id}/{v.family}] {v.verdict}: {v.reasoning}"
            for v in self.verdicts
            if v.conclusive
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "status": self.status,
            "reason": self.reason,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


@dataclass
class CouncilOutcome:
    """Esito aggregato. Non promuove: dice cosa fare dopo."""

    packet_id: str
    outcome: str
    axis_results: List[AxisResult] = field(default_factory=list)
    reason: str = ""

    @property
    def store_status(self) -> str:
        return OUTCOME_TO_STORE_STATUS.get(self.outcome, "pending_review")

    @property
    def promotable(self) -> bool:
        """Mai True: nessun esito del Council promuove da solo (serve il rerun)."""
        return False

    def axes_needing_arbitration(self) -> List[AxisResult]:
        return [r for r in self.axis_results if r.needs_arbitration]

    def failed_axes(self) -> List[AxisResult]:
        return [r for r in self.axis_results if r.status == AXIS_FAIL]

    def incomplete_axes(self) -> List[AxisResult]:
        return [r for r in self.axis_results if r.status in (AXIS_UNRESOLVED, AXIS_UNCOVERED)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "outcome": self.outcome,
            "store_status": self.store_status,
            "promotable": self.promotable,
            "reason": self.reason,
            "axis_results": [r.to_dict() for r in self.axis_results],
        }


def aggregate_axis(axis: str, verdicts: Sequence[ReviewVerdict]) -> AxisResult:
    """Chiude un singolo asse a partire dai verdetti ricevuti."""
    relevant = [v for v in verdicts if v.axis == axis]
    conclusive = [v for v in relevant if v.conclusive]

    if not relevant:
        return AxisResult(axis, AXIS_UNCOVERED, [], "nessun reviewer assegnato a questo asse")
    if not conclusive:
        return AxisResult(
            axis,
            AXIS_UNRESOLVED,
            relevant,
            "nessun verdetto conclusivo: tutti i reviewer hanno chiesto altra evidenza",
        )

    passes = [v for v in conclusive if v.verdict == VERDICT_PASS]
    fails = [v for v in conclusive if v.verdict == VERDICT_FAIL]

    if passes and fails:
        # Discordanza: NON si risolve a maggioranza. Serve un esperimento.
        return AxisResult(
            axis,
            AXIS_DISCORDANT,
            relevant,
            f"{len(passes)} pass contro {len(fails)} fail: discordanza da risolvere con evidenza, "
            "non con un voto",
        )
    if fails:
        motivi = "; ".join(v.reasoning for v in fails)[:600]
        return AxisResult(axis, AXIS_FAIL, relevant, f"tutti i verdetti conclusivi sono fail: {motivi}")
    return AxisResult(
        axis,
        AXIS_PASS,
        relevant,
        f"{len(passes)} verdetto/i conclusivo/i concordi su pass",
    )


class Aggregator:
    """Aggrega i verdetti dei 5 assi in un esito di Council."""

    def __init__(self, *, required_axes: Sequence[str] = AXES):
        self.required_axes = tuple(required_axes)

    def aggregate(
        self,
        packet_id: str,
        verdicts: Sequence[ReviewVerdict],
        *,
        uncovered_axes: Optional[Sequence[str]] = None,
    ) -> CouncilOutcome:
        uncovered = set(uncovered_axes or ())
        results: List[AxisResult] = []
        for axis in self.required_axes:
            if axis in uncovered:
                results.append(
                    AxisResult(axis, AXIS_UNCOVERED, [], "nessun reviewer disponibile sa coprire l'asse")
                )
                continue
            results.append(aggregate_axis(axis, verdicts))

        discordant = [r for r in results if r.status == AXIS_DISCORDANT]
        failed = [r for r in results if r.status == AXIS_FAIL]
        incomplete = [r for r in results if r.status in (AXIS_UNRESOLVED, AXIS_UNCOVERED)]

        # Ordine di precedenza: una discordanza va risolta PRIMA di dichiarare
        # qualsiasi esito; un fail conclusivo batte la copertura incompleta.
        if discordant:
            outcome = OUTCOME_NEEDS_ARBITRATION
            reason = "discordanza su: " + ", ".join(r.axis for r in discordant)
        elif failed:
            outcome = OUTCOME_FAILURE
            reason = "asse/i bocciato/i: " + ", ".join(r.axis for r in failed)
        elif incomplete:
            outcome = OUTCOME_NEEDS_HUMAN
            reason = (
                "copertura incompleta su: "
                + ", ".join(f"{r.axis}({r.status})" for r in incomplete)
                + " — nessuna promozione con assi non decisi"
            )
        else:
            outcome = OUTCOME_PASS_CANDIDATE
            reason = "tutti gli assi concordi su pass; resta obbligatorio il rerun prima della promozione"

        return CouncilOutcome(packet_id=packet_id, outcome=outcome, axis_results=results, reason=reason)
