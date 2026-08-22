"""Router Goal Mode: avvia ed espone i goal-run nel backend sempre attivo.

La Goal Mode gira DENTRO il servizio (non da CLI): questo router costruisce un
`Goal`, lo lancia in un thread di background con il ruolo Scaffolder collegato
all'orchestrator, e tiene lo stato in memoria per il polling.

Dipendenze pesanti (Orchestrator, CONFIG_PATH) risolte con lazy import a call
time, come gli altri router. La funzione di esecuzione accetta l'esecutore
INIETTATO, cosi' e' testabile offline con uno stub.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from devin.core.goal_mode import Goal, GoalError, parse_acceptance
from devin.core.goal_runner import Attempt, run_goal
from devin.core.time_service import timestamp_bundle

router = APIRouter()

# Store in memoria dei goal-run (id -> record). Lo snapshot attivo e' incluso
# nel registro operativo unificato usato dal frontdoor: un goal in background
# impedisce quindi il rilascio idle del backend anche senza richieste HTTP in
# corso. La persistenza/resume completa resta una fase separata di Goal Mode.
_goal_runs: dict[str, dict[str, Any]] = {}
_goal_stop_events: dict[str, threading.Event] = {}
_lock = threading.RLock()
ACTIVE_GOAL_STATUSES = frozenset({"starting", "running", "stopping"})
TERMINAL_GOAL_EVENTS = frozenset({"goal_finished", "goal_error"})
MAX_GOAL_EVENTS = 500


class GoalRunRequest(BaseModel):
    project_path: str
    objective: str = ""
    acceptance: list = Field(default_factory=list)  # {type, params} o stringhe DSL
    mode: str = "scaffold"
    approval_policy: str = "auto"
    budget_steps: int = Field(default=20, ge=1, le=100)
    budget_seconds: int = Field(default=3600, ge=1, le=28800)
    role: str = "scaffolder"        # scaffolder | tester | swarm (build + verify)
    goal: Optional[dict] = None     # alternativa: intero goal_v1


VALID_ROLES = {"scaffolder", "tester", "swarm"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_goal_event(
    goal_run_id: str,
    event_type: str,
    *,
    level: str = "info",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Aggiunge un evento bounded senza esporre path o log del progetto."""
    with _lock:
        rec = _goal_runs.get(goal_run_id)
        if rec is None:
            return None
        seq = int(rec.get("_event_seq", 0))
        rec["_event_seq"] = seq + 1
        stamp = timestamp_bundle()
        event_data = dict(data or {})
        event_data.pop("project_path", None)
        event_data.pop("work_dir", None)
        event = {
            "seq": seq,
            "ts": stamp["timestamp_utc"],
            "timestamp_utc": stamp["timestamp_utc"],
            "timestamp_local": stamp["timestamp_local"],
            "display_timezone": stamp["display_timezone"],
            "goal_run_id": goal_run_id,
            "type": str(event_type),
            "level": str(level),
            "message": str(message),
            "data": event_data,
        }
        events = rec.setdefault("events", [])
        events.append(event)
        if len(events) > MAX_GOAL_EVENTS:
            del events[:-MAX_GOAL_EVENTS]
        return dict(event)


def goal_operations_snapshot() -> list[dict[str, Any]]:
    """Ritorna soltanto i goal che possiedono ancora lavoro in background.

    La forma e' intenzionalmente piccola e stabile: viene aggregata da
    ``/api/operations/active`` senza esporre prompt, risultati o altri dati del
    progetto al control plane.
    """
    with _lock:
        return [
            {
                "operation_id": str(record["goal_run_id"]),
                "kind": "goal",
                "status": str(record["status"]),
                "started_at": record.get("started_at"),
                "updated_at": record.get("updated_at"),
            }
            for record in _goal_runs.values()
            if record.get("status") in ACTIVE_GOAL_STATUSES
        ]


def _resolve_goal_project_path(project_path: str) -> str:
    """Applica lo stesso gate e lo stesso routing ``work_dir`` dei run normali."""
    from devin.core.project_space import ProjectSpace
    from devin.ui.fast_app import _validated_project_path

    project = _validated_project_path(project_path, allow_general=False)
    work_dir = ProjectSpace(project).get_work_dir()
    if work_dir:
        project = _validated_project_path(work_dir, allow_general=False)
    return project


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
    if goal.budget_steps > 100:
        raise GoalError("budget_steps supera il massimo di 100")
    if goal.budget_seconds > 28800:
        raise GoalError("budget_seconds supera il massimo di 28800")
    return goal


