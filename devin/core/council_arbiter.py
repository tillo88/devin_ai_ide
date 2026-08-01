"""Federated Evidence Council — Fase 4: Arbiter.

Design: `docs/devin_federated_council_design_v1.md` §4.4 (ruolo) e §11 (runtime
GLM-Colibri come endpoint effimero batch).

L'arbitro **non e' un voto in piu'**. Sulla discordanza:

1. legge le ragioni contrastanti;
2. **genera un esperimento** discriminante;
3. l'esperimento gira nel **gate deterministico** (rerun);
4. il risultato REALE risolve la discordanza.

Due proprieta' volute, entrambe verificate dai test:

- **Pre-registrazione.** L'esperimento dichiara IN ANTICIPO (`on_pass`/`on_fail`)
  cosa implichera' ciascun esito per l'asse. Cosi' non si puo' razionalizzare il
  risultato dopo averlo visto: e' la stessa disciplina della calibrazione, dove
  il criterio si fissa prima del run.
- **L'esito inconclusivo non diventa un pass.** Se l'esperimento non si genera,
  non gira o non decide, l'asse resta irrisolto e va a review umana. Il silenzio
  non promuove (stessa regola del reviewer deterministico).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from devin.core.council import (
    VERDICT_FAIL,
    VERDICT_NEEDS_EVIDENCE,
    VERDICT_PASS,
    CouncilError,
    ReviewPacket,
    ReviewVerdict,
)
from devin.core.council_aggregator import AxisResult

# tipi di esperimento ammessi (allineati allo schema §11.4)
EXPERIMENT_KINDS = ("gold_test", "unit", "property", "concept_check")

# esiti dell'esecuzione
RESULT_PASSED = "passed"
RESULT_FAILED = "failed"
RESULT_INCONCLUSIVE = "inconclusive"


@dataclass
class Experiment:
    """Prova discriminante proposta dall'arbitro, con esiti pre-registrati."""

    kind: str
    spec: str
    on_pass: str          # verdetto per l'asse SE l'esperimento passa
    on_fail: str          # verdetto per l'asse SE l'esperimento fallisce
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EXPERIMENT_KINDS:
            raise CouncilError(f"tipo di esperimento sconosciuto: {self.kind!r}")
        if not str(self.spec).strip():
            raise CouncilError("spec dell'esperimento obbligatoria")
        for label, value in (("on_pass", self.on_pass), ("on_fail", self.on_fail)):
            if value not in (VERDICT_PASS, VERDICT_FAIL):
                raise CouncilError(f"{label} deve essere pass o fail (pre-registrazione), non {value!r}")
        if self.on_pass == self.on_fail:
            raise CouncilError(
                "on_pass e on_fail identici: l'esperimento non discrimina nulla"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "spec": self.spec, "on_pass": self.on_pass,
            "on_fail": self.on_fail, "rationale": self.rationale,
        }


@dataclass
class ExperimentResult:
    """Esito REALE dell'esecuzione nel gate deterministico."""

    status: str
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in (RESULT_PASSED, RESULT_FAILED, RESULT_INCONCLUSIVE):
            raise CouncilError(f"esito esperimento sconosciuto: {self.status!r}")

    @property
    def decisive(self) -> bool:
        return self.status in (RESULT_PASSED, RESULT_FAILED)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "evidence": dict(self.evidence)}


