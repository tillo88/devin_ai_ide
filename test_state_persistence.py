#!/usr/bin/env python3
"""
Regression tests for devin/core/state_persistence.py.

Covers the 2026-07-18 resume-hijack fix: a fresh run on a project with an
interrupted state must NOT silently inherit the old run's task/plan/attempt.
Resume is allowed only when the caller explicitly passes the interrupted
run_id. Also covers cleanup() (previously never called) and the read-only
load_latest() used by status endpoints.
"""
import os
import time

from devin.core.state_persistence import StatePersistence


def _interrupted_state(task="old task", attempt=1, max_retries=3):
    return {
        "task": task,
        "attempt": attempt,
        "last_error": "boom",
        "last_patch": "diff --git ...",
        "plan": {"steps": ["step1", "step2"], "raw_response": "raw"},
        "context_length": 42,
        "max_retries": max_retries,
        "model_source": "local",
    }


def test_fresh_run_does_not_hijack_interrupted_state(tmp_path):
    """A new run (fresh run_id) must not resume an old interrupted state."""
    old = StatePersistence(str(tmp_path), "run_20000101_000000")
    old.save(_interrupted_state())

    fresh = StatePersistence(str(tmp_path), "run_20990101_000000")
    assert fresh.get_resume_info() is None
    # And the instance identity must not have been rewritten to the old run.
    assert fresh.run_id == "run_20990101_000000"
    assert fresh.state_file.name == "run_20990101_000000.json"


def test_explicit_resume_with_interrupted_run_id(tmp_path):
    """Passing the interrupted run_id resumes that exact run."""
    old = StatePersistence(str(tmp_path), "run_20000101_000000")
    old.save(_interrupted_state(task="fix the bug", attempt=1))

    resume = StatePersistence(str(tmp_path), "run_20000101_000000")
    info = resume.get_resume_info()
    assert info is not None
    assert info["run_id"] == "run_20000101_000000"
    assert info["task"] == "fix the bug"
    assert info["attempt"] == 1
    assert info["plan"]["steps"] == ["step1", "step2"]
    assert info["can_resume"] is True


def test_completed_run_not_resumable(tmp_path):
    for status in ("success", "failed", "timeout", "stopped"):
        sp = StatePersistence(str(tmp_path), f"run_2000010{len(status)}_000000")
        state = _interrupted_state()
        state["final_status"] = status
        sp.save(state)
        assert sp.get_resume_info() is None, f"{status} must not be resumable"


def test_exhausted_attempts_report_cannot_resume(tmp_path):
    sp = StatePersistence(str(tmp_path), "run_20000101_000000")
    sp.save(_interrupted_state(attempt=3, max_retries=3))
    info = sp.get_resume_info()
    assert info is not None
    assert info["can_resume"] is False


def test_cleanup_removes_only_stale_states(tmp_path):
    old = StatePersistence(str(tmp_path), "run_20000101_000000")
    old.save(_interrupted_state())
    recent = StatePersistence(str(tmp_path), "run_20990101_000000")
    recent.save(_interrupted_state())

    # Backdate the old state file beyond the 24h cutoff.
    stale_ts = time.time() - 25 * 3600
    os.utime(old.state_file, (stale_ts, stale_ts))

    removed = recent.cleanup(max_age_hours=24)
    assert removed == 1
    assert not old.state_file.exists()
    assert recent.state_file.exists()


def test_cleanup_preserves_pending_approval_and_rollback_state(tmp_path):
    pending = StatePersistence(str(tmp_path), "run_pending_review")
    pending_state = _interrupted_state()
    pending_state["final_status"] = "awaiting_approval"
    pending.save(pending_state)
    applied = StatePersistence(str(tmp_path), "run_applied_review")
    applied_state = _interrupted_state()
    applied_state.update({
        "final_status": "success",
        "change_manifest_status": "applied",
    })
    applied.save(applied_state)
    stale_ts = time.time() - 25 * 3600
    os.utime(pending.state_file, (stale_ts, stale_ts))
    os.utime(applied.state_file, (stale_ts, stale_ts))

    removed = pending.cleanup(max_age_hours=24)

    assert removed == 0
    assert pending.state_file.exists()
    assert applied.state_file.exists()


