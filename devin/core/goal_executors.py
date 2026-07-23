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


def outcome_from_run_result(
    goal: Goal,
    project_root: Path,
    run_id: str,
    result: dict,
    apply_fn: ApplyFn | None,
    *,
    strategy: str,
) -> StepOutcome:
    """Mappa il risultato di un run (scaffold o maintenance) in uno StepOutcome.

    Logica comune a tutti i ruoli; `strategy` etichetta il ruolo che ha agito.

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
                    STEP_FAILED, strategy=strategy,
                    detail=f"apply manifest fallito: {type(exc).__name__}: {exc}",
                    failure_signature=f"apply-error:{type(exc).__name__}",
                )
            return StepOutcome(
                STEP_CHANGED, strategy=strategy, produced_changes=True,
                detail=f"manifest applicato (run {run_id})",
            )
        # Checkpoint umano richiesto: lascia il manifest in attesa.
        return StepOutcome(
            STEP_CHANGED, strategy=strategy, produced_changes=True,
            detail=f"manifest in attesa di approvazione (run {run_id})",
        )

    if result.get("success"):
        return StepOutcome(
            STEP_CHANGED, strategy=strategy,
            produced_changes=bool(result.get("files_written")),
            detail=str(status or "ok"),
        )

    return StepOutcome(
        STEP_FAILED, strategy=strategy,
        detail=(result.get("error") or "run fallito")[:200],
        failure_signature=failure_signature(result),
    )


def outcome_from_scaffold_result(
    goal: Goal, project_root: Path, run_id: str, result: dict, apply_fn: ApplyFn | None,
) -> StepOutcome:
    """Compat: mapping per il ruolo Scaffolder."""
    return outcome_from_run_result(goal, project_root, run_id, result, apply_fn, strategy="scaffolder")


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


# --- Ruolo Tester: verificatore adversariale --------------------------------

# Il Tester non "conferma" il codice: cerca di ROMPERLO. Questo prompt e' il suo
# cervello. Va al modello avvolgendo l'obiettivo originale del progetto.
TESTER_TASK = (
    "Agisci come VERIFICATORE ADVERSARIALE del progetto. Il tuo compito NON e' "
    "confermare che il codice funziona, ma cercare di ROMPERLO con test rigorosi.\n"
    "Obiettivo originale del progetto: {objective}\n\n"
    "Scrivi (o rafforza) una suite di test che:\n"
    "- copra casi limite e valori di confine, input vuoti/nulli/zero/negativi;\n"
    "- includa casi che distinguono un'implementazione CORRETTA da una plausibile "
    "ma SBAGLIATA (esempi per una primalita': 1, 2, 9, 25, quadrati di primi, "
    "primi grandi, numeri pari; adatta il ragionamento al dominio reale del "
    "progetto);\n"
    "- abbia assert chiari e specifici, un test per caso, nomi parlanti;\n"
    "- passi SOLO se l'implementazione e' davvero corretta.\n\n"
    "NON modificare la logica di produzione: scrivi soltanto test. Se i test "
    "rivelano un bug, lasciali fallire: e' esattamente il segnale che serve."
)


def tester_executor(
    run_tester_fn: RunScaffoldFn,
    *,
    apply_fn: ApplyFn | None = None,
    run_id_factory: Callable[[], str] = _new_run_id,
) -> StepExecutor:
    """Costruisce lo StepExecutor del ruolo Tester.

    `run_tester_fn(task, project_path, run_id) -> result` in produzione avvolge
    `orchestrator.run` (manutenzione) col prompt adversariale; nei test e' uno
    stub. Il mapping del risultato e' comune agli altri ruoli.
    """
    def executor(goal: Goal, project_root: Path, ctx: StepContext) -> StepOutcome:
        run_id = run_id_factory()
        result = run_tester_fn(task=goal.objective, project_path=str(project_root), run_id=run_id)
        return outcome_from_run_result(goal, Path(project_root), run_id, result or {}, apply_fn, strategy="tester")

    return executor


# --- Ruolo Debugger: recovery strutturato --------------------------------

# Il Debugger interviene quando la struttura c'e' ma qualcosa NON passa (build/
# test rossi). Non riscrive tutto e non indebolisce i test: diagnosi ordinata +
# fix minimo. Complementare al Critic INTERNO dell'orchestrator (che self-heala
# dentro una run): il Debugger e' il fixer di livello superiore.
DEBUGGER_TASK = (
    "Il progetto ha build o test ROSSI. Agisci come DEBUGGER con metodo, non a "
    "tentativi.\n"
    "Obiettivo originale del progetto: {objective}\n\n"
    "Procedura:\n"
    "1. Formula ipotesi sulla causa, ordinate per probabilita'.\n"
    "2. Individua la riproduzione minima del fallimento.\n"
    "3. Isola la causa radice (non i sintomi).\n"
    "4. Applica il fix piu' PICCOLO e mirato al codice di PRODUZIONE.\n\n"
    "Vincoli assoluti: NON riscrivere tutto; NON indebolire, cancellare o rendere "
    "banali i test per farli passare; NON mascherare l'errore. Se il fix corretto "
    "richiede piu' passi, fai solo il prossimo passo verificabile."
)


def debugger_executor(
    run_debugger_fn: RunScaffoldFn,
    *,
    apply_fn: ApplyFn | None = None,
    run_id_factory: Callable[[], str] = _new_run_id,
) -> StepExecutor:
    """Costruisce lo StepExecutor del ruolo Debugger (fixer di livello superiore)."""
    def executor(goal: Goal, project_root: Path, ctx: StepContext) -> StepOutcome:
        run_id = run_id_factory()
        result = run_debugger_fn(task=goal.objective, project_path=str(project_root), run_id=run_id)
        return outcome_from_run_result(goal, Path(project_root), run_id, result or {}, apply_fn, strategy="debugger")

    return executor


# --- Dispatcher: sceglie il costruttore giusto per lo stato corrente ---------

def default_build_policy(goal: Goal, project_root: Path, ctx: StepContext) -> str:
    """Policy deterministica del DISPATCH builder:
    - criteri strutturali non soddisfatti (file mancanti/contenuto) -> scaffolder;
    - struttura presente ma qualcosa non passa (tests/command) -> debugger.
    """
    pending_types = {r.criterion.type for r in ctx.pending}
    if pending_types & {"file_exists", "contains_text"}:
        return "scaffolder"
    return "debugger"


def dispatching_executor(
    builders: dict[str, StepExecutor],
    *,
    policy: Callable[[Goal, Path, StepContext], str] = default_build_policy,
) -> StepExecutor:
    """StepExecutor che sceglie il ruolo builder via `policy` e vi delega.
    `builders` es. {"scaffolder": ..., "debugger": ...}. La strategy nell'outcome
    riflette gia' il ruolo che ha agito (impostata dai sotto-executor)."""
    def executor(goal: Goal, project_root: Path, ctx: StepContext) -> StepOutcome:
        role = policy(goal, project_root, ctx)
        sub = builders.get(role) or next(iter(builders.values()))
        return sub(goal, project_root, ctx)

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


