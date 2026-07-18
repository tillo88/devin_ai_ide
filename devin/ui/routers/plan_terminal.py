"""Router plan_terminal: plan tracking + terminal output (2 stub inclusi).

Nono router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici
(`TerminalRequest` resta definito anche se il suo handler usa query params —
move verbatim, niente refactoring durante lo spostamento).

`active_runs` / `runs_lock` / `LOG_DIR` RESTANO in fast_app (stato run-core
single-owner) risolti con lazy import a call time. Nessun test chiama questi
handler: nessuno shim.
"""

from fastapi import APIRouter
from pydantic import BaseModel

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
    """Get terminal output for a run (from log file)."""
    from devin.ui.fast_app import LOG_DIR  # lazy: costante condivisa
    if not run_id:
        return {"error": "run_id required"}

    try:
        log_file = LOG_DIR / f"{run_id}.log"
        if not log_file.exists():
            return {"error": "log file not found", "output": ""}

        # Read last N lines
        output_lines = []
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            output_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "run_id": run_id,
            "output": "".join(output_lines),
            "total_lines": len(all_lines),
            "lines_returned": len(output_lines)
        }
    except Exception as e:
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