def _attempt_record(attempt: Attempt) -> dict[str, Any]:
    return {
        "index": attempt.index,
        "strategy": attempt.strategy,
        "status": attempt.status,
        "detail": attempt.detail,
        "satisfied": attempt.evaluation.get("satisfied"),
        "evaluation": dict(attempt.evaluation),
    }


def _goal_panel_record(record: dict[str, Any]) -> dict[str, Any]:
    """Bounded UI projection for the read-only Goal panel."""
    return {
        key: record.get(key)
        for key in (
            "goal_run_id",
            "status",
            "objective",
            "mode",
            "role",
            "approval_policy",
            "requires_checkpoint",
            "acceptance",
            "budget_steps",
            "budget_seconds",
            "attempts",
            "evaluation",
            "reason",
            "started_at",
            "updated_at",
            "finished_at",
        )
    }


def execute_goal_run(goal_run_id: str, goal: Goal, project_path: str, executor, verifier=None) -> None:
    """Esegue il loop e aggiorna il record in memoria. Sincrona: il chiamante la
    mette su thread. `executor` (e opzionale `verifier`) iniettati -> testabile
    con stub."""
    rec = _goal_runs[goal_run_id]
    stop_event = _goal_stop_events.get(goal_run_id)

    def on_attempt(attempt: Attempt) -> None:
        attempt_record = _attempt_record(attempt)
        with _lock:
            rec["attempts"].append(attempt_record)
            rec["evaluation"] = dict(attempt.evaluation)
            rec["updated_at"] = _now()
        _append_goal_event(
            goal_run_id,
            "goal_attempt",
            message=f"Step {attempt.index + 1}: {attempt.strategy or 'executor'}",
            level="warning" if attempt.status == "failed" else "info",
            data=attempt_record,
        )

    try:
        result = run_goal(
            goal,
            project_path,
            executor,
            verifier=verifier,
            on_attempt=on_attempt,
            should_stop=stop_event.is_set if stop_event is not None else None,
        )
        with _lock:
            rec["status"] = result.status
            rec["reason"] = result.reason
            rec["result"] = result.to_dict()
            rec["evaluation"] = dict(result.evaluation)
            rec["finished_at"] = _now()
            rec["updated_at"] = rec["finished_at"]
        _append_goal_event(
            goal_run_id,
            "goal_finished",
            message=f"Goal concluso: {result.status}",
            level="info" if result.status == "success" else "warning",
            data={
                "status": result.status,
                "reason": result.reason,
                "evaluation": dict(result.evaluation),
            },
        )
    except Exception as exc:  # difensivo: il thread non deve morire in silenzio
        with _lock:
            rec["status"] = "error"
            rec["reason"] = f"{type(exc).__name__}: {exc}"
            rec["finished_at"] = _now()
            rec["updated_at"] = rec["finished_at"]
        _append_goal_event(
            goal_run_id,
            "goal_error",
            level="error",
            message="Goal terminato con errore",
            data={"status": "error", "reason": rec["reason"]},
        )
    finally:
        with _lock:
            _goal_stop_events.pop(goal_run_id, None)


