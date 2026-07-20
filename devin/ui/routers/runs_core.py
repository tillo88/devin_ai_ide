"""Router runs_core: nucleo MUTANTE dei run (run, resume, scaffold, stop).

Tredicesimo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md) — fetta B del runs core. Move puro: path e
comportamento identici.

TUTTE le dipendenze RESTANO in fast_app e sono risolte con lazy import a
CALL TIME (dentro gli handler E dentro le closure `_bg`, che girano su
thread dopo la risposta): `Orchestrator`, `ProjectSpace`,
`_validated_project_path`, `LOG_DIR`, `_run_events`, `CONFIG_PATH`,
`active_runs`/`runs_lock`, `_make_run_callback`, `_finish_run_events`,
`_scaffold_event_status`. Cosi' i test che monkeypatchano
`fast_app.Orchestrator`/`LOG_DIR`/`_run_events`/... continuano a valere
(test_state_persistence). `safe_run_id` arriva diretto da
devin.core.run_events.

fast_app re-esporta handler + request model (shim): i test usano
`fast_app.ResumeRequest`/`fast_app.api_run_resume` e `/api/chat` chiama
`api_chat_scaffold(RunRequest(...))` direttamente.
"""

import threading
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from devin.core.run_events import safe_run_id

router = APIRouter()


class RunRequest(BaseModel):
    path: str
    task: str = "trova e correggi eventuali bug"
    entrypoint: Optional[str] = None
    max_attempts: int = 3
    max_seconds: int = 300


@router.post("/api/run")
async def api_run(req: RunRequest):
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        LOG_DIR,
        ProjectSpace,
        _run_events,
        _validated_project_path,
    )
    if not req.path:
        return {"error": "missing path"}
    req.path = _validated_project_path(req.path, allow_general=False)
    # Epic "Progetti come Claude" (2026-07-16): se il progetto ha una CARTELLA
    # DI LAVORO collegata, il run lavora li' — chat/knowledge/istruzioni
    # restano nel progetto workspace. La sicurezza non cambia: l'orchestrator
    # esegue comunque nella sua sandbox e la cartella deve essere in allowlist.
    _work_dir = ProjectSpace(req.path).get_work_dir()
    if _work_dir:
        req.path = _validated_project_path(_work_dir, allow_general=False)
        print(f"[WORKDIR] run instradato sulla cartella di lavoro: {req.path}")

    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    # Crea il file di log SUBITO (come /api/chat/scaffold): l'inizializzazione
    # dell'Orchestrator (launcher + health-check) puo' richiedere secondi, e
    # /stream/{run_id} si arrende dopo ~10s se non trova il file -> "Waiting for
    # log file..." all'infinito. Scriverlo qui garantisce che lo stream lo trovi.
    log_path_init = LOG_DIR / f"{run_id}.log"
    log_path_init.write_text(f"Run started: {run_id}\nTask: {req.task}\n", encoding="utf-8")
    _run_events.start(run_id, mode="maintenance", task=req.task, project_path=req.path)

    def _bg():
        from devin.ui.fast_app import (  # lazy: risolti a thread-run time
            CONFIG_PATH,
            LOG_DIR,
            Orchestrator,
            _finish_run_events,
            _make_run_callback,
            active_runs,
            runs_lock,
        )
        try:
            log_path = LOG_DIR / f"{run_id}.log"
            sse_callback = _make_run_callback(run_id, log_path)

            with Orchestrator(
                config_path=CONFIG_PATH,
                project_path=req.path,
                sse_callback=sse_callback
            ) as orch:
                with runs_lock:
                    active_runs[run_id] = orch
                try:
                    result = orch.run(
                        task=req.task,
                        project_path=req.path,
                        entrypoint=req.entrypoint,
                        max_attempts=req.max_attempts,
                        max_seconds=req.max_seconds,
                        run_id=run_id
                    )
                    _finish_run_events(run_id, result.get("status", "failed"), mode="maintenance")
                    # FIX: niente piu' scrittura qui — orchestrator.run() scrive GIA'
                    # il footer 'status: X' internamente (in ogni return path, vedi
                    # write_status_footer() in orchestrator.py). Scriverlo anche qui
                    # duplicava la riga.
                finally:
                    with runs_lock:
                        active_runs.pop(run_id, None)
        except Exception as e:
            log_path = LOG_DIR / f"{run_id}.log"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[FATAL] {e}\n")
                f.write("status: failed\n")
            _finish_run_events(run_id, "failed", mode="maintenance")

    t = threading.Thread(target=_bg, daemon=True)
    t.start()

    return {"run_id": run_id, "status": "started"}


class ResumeRequest(BaseModel):
    path: str
    run_id: str
    max_attempts: int = 3
    max_seconds: int = 300


class ChangeDecisionRequest(BaseModel):
    path: str
    run_id: str
    commit: bool = True


def _decision_project_path(path: str) -> str:
    from devin.ui.fast_app import ProjectSpace, _validated_project_path

    resolved = _validated_project_path(path, allow_general=False)
    work_dir = ProjectSpace(resolved).get_work_dir()
    if work_dir:
        resolved = _validated_project_path(work_dir, allow_general=False)
    return resolved


