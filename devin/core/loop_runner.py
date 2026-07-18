"""Loop runner: goal + azione + verifica + stop condition.

Il concetto (dal video "Agent Loops" + Loop Library, roadmap 2026-07): non
promptare l'agente una volta, ma DEFINIRE UN LOOP — un obiettivo oggettivo,
un'azione che itera, e uno STOP misurabile. DEVIN aveva solo run one-shot con
max 3 retry impliciti; questo generalizza con guardie da harness-engineering:
max iterazioni, budget tempo, streak di successi, log durevole, stop esplicito.

Design volutamente PURO (nessuna dipendenza da orchestrator/modelli): action e
verifier sono callable iniettati, cosi' e' testabile senza GPU e riusabile da
scaffold, maintenance e futuri loop (coverage, docs-sweep, quality-streak).

Contratti:
  action(iteration:int, last:VerifyResult|None) -> Any
      esegue un giro (genera/corregge). Puo' usare l'esito precedente come
      feedback (il "reject and feed back").
  verifier(action_result:Any) -> VerifyResult
      giudizio OGGETTIVO del giro (di norma il quality gate).

Stop: raggiunto lo streak richiesto di verify.ok consecutivi, oppure esaurite
le iterazioni / il budget tempo / stop_requested. La memoria NON viene toccata
qui: il loop produce solo un LoopOutcome con la traccia, il chiamante decide.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


@dataclass
class VerifyResult:
    ok: bool
    detail: str = ""
    evidence: Any = None


@dataclass
class LoopStep:
    iteration: int
    ok: bool
    detail: str
    seconds: float


@dataclass
class LoopOutcome:
    success: bool                 # streak raggiunto
    reason: str                   # "streak_reached" | "max_iterations" | "time_budget" | "stopped"
    iterations: int
    steps: List[LoopStep] = field(default_factory=list)
    last_result: Any = None       # ultimo action_result (es. dict scaffold)


def run_loop(
    action: Callable[[int, Optional[VerifyResult]], Any],
    verifier: Callable[[Any], VerifyResult],
    *,
    max_iterations: int = 3,
    success_streak: int = 1,
    time_budget_s: Optional[float] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    on_step: Optional[Callable[[LoopStep], None]] = None,
) -> LoopOutcome:
    """Esegue il loop finche' `success_streak` verifiche consecutive OK, o
    finche' scattano le guardie. Ritorna sempre un LoopOutcome (mai eccezioni
    di controllo-flusso).

    Guardie (tutte difensive):
      - max_iterations >= 1 (clampato);
      - success_streak >= 1 (clampato);
      - time_budget_s: superato tra un giro e l'altro -> stop "time_budget";
      - should_stop(): stop cooperativo dall'esterno (es. bottone Stop).
    """
    max_iterations = max(1, int(max_iterations))
    success_streak = max(1, int(success_streak))
    started = time.time()
    steps: List[LoopStep] = []
    streak = 0
    last_verify: Optional[VerifyResult] = None
    last_result: Any = None

    for i in range(1, max_iterations + 1):
        if should_stop and should_stop():
            return LoopOutcome(False, "stopped", i - 1, steps, last_result)
        if time_budget_s is not None and (time.time() - started) > time_budget_s:
            return LoopOutcome(False, "time_budget", i - 1, steps, last_result)

        t0 = time.time()
        last_result = action(i, last_verify)
        last_verify = verifier(last_result)
        step = LoopStep(i, bool(last_verify.ok), last_verify.detail, round(time.time() - t0, 2))
        steps.append(step)
        if on_step:
            try:
                on_step(step)
            except Exception:
                pass

        streak = streak + 1 if last_verify.ok else 0
        if streak >= success_streak:
            return LoopOutcome(True, "streak_reached", i, steps, last_result)

    return LoopOutcome(False, "max_iterations", max_iterations, steps, last_result)