def build_orchestrator_tester_runner(config_path: str, *, sse_callback=None) -> RunScaffoldFn:
    """run_tester_fn di produzione: usa orchestrator.run (manutenzione) col prompt
    adversariale del Tester per rafforzare i test di un progetto esistente.

    Da usare sul rig: qui non e' testato perche' carica i modelli.
    """
    from devin.core.orchestrator import Orchestrator

    def run_tester_fn(task: str, project_path: str, run_id: str) -> dict:
        full_task = TESTER_TASK.format(objective=task)
        with Orchestrator(config_path=config_path, project_path=project_path, sse_callback=sse_callback) as orch:
            return orch.run(task=full_task, project_path=project_path, run_id=run_id)

    return run_tester_fn


def build_orchestrator_debugger_runner(config_path: str, *, sse_callback=None) -> RunScaffoldFn:
    """run_debugger_fn di produzione: usa orchestrator.run (manutenzione) col prompt
    del Debugger per diagnosi + fix minimo su un progetto rosso. Da usare sul rig."""
    from devin.core.orchestrator import Orchestrator

    def run_debugger_fn(task: str, project_path: str, run_id: str) -> dict:
        full_task = DEBUGGER_TASK.format(objective=task)
        with Orchestrator(config_path=config_path, project_path=project_path, sse_callback=sse_callback) as orch:
            return orch.run(task=full_task, project_path=project_path, run_id=run_id)

    return run_debugger_fn


def default_apply_fn() -> ApplyFn:
    """apply_fn di produzione: applica il change manifest verificato su disco."""
    from devin.core.change_manifest import apply_change_manifest

    def apply_fn(project_path: str, run_id: str):
        return apply_change_manifest(project_path, run_id)

    return apply_fn