def _decision_state(req: ChangeDecisionRequest, expected_status):
    from devin.core.state_persistence import StatePersistence
    from devin.ui.fast_app import active_runs, runs_lock

    safe_run_id(req.run_id)
    req.path = _decision_project_path(req.path)
    with runs_lock:
        if req.run_id in active_runs:
            raise ValueError("run is still active")
    persistence = StatePersistence(req.path, req.run_id)
    state = persistence.load()
    expected = ((expected_status,) if isinstance(expected_status, str)
                else tuple(expected_status))
    if not state or state.get("final_status") not in expected:
        actual = state.get("final_status") if state else "missing"
        raise ValueError(f"run state is {actual}, expected one of {expected}")
    return persistence, state


def _record_decision(req: ChangeDecisionRequest, status: str, message: str) -> None:
    from devin.ui.fast_app import LOG_DIR, _run_events

    log_path = LOG_DIR / f"{safe_run_id(req.run_id)}.log"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{message}\nstatus: {status}\n")
    _run_events.append(
        req.run_id, f"changes_{status}",
        level="info" if status == "success" else "warning",
        message=message,
        data={"status": status},
    )


@router.post("/api/run/changes/apply")
async def api_run_changes_apply(req: ChangeDecisionRequest):
    """Apply one explicitly approved, already-verified sandbox manifest."""
    from devin.core.change_manifest import apply_change_manifest
    from devin.engine.git_ops import GitOps
    try:
        persistence, state = _decision_state(req, "awaiting_approval")
        manifest = apply_change_manifest(req.path, req.run_id)
        commit_result = None
        if req.commit:
            commit_result = GitOps(req.path).commit(
                state.get("last_patch", ""), state.get("task", "approved change")
            )
        final_status = "success"
        if commit_result is not None and not commit_result.get("success"):
            final_status = "applied_uncommitted"
        state.update({
            "final_status": final_status,
            "step": "changes_applied",
            "verified": True,
            "applied": True,
            "change_manifest_status": manifest["status"],
            "commit_result": commit_result,
        })
        persistence.save(state)
        _record_decision(
            req, final_status, "verified changes applied by user approval"
        )
        return {
            "run_id": req.run_id,
            "status": final_status,
            "applied": True,
            "commit": commit_result,
            "counts": manifest["counts"],
        }
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc), "run_id": req.run_id}


@router.get("/api/run/changes/{run_id}")
async def api_run_changes_preview(run_id: str, path: str = ""):
    """Return the bounded explicit diff that must be reviewed before approval."""
    from devin.core.change_manifest import preview_change_manifest

    try:
        req = ChangeDecisionRequest(path=path, run_id=run_id, commit=False)
        _decision_state(req, "awaiting_approval")
        return preview_change_manifest(req.path, req.run_id)
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc), "run_id": run_id}


@router.post("/api/run/changes/reject")
async def api_run_changes_reject(req: ChangeDecisionRequest):
    from devin.core.change_manifest import reject_change_manifest
    try:
        persistence, state = _decision_state(req, "awaiting_approval")
        manifest = reject_change_manifest(req.path, req.run_id)
        state.update({
            "final_status": "rejected",
            "step": "changes_rejected",
            "verified": True,
            "applied": False,
            "change_manifest_status": manifest["status"],
        })
        persistence.save(state)
        _record_decision(req, "rejected", "verified changes rejected by user")
        return {"run_id": req.run_id, "status": "rejected", "applied": False}
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc), "run_id": req.run_id}


@router.post("/api/run/changes/rollback")
async def api_run_changes_rollback(req: ChangeDecisionRequest):
    from devin.core.change_manifest import rollback_change_manifest
    try:
        persistence, state = _decision_state(req, ("success", "applied_uncommitted"))
        manifest = rollback_change_manifest(req.path, req.run_id)
        state.update({
            "final_status": "rolled_back",
            "step": "changes_rolled_back",
            "applied": False,
            "change_manifest_status": manifest["status"],
        })
        persistence.save(state)
        _record_decision(req, "rolled_back", "approved changes rolled back")
        return {"run_id": req.run_id, "status": "rolled_back", "applied": False}
    except (ValueError, RuntimeError) as exc:
        return {"error": str(exc), "run_id": req.run_id}