def test_load_latest_remains_available_for_readonly_endpoints(tmp_path):
    """Status endpoints need 'latest state for project' regardless of run_id."""
    first = StatePersistence(str(tmp_path), "run_20000101_000000")
    first.save(_interrupted_state(task="first"))
    # Ensure a strictly newer mtime for the second file.
    time.sleep(0.02)
    second = StatePersistence(str(tmp_path), "run_20000101_000001")
    second.save(_interrupted_state(task="second"))

    reader = StatePersistence(str(tmp_path), "run_20990101_000000")
    latest = reader.load_latest()
    assert latest is not None
    assert latest["task"] == "second"


# ============================================================
# /api/run/resume (2026-07-18): resume ESPLICITO di run interrotti
# ============================================================

def _patch_resume_surface(monkeypatch, fast_app, tmp_path):
    """Neutralizza path validation, workspace routing, log/event I/O."""
    monkeypatch.setattr(
        fast_app, "_validated_project_path",
        lambda p, allow_general=False: p)
    monkeypatch.setattr(
        fast_app, "ProjectSpace",
        lambda p: type("PS", (), {"get_work_dir": lambda self: None})())
    monkeypatch.setattr(fast_app, "LOG_DIR", tmp_path)
    fake_events = type("Events", (), {
        "append": lambda self, *a, **k: {},
        "start": lambda self, *a, **k: {},
        "finish": lambda self, *a, **k: {},
        "append_log": lambda self, *a, **k: {},
    })()
    monkeypatch.setattr(fast_app, "_run_events", fake_events)


def test_resume_endpoint_rejects_unsafe_run_id(tmp_path, monkeypatch):
    import asyncio
    import devin.ui.fast_app as fast_app

    _patch_resume_surface(monkeypatch, fast_app, tmp_path)
    req = fast_app.ResumeRequest(path=str(tmp_path), run_id="../evil")
    result = asyncio.run(fast_app.api_run_resume(req))
    assert "unsafe" in result["error"]


def test_resume_endpoint_rejects_missing_or_finished_state(tmp_path, monkeypatch):
    import asyncio
    import devin.ui.fast_app as fast_app

    _patch_resume_surface(monkeypatch, fast_app, tmp_path)

    # Nessuno stato salvato
    req = fast_app.ResumeRequest(path=str(tmp_path), run_id="run_20000101_000000")
    result = asyncio.run(fast_app.api_run_resume(req))
    assert "nothing resumable" in result["error"]

    # Stato completato: non riprendibile
    sp = StatePersistence(str(tmp_path), "run_20000101_000001")
    state = _interrupted_state()
    state["final_status"] = "success"
    sp.save(state)
    req = fast_app.ResumeRequest(path=str(tmp_path), run_id="run_20000101_000001")
    result = asyncio.run(fast_app.api_run_resume(req))
    assert "nothing resumable" in result["error"]

    # Stato con retry esauriti
    sp = StatePersistence(str(tmp_path), "run_20000101_000002")
    sp.save(_interrupted_state(attempt=3, max_retries=3))
    req = fast_app.ResumeRequest(path=str(tmp_path), run_id="run_20000101_000002")
    result = asyncio.run(fast_app.api_run_resume(req))
    assert "exhausted" in result["error"]


