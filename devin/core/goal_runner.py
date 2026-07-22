"""Goal Mode — loop di controllo (Fase 2, parte testabile offline).

Design: docs/devin_roadmap_skills-goalmode_v2.md

Questa e' la MACCHINA A STATI della Goal Mode, separata da chi esegue davvero i
passi. L'esecutore di step (`StepExecutor`) e' INIETTATO: qui non si avvia nessun
modello. In produzione l'esecutore avvolgera' `orchestrator.run` con un ruolo del
mini-swarm; nei test e' uno stub. Cosi' la logica di loop — budget, condizioni di
stop, cambio strategia sul blocco (D2), checkpoint approvazione (D4) — e'
verificabile senza VRAM.

Contratto:
- `run_goal(goal, root, executor)` cicla: valuta -> se non soddisfatto esegue uno
  step -> rivaluta -> registra l'attempt -> applica le regole di stop.
- Ogni step produce un `Attempt` registrato: e' il materiale grezzo che (fase
  successiva) alimenta la pipeline di training Teacher (D2: piu' prove = piu'
  memoria).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from devin.core.goal_mode import Goal, GoalEvaluation, evaluate_goal

# Esiti possibili di uno step eseguito da un ruolo.
STEP_CHANGED = "changed"      # ha prodotto modifiche
STEP_NO_CHANGE = "no_change"  # nessuna modifica (es. analisi)
STEP_FAILED = "failed"        # tentativo fallito, ma ritentabile con altra strategia
STEP_BLOCKED = "blocked"      # bloccato duro: l'esecutore non sa come procedere

# Stato finale del loop.
RESULT_SUCCESS = "success"
RESULT_BLOCKED = "blocked"
RESULT_BUDGET = "budget_exhausted"
RESULT_NEEDS_APPROVAL = "needs_approval"


@dataclass
class StepContext:
    """Cosa sa l'esecutore quando gli si chiede il prossimo passo."""

    pending: list  # criteri ancora non soddisfatti (CriterionResult)
    attempt_index: int
    history: list  # attempts precedenti, per variare strategia (D2)


@dataclass
class StepOutcome:
    """Cosa riporta l'esecutore all'orchestratore (autonomia circoscritta)."""

    status: str                 # STEP_*
    strategy: str = ""          # quale ruolo/strategia ha usato
    detail: str = ""
    produced_changes: bool = False
    failure_signature: str = ""  # per rilevare "stesso fallimento ripetuto" (D2)


@dataclass
class Attempt:
    index: int
    strategy: str
    status: str
    detail: str
    evaluation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "strategy": self.strategy,
            "status": self.status,
            "detail": self.detail,
            "evaluation": self.evaluation,
        }


@dataclass
class GoalRunResult:
    status: str                       # RESULT_*
    reason: str
    attempts: list[Attempt] = field(default_factory=list)
    evaluation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "attempts": [a.to_dict() for a in self.attempts],
            "evaluation": self.evaluation,
        }


StepExecutor = Callable[[Goal, Path, StepContext], StepOutcome]


def run_goal(
    goal: Goal,
    project_root: Path | str,
    executor: StepExecutor,
    *,
    max_identical_failures: int = 3,
    clock: Callable[[], float] = time.monotonic,
) -> GoalRunResult:
    """Esegue il loop della Goal Mode fino a successo / blocco / budget / pausa.

    - success: tutti i criteri soddisfatti.
    - blocked: esecutore bloccato, oppure STESSO fallimento ripetuto
      `max_identical_failures` volte (D2: prima si cambia strategia, poi si
      escala).
    - budget_exhausted: esauriti step o tempo.
    - needs_approval: uno step ha prodotto modifiche e la modalita' richiede
      approvazione (D4: maintenance manuale). In scaffold/auto non si ferma.
    """
    goal.validate()
    root = Path(project_root)

    ev: GoalEvaluation = evaluate_goal(goal, root)
    if ev.satisfied:
        return GoalRunResult(RESULT_SUCCESS, "gia' soddisfatto all'avvio", [], ev.to_dict())

    attempts: list[Attempt] = []
    failure_counts: dict[str, int] = {}
    start = clock()

    for step in range(goal.budget_steps):
        if clock() - start >= goal.budget_seconds:
            return GoalRunResult(RESULT_BUDGET, "tempo esaurito", attempts, ev.to_dict())

        ctx = StepContext(pending=ev.pending, attempt_index=step, history=list(attempts))
        outcome = executor(goal, root, ctx)

        # Rivaluta lo stato reale dopo lo step (la verita' e' sul filesystem).
        ev = evaluate_goal(goal, root)
        attempts.append(Attempt(step, outcome.strategy, outcome.status, outcome.detail, ev.to_dict()))

        if ev.satisfied:
            return GoalRunResult(RESULT_SUCCESS, "criteri soddisfatti", attempts, ev.to_dict())

        if outcome.status == STEP_BLOCKED:
            return GoalRunResult(RESULT_BLOCKED, f"esecutore bloccato: {outcome.detail}", attempts, ev.to_dict())

        if outcome.status == STEP_FAILED:
            sig = outcome.failure_signature or outcome.detail or "unknown"
            failure_counts[sig] = failure_counts.get(sig, 0) + 1
            if failure_counts[sig] >= max_identical_failures:
                return GoalRunResult(
                    RESULT_BLOCKED,
                    f"stesso fallimento ripetuto x{failure_counts[sig]}: {sig}",
                    attempts, ev.to_dict(),
                )

        # Checkpoint D4: modifiche + modalita' che richiede approvazione -> pausa.
        if outcome.produced_changes and goal.requires_checkpoint():
            return GoalRunResult(RESULT_NEEDS_APPROVAL, "modifiche in attesa di approvazione", attempts, ev.to_dict())

    return GoalRunResult(RESULT_BUDGET, "step esauriti", attempts, ev.to_dict())
