"""Goal Mode — esecutori di step reali (ruoli del mini-swarm).

Design: docs/devin_roadmap_skills-goalmode_v2.md

Qui vive il primo ruolo concreto — lo **Scaffolder** — che collega il loop della
Goal Mode (`goal_runner.run_goal`) all'orchestrator esistente (`run_scaffold`).

Separazione voluta:
- `scaffolder_executor(...)` costruisce uno `StepExecutor` a partire da due
  callable INIETTATE (`run_scaffold_fn`, `apply_fn`): e' quindi testabile offline
  con stub, senza modelli ne' VRAM.
- `build_orchestrator_scaffold_runner(...)` e `default_apply_fn()` sono il
  cablaggio di PRODUZIONE (costruiscono l'Orchestrator, applicano il manifest):
  thin, non unit-testati perche' richiedono modelli/rig.

Nodo chiave (D4): in scaffold il loop non si ferma, quindi quando `run_scaffold`
ritorna `awaiting_approval` (manifest verificato NON applicato) l'esecutore deve
**auto-applicare** il manifest, altrimenti i file non finiscono su disco e il
criterio non avanzera' mai. In maintenance manuale, invece, NON applica: lascia
il manifest in attesa e il loop si mette in `needs_approval` per l'umano.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from devin.core.goal_mode import Goal
from devin.core.goal_runner import (
    STEP_CHANGED,
    STEP_FAILED,
    StepContext,
    StepExecutor,
    StepOutcome,
)

# Firma dei callable iniettati.
RunScaffoldFn = Callable[..., dict]          # (task, project_path, run_id) -> result dict
ApplyFn = Callable[[str, str], Any]          # (project_path, run_id) -> apply result


def _new_run_id() -> str:
    return time.strftime("goal_%Y%m%d_%H%M%S")


def failure_signature(result: dict) -> str:
    """Firma grezza per riconoscere lo STESSO fallimento ripetuto (D2).

    Volutamente coarse: preferisce l'errore esplicito, poi l'output del quality
    gate, poi lo stato. Non normalizza path/timestamp: e' un segnale, non una
    chiave crittografica. Se serve piu' precisione si raffina in seguito.
    """
    err = (result.get("error") or "").strip()
    if err:
        return err[:200]
    gate = result.get("quality_gate") or {}
    gate_out = (gate.get("output") or gate.get("status") or "").strip()
    if gate_out:
        return f"gate:{gate_out[:200]}"
    return f"status:{result.get('status') or 'unknown'}"


def outcome_from_scaffold_result(
    goal: Goal,
    project_root: Path,
    run_id: str,
    result: dict,
    apply_fn: ApplyFn | None,
) -> StepOutcome:
    """Mappa il risultato di run_scaffold in uno StepOutcome per il loop.

    - awaiting_approval + non serve checkpoint (scaffold/auto): applica il
      manifest ORA (auto-apply) -> changed.
    - awaiting_approval + serve checkpoint (maintenance manuale): NON applica ->
      changed con produced_changes, cosi' il loop si mette in needs_approval.
    - success (verified/syntax_only): changed.
    - fallito: failed con firma stabile.
    """
    status = result.get("status")

    if status == "awaiting_approval":
        if not goal.requires_checkpoint() and apply_fn is not None:
            try:
                apply_fn(str(project_root), run_id)
            except Exception as exc:  # apply fallito: trattalo come step fallito ritentabile
                return StepOutcome(
                    STEP_FAILED, strategy="scaffolder",
                    detail=f"apply manifest fallito: {type(exc).__name__}: {exc}",
                    failure_signature=f"apply-error:{type(exc).__name__}",
                )
            return StepOutcome(
                STEP_CHANGED, strategy="scaffolder", produced_changes=True,
                detail=f"manifest applicato (run {run_id})",
            )
        # Checkpoint umano richiesto: lascia il manifest in attesa.
        return StepOutcome(
            STEP_CHANGED, strategy="scaffolder", produced_changes=True,
            detail=f"manifest in attesa di approvazione (run {run_id})",
        )

    if result.get("success"):
        return StepOutcome(
            STEP_CHANGED, strategy="scaffolder",
            produced_changes=bool(result.get("files_written")),
            detail=str(status or "ok"),
        )

    return StepOutcome(
        STEP_FAILED, strategy="scaffolder",
        detail=(result.get("error") or "scaffold fallito")[:200],
        failure_signature=failure_signature(result),
    )


def scaffolder_executor(
    run_scaffold_fn: RunScaffoldFn,
    *,
    apply_fn: ApplyFn | None = None,
    run_id_factory: Callable[[], str] = _new_run_id,
) -> StepExecutor:
    """Costruisce lo StepExecutor del ruolo Scaffolder.

    `run_scaffold_fn(task, project_path, run_id) -> result` e `apply_fn(project_path,
    run_id)` sono iniettati: in produzione avvolgono orchestrator/change_manifest,
    nei test sono stub.
    """
    def executor(goal: Goal, project_root: Path, ctx: StepContext) -> StepOutcome:
        run_id = run_id_factory()
        result = run_scaffold_fn(task=goal.objective, project_path=str(project_root), run_id=run_id)
        return outcome_from_scaffold_result(goal, Path(project_root), run_id, result or {}, apply_fn)

    return executor


# --- cablaggio di produzione (non unit-testato: richiede modelli/rig) ---------

def build_orchestrator_scaffold_runner(config_path: str, *, sse_callback=None) -> RunScaffoldFn:
    """Ritorna un run_scaffold_fn che costruisce l'Orchestrator e lancia lo scaffold.

    Ogni chiamata apre e chiude un Orchestrator (context manager), come fa oggi
    runs_core. Da usare sul rig: qui non e' testato perche' carica i modelli.
    """
    from devin.core.orchestrator import Orchestrator

    def run_scaffold_fn(task: str, project_path: str, run_id: str) -> dict:
        with Orchestrator(config_path=config_path, project_path=project_path, sse_callback=sse_callback) as orch:
            return orch.run_scaffold(task=task, project_path=project_path, run_id=run_id)

    return run_scaffold_fn


def default_apply_fn() -> ApplyFn:
    """apply_fn di produzione: applica il change manifest verificato su disco."""
    from devin.core.change_manifest import apply_change_manifest

    def apply_fn(project_path: str, run_id: str):
        return apply_change_manifest(project_path, run_id)

    return apply_fn
