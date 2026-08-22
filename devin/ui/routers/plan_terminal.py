"""Router plan_terminal: plan tracking + bounded terminal output (2 stub inclusi).

Nono router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). `TerminalRequest` resta definito anche se il suo
handler usa query params; C3.3 ha poi reso la lettura del log contenuta e
bounded senza aggiungere input interattivo.

`active_runs` / `runs_lock` / `LOG_DIR` RESTANO in fast_app (stato run-core
single-owner) risolti con lazy import a call time. Il tail endpoint è coperto
direttamente, inclusi containment, cap e symlink escape.
"""

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from devin.core.run_events import safe_run_id

router = APIRouter()


@router.get("/api/plan/current")
async def api_plan_current(run_id: str = ""):
    """Get current plan steps for an active or recent run."""
    from devin.ui.fast_app import LOG_DIR, active_runs, runs_lock  # lazy: run-core
    if not run_id:
        return {"error": "run_id required"}

    # Try to get from active runs first
    with runs_lock:
        if run_id in active_runs:
            run_data = active_runs[run_id]
            return {
                "run_id": run_id,
                "status": "running",
                "plan": run_data.get("plan", []),
                "current_step": run_data.get("current_step", 0),
                "total_steps": len(run_data.get("plan", []))
            }

    # Fallback to persisted state
    try:
        from devin.core.state_persistence import StatePersistence
        log_file = LOG_DIR / f"{run_id}.log"
        if log_file.exists():
            project_path = log_file.parent.parent  # Approximate
            sp = StatePersistence(str(project_path), run_id)
            resume_info = sp.get_resume_info()
            if resume_info:
                saved_plan = resume_info.get("plan", {})
                steps = saved_plan.get("steps", [])
                return {
                    "run_id": run_id,
                    "status": "paused" if resume_info.get("can_resume") else "completed",
                    "plan": steps,
                    "current_step": resume_info.get("attempt", 0),
                    "total_steps": len(steps),
                    "task": resume_info.get("task", "")[:500]
                }
    except Exception as e:
        return {"error": f"failed to load plan state: {e}"}

    return {"error": "run not found"}


class PlanStepRequest(BaseModel):
    run_id: str
    step_index: int


@router.post("/api/plan/step/skip")
async def api_plan_step_skip(req: PlanStepRequest):
    """Skip a specific plan step (marks as completed without execution)."""
    # This is a placeholder - actual implementation would require
    # orchestrator to support step-by-step execution with skip capability
    return {
        "success": False,
        "error": "step skip not yet implemented - requires orchestrator refactoring for step-by-step execution"
    }


class TerminalRequest(BaseModel):
    run_id: str


@router.get("/api/terminal/output")
async def api_terminal_output(run_id: str = "", lines: int = 100):
    """Return a contained, bounded tail of one run log.

    This remains read-only.  The central cockpit treats structured run events
    as the source of truth for faults and uses this payload only as technical
    context.  Reading from the tail avoids loading an arbitrarily large log in
    memory just to render its last lines.
    """
    from devin.ui.fast_app import LOG_DIR  # lazy: costante condivisa
    if not run_id:
        return {"error": "run_id required"}

    try:
        checked_run_id = safe_run_id(run_id)
        log_root = Path(LOG_DIR).resolve()
        log_file = (log_root / f"{checked_run_id}.log").resolve()
        if log_root not in log_file.parents or not log_file.is_file():
            return {"error": "log file not found", "output": ""}

        safe_lines = max(1, min(int(lines), 1000))
        max_tail_bytes = 512_000
        file_size = log_file.stat().st_size
        start_offset = max(0, file_size - max_tail_bytes)
        with log_file.open("rb") as handle:
            handle.seek(start_offset)
            raw = handle.read(max_tail_bytes)

        text = raw.decode("utf-8", errors="replace")
        partial_prefix = start_offset > 0
        if partial_prefix:
            newline = text.find("\n")
            text = text[newline + 1:] if newline >= 0 else ""
        available_lines = text.splitlines(keepends=True)
        output_lines = available_lines[-safe_lines:]
        truncated = partial_prefix or len(available_lines) > len(output_lines)

        return {
            "schema": "devin_terminal_tail_v2",
            "run_id": checked_run_id,
            "output": "".join(output_lines),
            "lines_returned": len(output_lines),
            "lines_available_in_tail": len(available_lines),
            "tail_bytes": len(raw),
            "file_size": file_size,
            "truncated": truncated,
        }
    except (TypeError, ValueError):
        return {"error": "invalid run_id or lines", "output": ""}
    except OSError as e:
        return {"error": f"failed to read terminal output: {e}", "output": ""}


class TerminalInputRequest(BaseModel):
    run_id: str
    input: str


@router.post("/api/terminal/input")
async def api_terminal_input(req: TerminalInputRequest):
    """Send input to running terminal (placeholder - requires terminal process tracking)."""
    # This is a placeholder - actual implementation would require
    # the runner to track terminal processes and support stdin injection
    return {
        "success": False,
        "error": "terminal input not yet implemented - requires runner refactoring for process tracking and stdin injection"
    }
