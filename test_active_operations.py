from __future__ import annotations

import pytest

from devin.ui import fast_app
from devin.ui.routers import goal, runs_read, training


@pytest.fixture(autouse=True)
def clean_operation_registries():
    with fast_app.runs_lock:
        fast_app.starting_runs.clear()
        fast_app.active_runs.clear()
    with training._training_jobs_lock:
        training._training_jobs.clear()
    with goal._lock:
        goal._goal_runs.clear()
    yield
    with fast_app.runs_lock:
        fast_app.starting_runs.clear()
        fast_app.active_runs.clear()
    with training._training_jobs_lock:
        training._training_jobs.clear()
    with goal._lock:
        goal._goal_runs.clear()


def test_active_operations_unifica_run_training_e_goal():
    with fast_app.runs_lock:
        fast_app.starting_runs.clear()
        fast_app.active_runs.clear()
        fast_app.starting_runs.add("run_starting")
        fast_app.active_runs["run_running"] = object()
    with training._training_jobs_lock:
        training._training_jobs.clear()
        training._training_jobs.update({
            "training_active": {
                "job_id": "training_active", "status": "queued",
                "created_at": "2026-08-22T00:00:00",
            },
            "training_done": {"job_id": "training_done", "status": "completed"},
        })
    with goal._lock:
        goal._goal_runs.clear()
        goal._goal_runs.update({
            "goal_active": {
                "goal_run_id": "goal_active", "status": "running",
                "started_at": "2026-08-22T00:00:00", "updated_at": "2026-08-22T00:00:01",
            },
            "goal_done": {"goal_run_id": "goal_done", "status": "success"},
        })

    snapshot = runs_read.active_operations_snapshot()

    assert snapshot["schema"] == "devin_active_operations_v1"
    assert snapshot["busy"] is True
    assert snapshot["counts"] == {"goal": 1, "run": 2, "training": 1}
    assert {(item["kind"], item["operation_id"]) for item in snapshot["operations"]} == {
        ("goal", "goal_active"),
        ("run", "run_running"),
        ("run", "run_starting"),
        ("training", "training_active"),
    }


def test_active_operations_vuoto_non_e_busy():
    with fast_app.runs_lock:
        fast_app.starting_runs.clear()
        fast_app.active_runs.clear()
    with training._training_jobs_lock:
        training._training_jobs.clear()
    with goal._lock:
        goal._goal_runs.clear()

    snapshot = runs_read.active_operations_snapshot()

    assert snapshot == {
        "schema": "devin_active_operations_v1",
        "busy": False,
        "operations": [],
        "counts": {},
    }