def _build_actors(role: str, config_path: str | None = None, auto_apply: bool = False):
    """(executor, verifier) di PRODUZIONE per il ruolo scelto. Lazy import: carica
    l'orchestrator solo quando serve davvero. `config_path` iniettabile per i test.
    `auto_apply` (goal auto/scaffold): i ruoli scrivono DIRETTAMENTE nel goal
    workspace (legacy_auto_apply) cosi' anche l'output non verificato atterra e il
    Debugger puo' iterarci (completa D4). In maintenance/manuale resta 'review'.

    - scaffolder: solo build.
    - tester: solo verifica adversariale (standalone, raro).
    - swarm: DISPATCH -> scaffolder costruisce + debugger ripara, tester = cancello.
    """
    if config_path is None:
        from devin.ui.fast_app import CONFIG_PATH  # lazy: costante condivisa
        config_path = CONFIG_PATH
    from devin.core.goal_executors import (
        build_orchestrator_debugger_runner,
        build_orchestrator_scaffold_runner,
        build_orchestrator_tester_runner,
        debugger_executor,
        default_apply_fn,
        dispatching_executor,
        scaffolder_executor,
        tester_executor,
    )
    apply_fn = default_apply_fn()
    cp = config_path
    scaffolder = scaffolder_executor(
        build_orchestrator_scaffold_runner(cp, auto_apply=auto_apply), apply_fn=apply_fn)
    if role == "tester":
        return tester_executor(
            build_orchestrator_tester_runner(cp, auto_apply=auto_apply), apply_fn=apply_fn), None
    if role == "swarm":
        # DISPATCH a 3 ruoli: scaffolder costruisce / debugger ripara (scelti dalla
        # policy per stato), tester come cancello di verifica adversariale.
        debugger = debugger_executor(
            build_orchestrator_debugger_runner(cp, auto_apply=auto_apply), apply_fn=apply_fn)
        tester = tester_executor(
            build_orchestrator_tester_runner(cp, auto_apply=auto_apply), apply_fn=apply_fn)
        builder = dispatching_executor({"scaffolder": scaffolder, "debugger": debugger})
        return builder, tester
    return scaffolder, None


@router.post("/api/goal/run")
async def api_goal_run(req: GoalRunRequest):
    try:
        goal = goal_from_request(req)
    except (GoalError, ValueError, KeyError) as exc:
        return {"error": f"goal non valido: {exc}"}

    with _lock:
        active = next(
            (record for record in _goal_runs.values() if record.get("status") in ACTIVE_GOAL_STATUSES),
            None,
        )
        if active:
            return {
                "error": "un goal-run e' gia' in esecuzione",
                "goal_run_id": active.get("goal_run_id"),
                "status": active.get("status"),
            }

    role = req.role if req.role in VALID_ROLES else "scaffolder"

    # Stesso allowlist gate dei run/scaffold e stesso routing verso l'eventuale
    # linked work_dir. In precedenza Goal Mode accettava e creava qualunque path
    # assoluto, aggirando il contratto di progetto del resto del backend.
    project = _resolve_goal_project_path(req.project_path)
    Path(project).mkdir(parents=True, exist_ok=True)

    goal_run_id = datetime.now().strftime("goal_%Y%m%d_%H%M%S_%f")
    started_at = _now()
    with _lock:
        _goal_stop_events[goal_run_id] = threading.Event()
        _goal_runs[goal_run_id] = {
            "goal_run_id": goal_run_id,
            "status": "starting",
            "reason": "",
            "objective": goal.objective,
            "mode": goal.mode,
            "role": role,
            "approval_policy": goal.approval_policy,
            "requires_checkpoint": goal.requires_checkpoint(),
            "acceptance": [criterion.to_dict() for criterion in goal.acceptance],
            "budget_steps": goal.budget_steps,
            "budget_seconds": goal.budget_seconds,
            "project_path": project,
            "attempts": [],
            "evaluation": {},
            "events": [],
            "_event_seq": 0,
            "result": None,
            "started_at": started_at,
            "updated_at": started_at,
            "finished_at": None,
        }

    try:
        # goal auto (scaffold o approval=auto) -> i ruoli applicano direttamente nel
        # goal workspace, cosi' anche l'output non verificato atterra (D4).
        executor, verifier = _build_actors(role, auto_apply=not goal.requires_checkpoint())
    except Exception as exc:
        with _lock:
            _goal_stop_events.pop(goal_run_id, None)
            _goal_runs[goal_run_id]["status"] = "error"
            _goal_runs[goal_run_id]["reason"] = f"avvio esecutore fallito: {exc}"
            _goal_runs[goal_run_id]["finished_at"] = _now()
            _goal_runs[goal_run_id]["updated_at"] = _goal_runs[goal_run_id]["finished_at"]
        _append_goal_event(
            goal_run_id,
            "goal_error",
            level="error",
            message="Avvio esecutore fallito",
            data={"status": "error", "reason": str(exc)},
        )
        return {"error": str(exc), "goal_run_id": goal_run_id}

    t = threading.Thread(
        target=execute_goal_run, args=(goal_run_id, goal, project, executor, verifier), daemon=True,
    )
    try:
        with _lock:
            _goal_runs[goal_run_id]["status"] = "running"
            _goal_runs[goal_run_id]["updated_at"] = _now()
        _append_goal_event(
            goal_run_id,
            "goal_started",
            message="Goal avviato",
            data={
                "status": "running",
                "mode": goal.mode,
                "role": role,
                "budget_steps": goal.budget_steps,
                "budget_seconds": goal.budget_seconds,
                "criteria": len(goal.acceptance),
            },
        )
        t.start()
    except Exception as exc:
        with _lock:
            _goal_stop_events.pop(goal_run_id, None)
            _goal_runs[goal_run_id]["status"] = "error"
            _goal_runs[goal_run_id]["reason"] = f"thread goal non avviato: {exc}"
            _goal_runs[goal_run_id]["finished_at"] = _now()
            _goal_runs[goal_run_id]["updated_at"] = _goal_runs[goal_run_id]["finished_at"]
        _append_goal_event(
            goal_run_id,
            "goal_error",
            level="error",
            message="Thread Goal non avviato",
            data={"status": "error", "reason": str(exc)},
        )
        return {"error": str(exc), "goal_run_id": goal_run_id}
    return {"goal_run_id": goal_run_id, "status": "started"}


