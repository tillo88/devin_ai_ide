"""Federated Evidence Council — Fase 2: CouncilRouter.

Design: `docs/devin_federated_council_design_v1.md` §4.2.

Il router NON fa numero: sceglie i reviewer per **coprire gli assi**. Regole
implementate, tutte deterministiche e testabili offline:

- ogni asse coperto da >= 1 reviewer, quando esiste qualcuno che sa coprirlo;
- su cambio critico si aggiungono reviewer per asse, ma **bounded**;
- **mai due reviewer della stessa famiglia sullo stesso asse** (errori correlati:
  due modelli della stessa famiglia sbagliano insieme, e il loro accordo non e'
  evidenza);
- il pacchetto per-reviewer e' **cieco** (nessun verdetto altrui) e porta la
  **lente dell'asse**;
- gli assi che nessuno sa coprire NON vengono nascosti: finiscono in
  `uncovered_axes`, cosi' l'Aggregator sa che la copertura e' incompleta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from devin.core.council import (
    AXES,
    AXIS_LENS,
    CouncilError,
    ReviewerAdapter,
    ReviewPacket,
)


@dataclass(frozen=True)
class Assignment:
    """Un reviewer, un asse, un pacchetto cieco tarato su quell'asse."""

    reviewer_id: str
    family: str
    axis: str
    packet: ReviewPacket
    lens: str
    reviewer: Optional[ReviewerAdapter] = field(default=None, compare=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "family": self.family,
            "axis": self.axis,
            "packet_id": self.packet.packet_id,
            "lens": self.lens,
        }


@dataclass
class RoutingPlan:
    """Piano di review: chi guarda cosa, e cosa resta scoperto."""

    packet_id: str
    assignments: List[Assignment] = field(default_factory=list)
    uncovered_axes: List[str] = field(default_factory=list)
    critical: bool = False

    @property
    def covered_axes(self) -> List[str]:
        seen: List[str] = []
        for a in self.assignments:
            if a.axis not in seen:
                seen.append(a.axis)
        return seen

    @property
    def complete(self) -> bool:
        """True se ogni asse richiesto ha almeno un reviewer assegnato."""
        return not self.uncovered_axes

    def for_axis(self, axis: str) -> List[Assignment]:
        return [a for a in self.assignments if a.axis == axis]

    def families_on(self, axis: str) -> List[str]:
        return [a.family for a in self.for_axis(axis)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "critical": self.critical,
            "assignments": [a.to_dict() for a in self.assignments],
            "covered_axes": self.covered_axes,
            "uncovered_axes": list(self.uncovered_axes),
            "complete": self.complete,
        }


class CouncilRouter:
    """Sceglie i reviewer per copertura, non per quantita'.

    `max_per_axis` limita i reviewer su un asse in condizioni normali;
    `critical_max_per_axis` e' il tetto sui cambi critici. Il totale e' comunque
    limitato da `max_total`, cosi' un Council esteso resta bounded (il budget
    vero e' della Fase 5, questo e' il tetto strutturale).
    """

    def __init__(
        self,
        reviewers: Sequence[ReviewerAdapter],
        *,
        max_per_axis: int = 1,
        critical_max_per_axis: int = 2,
        max_total: int = 12,
    ):
        if max_per_axis < 1 or critical_max_per_axis < 1:
            raise CouncilError("max_per_axis e critical_max_per_axis devono essere >= 1")
        if critical_max_per_axis < max_per_axis:
            raise CouncilError("critical_max_per_axis non puo' essere minore di max_per_axis")
        self.reviewers = list(reviewers)
        self.max_per_axis = int(max_per_axis)
        self.critical_max_per_axis = int(critical_max_per_axis)
        self.max_total = int(max_total)

    # ordinamento stabile: il piano deve essere riproducibile
    def _candidates(self, axis: str) -> List[ReviewerAdapter]:
        return sorted(
            (r for r in self.reviewers if r.can_review(axis)),
            key=lambda r: (str(r.family), str(r.reviewer_id)),
        )

    def route(
        self,
        packet: ReviewPacket,
        *,
        critical: bool = False,
        axes: Sequence[str] = AXES,
    ) -> RoutingPlan:
        for axis in axes:
            if axis not in AXES:
                raise CouncilError(f"asse sconosciuto nel routing: {axis!r}")

        limit = self.critical_max_per_axis if critical else self.max_per_axis
        plan = RoutingPlan(packet_id=packet.packet_id, critical=critical)

        for axis in axes:
            chosen: List[ReviewerAdapter] = []
            used_families: set = set()
            for reviewer in self._candidates(axis):
                if len(chosen) >= limit:
                    break
                if len(plan.assignments) + len(chosen) >= self.max_total:
                    break
                # niente duplicati di famiglia sullo stesso asse: due reviewer
                # della stessa famiglia sbagliano in modo correlato.
                if reviewer.family in used_families:
                    continue
                used_families.add(reviewer.family)
                chosen.append(reviewer)

            if not chosen:
                plan.uncovered_axes.append(axis)
                continue

            for reviewer in chosen:
                plan.assignments.append(
                    Assignment(
                        reviewer_id=reviewer.reviewer_id,
                        family=reviewer.family,
                        axis=axis,
                        packet=packet,          # gia' cieco per costruzione
                        lens=AXIS_LENS.get(axis, ""),
                        reviewer=reviewer,
                    )
                )
        return plan