def test_resume_endpoint_relaunches_interrupted_run(tmp_path, monkeypatch):
    import asyncio
    import threading
    import devin.ui.fast_app as fast_app

    _patch_resume_surface(monkeypatch, fast_app, tmp_path)

    sp = StatePersistence(str(tmp_path), "run_20000101_000003")
    sp.save(_interrupted_state(task="fix the real bug", attempt=1))

    launched = {}
    done = threading.Event()

    class FakeOrch:
        def __init__(self, **kwargs):
            launched["init"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run(self, **kwargs):
            launched["run_kwargs"] = kwargs
            done.set()
            return {"status": "success"}

    monkeypatch.setattr(fast_app, "Orchestrator", FakeOrch)

    req = fast_app.ResumeRequest(path=str(tmp_path), run_id="run_20000101_000003")
    result = asyncio.run(fast_app.api_run_resume(req))

    assert result["status"] == "resumed"
    assert result["run_id"] == "run_20000101_000003"
    assert done.wait(timeout=10), "il run ripreso non e' partito"
    assert launched["run_kwargs"]["run_id"] == "run_20000101_000003"
    assert launched["run_kwargs"]["task"] == "fix the real bug"


def _pending_change(project, run_id, *, after="after\n"):
    import shutil
    from devin.core.change_manifest import build_change_manifest

    (project / "value.txt").write_text("before\n", encoding="utf-8")
    sandbox = project / "workspace" / "sandboxes" / run_id
    sandbox.mkdir(parents=True)
    shutil.copy2(project / "value.txt", sandbox / "value.txt")
    (sandbox / "value.txt").write_text(after, encoding="utf-8")
    build_change_manifest(project, sandbox, run_id)
    state = _interrupted_state(task="approved edit")
    state.update({
        "final_status": "awaiting_approval",
        "verified": True,
        "applied": False,
    })
    StatePersistence(str(project), run_id).save(state)


def test_change_decision_endpoints_apply_then_rollback(tmp_path, monkeypatch):
    import asyncio
    import devin.ui.fast_app as fast_app

    project = tmp_path / "project"
    project.mkdir()
    _patch_resume_surface(monkeypatch, fast_app, tmp_path)
    _pending_change(project, "run_decision_apply")

    apply_req = fast_app.ChangeDecisionRequest(
        path=str(project), run_id="run_decision_apply", commit=False)
    preview = asyncio.run(fast_app.api_run_changes_preview(
        "run_decision_apply", path=str(project)))
    assert preview["status"] == "pending"
    assert "+after" in preview["unified_diff"]
    applied = asyncio.run(fast_app.api_run_changes_apply(apply_req))

    assert applied["status"] == "success"
    assert applied["applied"] is True
    assert (project / "value.txt").read_text(encoding="utf-8") == "after\n"
    state = StatePersistence(str(project), "run_decision_apply").load()
    assert state["final_status"] == "success"
    assert state["applied"] is True

    rollback_req = fast_app.ChangeDecisionRequest(
        path=str(project), run_id="run_decision_apply", commit=False)
    rolled_back = asyncio.run(fast_app.api_run_changes_rollback(rollback_req))

    assert rolled_back["status"] == "rolled_back"
    assert (project / "value.txt").read_text(encoding="utf-8") == "before\n"


def test_change_decision_endpoint_rejects_without_writing(tmp_path, monkeypatch):
    import asyncio
    import devin.ui.fast_app as fast_app

    project = tmp_path / "project"
    project.mkdir()
    _patch_resume_surface(monkeypatch, fast_app, tmp_path)
    _pending_change(project, "run_decision_reject")

    req = fast_app.ChangeDecisionRequest(
        path=str(project), run_id="run_decision_reject", commit=False)
    rejected = asyncio.run(fast_app.api_run_changes_reject(req))

    assert rejected["status"] == "rejected"
    assert rejected["applied"] is False
    assert (project / "value.txt").read_text(encoding="utf-8") == "before\n"
    state = StatePersistence(str(project), "run_decision_reject").load()
    assert state["final_status"] == "rejected"


# ============================================================
# /api/project/last_run: riconciliazione con active_runs (2026-07-18)
# ============================================================

def test_last_run_reports_live_run_as_running_not_resumable(tmp_path):
    """Un run VIVO salva stati intermedi senza final_status: il badge non deve
    mostrarlo come 'interrotto' ne' offrire Riprendi mentre sta girando."""
    import asyncio
    import devin.ui.fast_app as fast_app

    sp = StatePersistence(str(tmp_path), "run_live_1")
    sp.save(_interrupted_state(attempt=1))

    fast_app.active_runs["run_live_1"] = object()
    try:
        result = asyncio.run(fast_app.api_project_last_run(project_path=str(tmp_path)))
    finally:
        fast_app.active_runs.pop("run_live_1", None)

    assert result["status"] == "running"
    assert result["running"] is True
    assert result["resumable"] is False


def test_last_run_marks_crashed_run_resumable_within_retry_budget(tmp_path):
    """Stesso stato, processo riavviato (active_runs vuoto): e' un crash
    rilevato -> 'interrotto' e riprendibile se i retry non sono esauriti."""
    import asyncio
    import devin.ui.fast_app as fast_app

    sp = StatePersistence(str(tmp_path), "run_dead_1")
    sp.save(_interrupted_state(attempt=1, max_retries=3))

    result = asyncio.run(fast_app.api_project_last_run(project_path=str(tmp_path)))
    assert result["status"] == "interrotto"
    assert result["running"] is False
    assert result["resumable"] is True

    # Retry esauriti: interrotto ma NON riprendibile (come il resume endpoint)
    sp2 = StatePersistence(str(tmp_path), "run_dead_2")
    sp2.save(_interrupted_state(attempt=3, max_retries=3))
    time.sleep(0.02)  # mtime piu' recente -> load_latest legge questo
    result = asyncio.run(fast_app.api_project_last_run(project_path=str(tmp_path)))
    assert result["run_id"] == "run_dead_2"
    assert result["resumable"] is False
