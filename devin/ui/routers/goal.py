"""Router Goal Mode: avvia ed espone i goal-run nel backend sempre attivo.

La Goal Mode gira DENTRO il servizio (non da CLI): questo router costruisce un
`Goal`, lo lancia in un thread di background con il ruolo Scaffolder collegato
all'orchestrator, e tiene lo stato in memoria per il polling.

Dipendenze pesanti (Orchestrator, CONFIG_PATH) risolte con lazy import a call
time, come gli altri router. La funzione di esecuzione accetta l'esecutore
INIETTATO, cosi' e' testabile offline con uno stub.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from devin.core.goal_mode import Goal, GoalError, parse_acceptance
from devin.core.goal_runner import Attempt, run_goal

router = APIRouter()

# Store in memoria dei goal-run (id -> record). Semplice come active_runs.
_goal_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class GoalRunRequest(BaseModel):
    project_path: str
    objective: str = ""
    acceptance: list = []          # lista di {type, params} oppure stringhe DSL
    mode: str = "scaffold"
    approval_policy: str = "auto"
    budget_steps: int = 20
    budget_seconds: int = 3600
    role: str = "scaffolder"        # scaffolder | tester | swarm (build + verify)
    goal: Optional[dict] = None     # alternativa: intero goal_v1


VALID_ROLES = {"scaffolder", "tester", "swarm"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def goal_from_request(req: GoalRunRequest) -> Goal:
    """Costruisce e valida un Goal dalla richiesta (solleva GoalError se invalido)."""
    if req.goal:
        goal = Goal.from_dict(req.goal)
    else:
        goal = Goal(
            objective=req.objective,
            acceptance=parse_acceptance(req.acceptance),
            mode=req.mode,
            approval_policy=req.approval_policy,
            budget_steps=req.budget_steps,
            budget_seconds=req.budget_seconds,
        )
    goal.validate()
    return goal


def _attempt_record(attempt: Attempt) -> dict[str, Any]:
    return {
        "index": attempt.index,
        "strategy": attempt.strategy,
        "status": attempt.status,
        "detail": attempt.detail,
        "satisfied": attempt.evaluation.get("satisfied"),
    }


def execute_goal_run(goal_run_id: str, goal: Goal, project_path: str, executor, verifier=None) -> None:
    """Esegue il loop e aggiorna il record in memoria. Sincrona: il chiamante la
    mette su thread. `executor` (e opzionale `verifier`) iniettati -> testabile
    con stub."""
    rec = _goal_runs[goal_run_id]

    def on_attempt(attempt: Attempt) -> None:
        with _lock:
            rec["attempts"].append(_attempt_record(attempt))

    try:
        result = run_goal(goal, project_path, executor, verifier=verifier, on_attempt=on_attempt)
        with _lock:
            rec["status"] = result.status
            rec["reason"] = result.reason
            rec["result"] = result.to_dict()
            rec["finished_at"] = _now()
    except Exception as exc:  # difensivo: il thread non deve morire in silenzio
        with _lock:
            rec["status"] = "error"
            rec["reason"] = f"{type(exc).__name__}: {exc}"
            rec["finished_at"] = _now()


def _build_actors(role: str):
    """(executor, verifier) di PRODUZIONE per il ruolo scelto. Lazy import: carica
    l'orchestrator solo quando serve davvero.

    - scaffolder: solo build.
    - tester: solo verifica adversariale (standalone, raro).
    - swarm: DISPATCH -> scaffolder costruisce, tester fa da cancello di verifica.
    """
    from devin.ui.fast_app import CONFIG_PATH  # lazy: costante condivisa
    from devin.core.goal_executors import (
        build_orchestrator_debugger_runner,
        build_orchestrator_scaffold_runner,
        build_orchestrator_tester_runner,
        default_apply_fn,
        dispatching_executor,
        scaffolder_executor,
        tester_executor,
    )
    apply_fn = default_apply_fn()
    scaffolder = scaffolder_executor(build_orchestrator_scaffold_runner(CONFIG_PATH), apply_fn=apply_fn)
    if role == "tester":
        return tester_executor(build_orchestrator_tester_runner(CONFIG_PATH), apply_fn=apply_fn), None
    if role == "swarm":
        # DISPATCH a 3 ruoli: scaffolder costruisce / debugger ripara (scelti dalla
        # policy per stato), tester come cancello di verifica adversariale.
        debugger = debugger_executor(build_orchestrator_debugger_runner(CONFIG_PATH), apply_fn=apply_fn)
        tester = tester_executor(build_orchestrator_tester_runner(CONFIG_PATH), apply_fn=apply_fn)
        builder = dispatching_executor({"scaffolder": scaffolder, "debugger": debugger})
        return builder, tester
    return scaffolder, None


@router.post("/api/goal/run")
async def api_goal_run(req: GoalRunRequest):
    try:
        goal = goal_from_request(req)
    except (GoalError, ValueError, KeyError) as exc:
        return {"error": f"goal non valido: {exc}"}

    role = req.role if req.role in VALID_ROLES else "scaffolder"

    # Risolvi SEMPRE a path assoluto: un project_path relativo verrebbe risolto
    # rispetto alla CWD del servizio (imprevedibile) e non sapremmo dove sono
    # finiti i file. Lo store e la risposta riportano il path assoluto reale.
    from pathlib import Path
    project = str(Path(req.project_path).expanduser().resolve())
    Path(project).mkdir(parents=True, exist_ok=True)

    goal_run_id = datetime.now().strftime("goal_%Y%m%d_%H%M%S_%f")
    with _lock:
        _goal_runs[goal_run_id] = {
            "goal_run_id": goal_run_id,
            "status": "running",
            "reason": "",
            "objective": goal.objective,
            "mode": goal.mode,
            "role": role,
            "approval_policy": goal.approval_policy,
            "requires_checkpoint": goal.requires_checkpoint(),
            "project_path": project,
            "attempts": [],
            "result": None,
            "started_at": _now(),
            "finished_at": None,
        }

    try:
        executor, verifier = _build_actors(role)
    except Exception as exc:
        with _lock:
            _goal_runs[goal_run_id]["status"] = "error"
            _goal_runs[goal_run_id]["reason"] = f"avvio esecutore fallito: {exc}"
        return {"error": str(exc), "goal_run_id": goal_run_id}

    t = threading.Thread(
        target=execute_goal_run, args=(goal_run_id, goal, project, executor, verifier), daemon=True,
    )
    t.start()
    return {"goal_run_id": goal_run_id, "status": "started"}


@router.get("/api/goal/{goal_run_id}")
async def api_goal_status(goal_run_id: str):
    with _lock:
        rec = _goal_runs.get(goal_run_id)
        if not rec:
            return {"error": "goal-run non trovato", "goal_run_id": goal_run_id}
        return dict(rec)


@router.get("/api/goal")
async def api_goal_list():
    with _lock:
        return {"goal_runs": [
            {k: r[k] for k in ("goal_run_id", "status", "objective", "started_at", "finished_at")}
            for r in _goal_runs.values()
        ]}