@router.post("/api/goal/{goal_run_id}/stop")
async def api_goal_stop(goal_run_id: str):
    """Richiede uno stop cooperativo, effettivo alla fine dello step corrente."""
    with _lock:
        rec = _goal_runs.get(goal_run_id)
        if not rec:
            return {"error": "goal-run non trovato", "goal_run_id": goal_run_id}
        if rec.get("status") == "stopped":
            return {
                "goal_run_id": goal_run_id,
                "status": "stopped",
                "reason": rec.get("reason", ""),
            }
        if rec.get("status") not in ACTIVE_GOAL_STATUSES:
            return {
                "error": f"goal-run non arrestabile nello stato {rec.get('status')}",
                "goal_run_id": goal_run_id,
                "status": rec.get("status"),
            }
        stop_event = _goal_stop_events.get(goal_run_id)
        if stop_event is None:
            return {"error": "controllo stop non disponibile", "goal_run_id": goal_run_id}
        stop_event.set()
        rec["status"] = "stopping"
        rec["reason"] = "stop richiesto; attendo la fine dello step corrente"
        rec["updated_at"] = _now()
        _append_goal_event(
            goal_run_id,
            "goal_stop_requested",
            level="warning",
            message="Stop richiesto; attendo la fine dello step corrente",
            data={"status": "stopping"},
        )
        return {
            "goal_run_id": goal_run_id,
            "status": rec["status"],
            "reason": rec["reason"],
        }


@router.get("/api/goal/{goal_run_id}/events")
async def api_goal_events(goal_run_id: str, after_seq: int | None = None, limit: int = 500):
    with _lock:
        rec = _goal_runs.get(goal_run_id)
        if rec is None:
            return {"error": "goal-run non trovato", "goal_run_id": goal_run_id}
        safe_limit = max(1, min(int(limit), MAX_GOAL_EVENTS))
        events = [
            dict(event)
            for event in rec.get("events", [])
            if after_seq is None or int(event.get("seq", -1)) > after_seq
        ][:safe_limit]
        return {"goal_run_id": goal_run_id, "events": events}


@router.get("/api/goal/{goal_run_id}/events/stream")
async def api_goal_events_stream(goal_run_id: str, after_seq: int | None = None):
    with _lock:
        if goal_run_id not in _goal_runs:
            return JSONResponse(
                {"error": "goal-run non trovato", "goal_run_id": goal_run_id},
                status_code=404,
            )

    async def generate():
        last_seq = after_seq if after_seq is not None else -1
        idle_polls = 0
        while True:
            with _lock:
                rec = _goal_runs.get(goal_run_id)
                if rec is None:
                    return
                events = [
                    dict(event)
                    for event in rec.get("events", [])
                    if int(event.get("seq", -1)) > last_seq
                ][:100]
                alive = rec.get("status") in ACTIVE_GOAL_STATUSES
            for event in events:
                last_seq = int(event.get("seq", last_seq))
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event.get("type") in TERMINAL_GOAL_EVENTS:
                    return
            if not alive:
                yield "event: done\ndata: " + json.dumps(
                    {"goal_run_id": goal_run_id, "last_seq": last_seq}
                ) + "\n\n"
                return
            idle_polls += 1
            if idle_polls % 50 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        return {"goal_runs": [_goal_panel_record(record) for record in _goal_runs.values()]}
