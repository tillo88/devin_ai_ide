"""Test degli helper del router Goal Mode (offline, esecutore stub).

Non tocca l'orchestrator: `execute_goal_run` riceve un esecutore iniettato.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devin.core.goal_mode import GoalError, MODE_SCAFFOLD
from devin.core.goal_runner import STEP_CHANGED, StepOutcome
from devin.ui.routers import goal as goal_router


def test_goal_from_request_dsl():
    req = goal_router.GoalRunRequest(
        project_path="/tmp/x", objective="fai qualcosa",
        acceptance=["tests_pass", "file_exists:main.py"], mode="scaffold",
    )
    g = goal_router.goal_from_request(req)
    assert [c.type for c in g.acceptance] == ["tests_pass", "file_exists"]
    assert g.mode == "scaffold"


def test_goal_from_request_dict_criteri():
    req = goal_router.GoalRunRequest(
        project_path="/tmp/x", objective="obj",
        acceptance=[{"type": "file_exists", "params": {"path": "a.py"}}],
    )
    g = goal_router.goal_from_request(req)
    assert g.acceptance[0].params["path"] == "a.py"


def test_goal_from_request_goal_intero():
    req = goal_router.GoalRunRequest(
        project_path="/tmp/x",
        goal={"objective": "o", "acceptance": [{"type": "tests_pass", "params": {}}], "mode": "scaffold"},
    )
    g = goal_router.goal_from_request(req)
    assert g.objective == "o"
    assert g.mode == "scaffold"


def test_goal_from_request_invalido():
    req = goal_router.GoalRunRequest(project_path="/tmp/x", objective="", acceptance=[])
    with pytest.raises(GoalError):
        goal_router.goal_from_request(req)


def test_execute_goal_run_aggiorna_lo_store(tmp_path: Path):
    # Prepara il record come fa l'endpoint, poi esegue con esecutore stub.
    from devin.core.goal_mode import Criterion, Goal
    goal = Goal(objective="o", acceptance=[Criterion("file_exists", {"path": "main.py"})], mode=MODE_SCAFFOLD)
    gid = "goal_test_1"
    goal_router._goal_runs[gid] = {
        "goal_run_id": gid, "status": "running", "reason": "", "attempts": [],
        "result": None, "started_at": "now", "finished_at": None,
    }

    def stub_executor(g, root, ctx):
        (Path(root) / "main.py").write_text("x=1\n", encoding="utf-8")
        return StepOutcome(STEP_CHANGED, strategy="scaffolder", produced_changes=True)

    goal_router.execute_goal_run(gid, goal, str(tmp_path), stub_executor)

    rec = goal_router._goal_runs[gid]
    assert rec["status"] == "success"
    assert len(rec["attempts"]) == 1
    assert rec["attempts"][0]["strategy"] == "scaffolder"
    assert rec["finished_at"] is not None


def test_execute_goal_run_con_verifier_swarm(tmp_path: Path):
    from devin.core.goal_mode import Criterion, Goal
    goal = Goal(objective="o", acceptance=[Criterion("file_exists", {"path": "code.py"})], mode=MODE_SCAFFOLD)
    gid = "goal_swarm_1"
    goal_router._goal_runs[gid] = {
        "goal_run_id": gid, "status": "running", "reason": "", "attempts": [],
        "result": None, "started_at": "now", "finished_at": None,
    }

    def builder(g, root, ctx):
        (Path(root) / "code.py").write_text("x=1\n", encoding="utf-8")
        return StepOutcome(STEP_CHANGED, strategy="scaffolder", produced_changes=True)

    def verifier(g, root, ctx):
        return StepOutcome(STEP_CHANGED, strategy="tester", produced_changes=True)  # non rompe

    goal_router.execute_goal_run(gid, goal, str(tmp_path), builder, verifier)

    rec = goal_router._goal_runs[gid]
    assert rec["status"] == "success"
    strategies = {a["strategy"] for a in rec["attempts"]}
    assert strategies == {"scaffolder", "tester"}  # dispatch: entrambi hanno agito


def test_execute_goal_run_cattura_eccezioni(tmp_path: Path):
    from devin.core.goal_mode import Criterion, Goal
    goal = Goal(objective="o", acceptance=[Criterion("file_exists", {"path": "z.py"})], mode=MODE_SCAFFOLD)
    gid = "goal_test_2"
    goal_router._goal_runs[gid] = {
        "goal_run_id": gid, "status": "running", "reason": "", "attempts": [],
        "result": None, "started_at": "now", "finished_at": None,
    }

    def boom(g, root, ctx):
        raise RuntimeError("executor rotto")

    goal_router.execute_goal_run(gid, goal, str(tmp_path), boom)
    rec = goal_router._goal_runs[gid]
    assert rec["status"] == "error"
    assert "executor rotto" in rec["reason"]