@dataclass
class ArbitrationRecord:
    """Traccia completa di un arbitrato: proposta, esecuzione, verdetto risolto."""

    axis: str
    packet_id: str
    arbiter_id: str
    resolved_verdict: str
    reasoning: str
    experiment: Optional[Experiment] = None
    result: Optional[ExperimentResult] = None
    conflicting_reasons: List[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.resolved_verdict in (VERDICT_PASS, VERDICT_FAIL)

    def to_verdict(self, family: str = "arbiter") -> ReviewVerdict:
        """Il verdetto risolto, con la provenance dell'evidenza."""
        return ReviewVerdict(
            axis=self.axis,
            verdict=self.resolved_verdict,
            reviewer_id=self.arbiter_id,
            family=family,
            reasoning=self.reasoning,
            confidence=1.0 if self.resolved else 0.0,
            proposed_experiment=self.experiment.spec if self.experiment else None,
            evidence={
                "arbitration": True,
                "experiment": self.experiment.to_dict() if self.experiment else None,
                "experiment_result": self.result.to_dict() if self.result else None,
                "conflicting_reasons": list(self.conflicting_reasons),
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "packet_id": self.packet_id,
            "arbiter_id": self.arbiter_id,
            "resolved_verdict": self.resolved_verdict,
            "resolved": self.resolved,
            "reasoning": self.reasoning,
            "experiment": self.experiment.to_dict() if self.experiment else None,
            "result": self.result.to_dict() if self.result else None,
            "conflicting_reasons": list(self.conflicting_reasons),
        }


class ExperimentGenerator(Protocol):
    """Propone l'esperimento. In futuro: GLM-Colibri (§11), JSON grammar-forced."""

    def generate(self, axis_result: AxisResult, packet: ReviewPacket) -> Optional[Experiment]:
        ...


class ExperimentRunner(Protocol):
    """Esegue l'esperimento nel gate deterministico. Qui sta la verita'."""

    def run(self, experiment: Experiment, packet: ReviewPacket) -> ExperimentResult:
        ...


class NullExperimentGenerator:
    """Generatore che non sa proporre (nessun modello disponibile).

    Serve come default onesto: senza arbitro-modello la discordanza NON si
    risolve da sola e finisce a review umana.
    """

    def generate(self, axis_result: AxisResult, packet: ReviewPacket) -> Optional[Experiment]:
        return None


class Arbiter:
    """Risolve una discordanza con un esperimento eseguito, non con autorevolezza."""

    def __init__(
        self,
        generator: Optional[ExperimentGenerator] = None,
        runner: Optional[ExperimentRunner] = None,
        *,
        arbiter_id: str = "arbiter",
    ):
        self.generator = generator or NullExperimentGenerator()
        self.runner = runner
        self.arbiter_id = arbiter_id

    def arbitrate(self, axis_result: AxisResult, packet: ReviewPacket) -> ArbitrationRecord:
        if not axis_result.needs_arbitration:
            raise CouncilError(
                f"asse {axis_result.axis!r} non e' in discordanza: niente da arbitrare"
            )
        conflicting = axis_result.conflicting_reasons()
        base = dict(
            axis=axis_result.axis,
            packet_id=packet.packet_id,
            arbiter_id=self.arbiter_id,
            conflicting_reasons=conflicting,
        )

        experiment = self.generator.generate(axis_result, packet)
        if experiment is None:
            return ArbitrationRecord(
                resolved_verdict=VERDICT_NEEDS_EVIDENCE,
                reasoning=(
                    "Nessun esperimento discriminante proponibile: la discordanza resta aperta "
                    "e richiede review umana. Non si sceglie un lato per maggioranza."
                ),
                **base,
            )

        if self.runner is None:
            return ArbitrationRecord(
                experiment=experiment,
                resolved_verdict=VERDICT_NEEDS_EVIDENCE,
                reasoning=(
                    "Esperimento proposto ma nessun runner deterministico disponibile per eseguirlo. "
                    "Una proposta non eseguita non e' evidenza."
                ),
                **base,
            )

        result = self.runner.run(experiment, packet)
        if not result.decisive:
            return ArbitrationRecord(
                experiment=experiment,
                result=result,
                resolved_verdict=VERDICT_NEEDS_EVIDENCE,
                reasoning=(
                    f"Esperimento eseguito ma inconclusivo ({result.detail or 'nessun dettaglio'}): "
                    "l'asse resta irrisolto. Un esito non decisivo non diventa un pass."
                ),
                **base,
            )

        verdict = experiment.on_pass if result.status == RESULT_PASSED else experiment.on_fail
        return ArbitrationRecord(
            experiment=experiment,
            result=result,
            resolved_verdict=verdict,
            reasoning=(
                f"Discordanza risolta dall'evidenza: l'esperimento '{experiment.kind}' e' "
                f"{result.status}, esito pre-registrato come {verdict}. "
                f"{result.detail}".strip()
            ),
            **base,
        )