@router.post("/api/run/resume")
async def api_run_resume(req: ResumeRequest):
    """Riprende un run di mantenimento INTERROTTO (crash/restart del backend).

    Chiusura del cerchio del fix resume-hijack (2026-07-18): il resume non e'
    piu' implicito (un run nuovo non eredita MAI lo stato di uno vecchio) —
    avviene solo qui, su richiesta esplicita dell'utente, riusando lo STESSO
    run_id: log e timeline continuano sullo stesso file e l'orchestratore
    riparte da attempt/piano/last_error salvati in .devin_state."""
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        LOG_DIR,
        ProjectSpace,
        _run_events,
        _validated_project_path,
        active_runs,
        runs_lock,
    )
    from devin.core.state_persistence import StatePersistence

    if not req.path or not req.run_id:
        return {"error": "missing path or run_id"}
    try:
        safe_run_id(req.run_id)
    except ValueError:
        return {"error": "unsafe run_id"}
    req.path = _validated_project_path(req.path, allow_general=False)
    _work_dir = ProjectSpace(req.path).get_work_dir()
    if _work_dir:
        req.path = _validated_project_path(_work_dir, allow_general=False)

    with runs_lock:
        if req.run_id in active_runs:
            return {"error": "run already active"}

    sp = StatePersistence(req.path, req.run_id)
    resume_info = sp.get_resume_info()
    if not resume_info:
        return {"error": "nothing resumable for this run_id (missing state or already finished)"}
    if not resume_info.get("can_resume"):
        return {"error": "run exhausted its retries; start a fresh run instead"}

    run_id = req.run_id
    log_path = LOG_DIR / f"{run_id}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\nRun resumed by user: {run_id}\n")
    _run_events.append(
        run_id, "run_resumed", level="info",
        message=f"run resumed by user (from attempt {resume_info.get('attempt', 0)})",
        data={"mode": "maintenance", "resumed": True},
    )

    def _bg():
        from devin.ui.fast_app import (  # lazy: risolti a thread-run time
            CONFIG_PATH,
            Orchestrator,
            _finish_run_events,
            _make_run_callback,
            active_runs,
            runs_lock,
        )
        try:
            sse_callback = _make_run_callback(run_id, log_path)
            with Orchestrator(
                config_path=CONFIG_PATH,
                project_path=req.path,
                sse_callback=sse_callback
            ) as orch:
                with runs_lock:
                    active_runs[run_id] = orch
                try:
                    result = orch.run(
                        task=resume_info.get("task") or "",
                        project_path=req.path,
                        max_attempts=req.max_attempts,
                        max_seconds=req.max_seconds,
                        run_id=run_id
                    )
                    _finish_run_events(run_id, result.get("status", "failed"), mode="maintenance")
                finally:
                    with runs_lock:
                        active_runs.pop(run_id, None)
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[FATAL] {e}\n")
                f.write("status: failed\n")
            _finish_run_events(run_id, "failed", mode="maintenance")

    t = threading.Thread(target=_bg, daemon=True)
    t.start()

    return {"run_id": run_id, "status": "resumed", "attempt": resume_info.get("attempt", 0)}


@router.post("/api/chat/scaffold")
async def api_chat_scaffold(req: RunRequest):
    """
    Avvia la creazione di un progetto da zero, esclusivamente via tool (no diff pipeline).
    Il frontend fa subito subscribe a /stream/{run_id}: nessun tempo morto silenzioso,
    ogni file creato emette un evento SSE (regola Chat First).
    """
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        LOG_DIR,
        _make_run_callback,
        _run_events,
        _validated_project_path,
    )
    if not req.path:
        return {"error": "missing path"}
    req.path = _validated_project_path(req.path, allow_general=False)

    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
    log_path = LOG_DIR / f"{run_id}.log"
    log_path.write_text(f"Scaffold started: {run_id}\nTask: {req.task}\n", encoding="utf-8")
    _run_events.start(run_id, mode="scaffold", task=req.task, project_path=req.path)

    sse_callback = _make_run_callback(run_id, log_path)

    def _bg():
        from devin.ui.fast_app import (  # lazy: risolti a thread-run time
            CONFIG_PATH,
            Orchestrator,
            _finish_run_events,
            _scaffold_event_status,
            active_runs,
            runs_lock,
        )
        try:
            with Orchestrator(
                config_path=CONFIG_PATH,
                project_path=req.path,
                sse_callback=sse_callback
            ) as orch:
                with runs_lock:
                    active_runs[run_id] = orch
                try:
                    result = orch.run_scaffold(task=req.task, project_path=req.path, run_id=run_id)
                    scaffold_status = _scaffold_event_status(result)
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\nevidence: {scaffold_status}\n")
                        f.write(f"status: {'success' if result.get('success') else 'failed'}\n")
                    _finish_run_events(run_id, scaffold_status, mode='scaffold')
                finally:
                    with runs_lock:
                        active_runs.pop(run_id, None)
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[FATAL] {e}\n")
                f.write("status: failed\n")
            _finish_run_events(run_id, "failed", mode="scaffold")

    t = threading.Thread(target=_bg, daemon=True)
    t.start()

    return {"run_id": run_id, "status": "started", "mode": "scaffold"}


@router.post("/api/stop")
async def api_stop(request: Request):
    from devin.ui.fast_app import active_runs, runs_lock  # lazy: run-core
    data = await request.json()
    run_id = data.get("run_id")
    if not run_id:
        return {"error": "missing run_id"}

    with runs_lock:
        orch = active_runs.get(run_id)

    if orch:
        orch.stop()
        return {"status": "stop_requested", "run_id": run_id}
    return {"error": "run not found or already finished"}
