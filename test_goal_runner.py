"""Test del loop di controllo Goal Mode (macchina a stati), con esecutore stub.

Offline: nessun modello. L'esecutore e' iniettato e simula i ruoli.
"""

from __future__ import annotations

from pathlib import Path

from devin.core.goal_mode import (
    APPROVAL_AUTO,
    MODE_MAINTENANCE,
    MODE_SCAFFOLD,
    Criterion,
    Goal,
)
from devin.core.goal_runner import (
    RESULT_BLOCKED,
    RESULT_BUDGET,
    RESULT_NEEDS_APPROVAL,
    RESULT_SUCCESS,
    STEP_BLOCKED,
    STEP_CHANGED,
    STEP_FAILED,
    STEP_NO_CHANGE,
    StepOutcome,
    run_goal,
)


def _goal(criteria, **kw) -> Goal:
    return Goal(objective="test", acceptance=criteria, **kw)


def test_gia_soddisfatto_all_avvio(tmp_path: Path):
    (tmp_path / "main.py").write_text("x=1\n", encoding="utf-8")
    goal = _goal([Criterion("file_exists", {"path": "main.py"})])
    called = []

    def executor(g, root, ctx):
        called.append(ctx.attempt_index)
        return StepOutcome(STEP_CHANGED)

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS
    assert called == []  # non deve neanche eseguire uno step


def test_successo_dopo_uno_step(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "main.py"})], mode=MODE_SCAFFOLD)

    def executor(g, root, ctx):
        (Path(root) / "main.py").write_text("x=1\n", encoding="utf-8")
        return StepOutcome(STEP_CHANGED, strategy="scaffolder", produced_changes=True)

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS
    assert len(res.attempts) == 1
    assert res.attempts[0].strategy == "scaffolder"


def test_scaffold_non_si_ferma_sulle_modifiche(tmp_path: Path):
    # scaffold: produce modifiche step dopo step senza checkpoint, fino al criterio.
    goal = _goal([Criterion("file_exists", {"path": "b.py"})], mode=MODE_SCAFFOLD, budget_steps=5)
    steps = {"n": 0}

    def executor(g, root, ctx):
        steps["n"] += 1
        if steps["n"] == 1:
            (Path(root) / "a.py").write_text("1\n", encoding="utf-8")  # non basta
        else:
            (Path(root) / "b.py").write_text("2\n", encoding="utf-8")  # soddisfa
        return StepOutcome(STEP_CHANGED, produced_changes=True)

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS
    assert steps["n"] == 2  # non si e' fermato al primo cambiamento


def test_maintenance_manuale_pausa_su_modifiche(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "z.py"})], mode=MODE_MAINTENANCE)

    def executor(g, root, ctx):
        (Path(root) / "a.py").write_text("1\n", encoding="utf-8")  # cambia ma non soddisfa
        return StepOutcome(STEP_CHANGED, produced_changes=True)

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_NEEDS_APPROVAL
    assert len(res.attempts) == 1


def test_maintenance_auto_non_pausa(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "z.py"})], mode=MODE_MAINTENANCE,
                 approval_policy=APPROVAL_AUTO, budget_steps=3)
    steps = {"n": 0}

    def executor(g, root, ctx):
        steps["n"] += 1
        name = "z.py" if steps["n"] >= 2 else "a.py"
        (Path(root) / name).write_text("1\n", encoding="utf-8")
        return StepOutcome(STEP_CHANGED, produced_changes=True)

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS  # auto-approva: non si ferma
    assert steps["n"] == 2


def test_stesso_fallimento_ripetuto_blocca(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "mai.py"})], mode=MODE_SCAFFOLD, budget_steps=10)

    def executor(g, root, ctx):
        return StepOutcome(STEP_FAILED, detail="boom", failure_signature="err-42")

    res = run_goal(goal, tmp_path, executor, max_identical_failures=3)
    assert res.status == RESULT_BLOCKED
    assert "err-42" in res.reason
    assert len(res.attempts) == 3  # si ferma alla terza occorrenza identica


def test_fallimenti_diversi_continuano_finche_budget(tmp_path: Path):
    # Fallimenti con signature diverse = "cambio strategia" (D2): non blocca per
    # ripetizione, va avanti fino a esaurire il budget di step.
    goal = _goal([Criterion("file_exists", {"path": "mai.py"})], mode=MODE_SCAFFOLD, budget_steps=4)

    def executor(g, root, ctx):
        return StepOutcome(STEP_FAILED, detail="x", failure_signature=f"sig-{ctx.attempt_index}")

    res = run_goal(goal, tmp_path, executor, max_identical_failures=3)
    assert res.status == RESULT_BUDGET
    assert len(res.attempts) == 4


def test_executor_blocked_ferma_subito(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "mai.py"})], mode=MODE_SCAFFOLD)

    def executor(g, root, ctx):
        return StepOutcome(STEP_BLOCKED, detail="non so come procedere")

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_BLOCKED
    assert len(res.attempts) == 1


def test_budget_tempo_esaurito(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "mai.py"})], mode=MODE_SCAFFOLD,
                 budget_seconds=10, budget_steps=100)
    fake = {"t": 0.0}

    def clock():
        fake["t"] += 6.0  # ogni chiamata avanza di 6s: al secondo giro supera 10s
        return fake["t"]

    def executor(g, root, ctx):
        return StepOutcome(STEP_FAILED, failure_signature=f"s{ctx.attempt_index}")

    res = run_goal(goal, tmp_path, executor, clock=clock)
    assert res.status == RESULT_BUDGET
    assert "tempo" in res.reason


def test_history_passata_all_executor(tmp_path: Path):
    goal = _goal([Criterion("file_exists", {"path": "done.py"})], mode=MODE_SCAFFOLD, budget_steps=3)
    seen_history_len = []

    def executor(g, root, ctx):
        seen_history_len.append(len(ctx.history))
        if ctx.attempt_index == 1:
            (Path(root) / "done.py").write_text("1\n", encoding="utf-8")
        return StepOutcome(STEP_NO_CHANGE) if ctx.attempt_index == 0 else StepOutcome(STEP_CHANGED, produced_changes=True)

    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS
    assert seen_history_len == [0, 1]  # la history cresce a ogni step
