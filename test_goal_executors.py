"""Test dell'esecutore Scaffolder con orchestrator/apply STUB (offline).

Verifica la mappatura run_scaffold->StepOutcome e l'auto-apply condizionato dalla
politica di approvazione (D4), senza modelli.
"""

from __future__ import annotations

from pathlib import Path

from devin.core.goal_mode import (
    APPROVAL_AUTO,
    APPROVAL_MANUAL,
    MODE_MAINTENANCE,
    MODE_SCAFFOLD,
    Criterion,
    Goal,
)
from devin.core.goal_executors import (
    TESTER_TASK,
    outcome_from_run_result,
    outcome_from_scaffold_result,
    scaffolder_executor,
    tester_executor as make_tester_executor,
)
from devin.core.goal_runner import (
    RESULT_NEEDS_APPROVAL,
    RESULT_SUCCESS,
    STEP_CHANGED,
    STEP_FAILED,
    StepContext,
    run_goal,
)


def _goal(mode=MODE_SCAFFOLD, approval=APPROVAL_MANUAL, path="main.py") -> Goal:
    return Goal(
        objective="scaffolda", acceptance=[Criterion("file_exists", {"path": path})],
        mode=mode, approval_policy=approval,
    )


def _ctx() -> StepContext:
    return StepContext(pending=[], attempt_index=0, history=[])


# --- mappatura del risultato ---------------------------------------------

def test_awaiting_approval_scaffold_auto_applica(tmp_path: Path):
    applied = []
    goal = _goal(mode=MODE_SCAFFOLD)
    out = outcome_from_scaffold_result(
        goal, tmp_path, "run_1",
        {"status": "awaiting_approval"},
        apply_fn=lambda p, r: applied.append((p, r)),
    )
    assert out.status == STEP_CHANGED
    assert out.produced_changes is True
    assert applied == [(str(tmp_path), "run_1")]  # auto-apply avvenuto


def test_awaiting_approval_maintenance_manuale_non_applica(tmp_path: Path):
    applied = []
    goal = _goal(mode=MODE_MAINTENANCE, approval=APPROVAL_MANUAL)
    out = outcome_from_scaffold_result(
        goal, tmp_path, "run_1",
        {"status": "awaiting_approval"},
        apply_fn=lambda p, r: applied.append((p, r)),
    )
    assert out.status == STEP_CHANGED
    assert out.produced_changes is True
    assert applied == []  # niente auto-apply: aspetta l'umano


def test_awaiting_approval_maintenance_auto_applica(tmp_path: Path):
    applied = []
    goal = _goal(mode=MODE_MAINTENANCE, approval=APPROVAL_AUTO)
    outcome_from_scaffold_result(
        goal, tmp_path, "run_1", {"status": "awaiting_approval"},
        apply_fn=lambda p, r: applied.append((p, r)),
    )
    assert applied == [(str(tmp_path), "run_1")]


def test_apply_fallito_diventa_failed(tmp_path: Path):
    def boom(p, r):
        raise RuntimeError("disco pieno")

    out = outcome_from_scaffold_result(
        _goal(mode=MODE_SCAFFOLD), tmp_path, "run_1",
        {"status": "awaiting_approval"}, apply_fn=boom,
    )
    assert out.status == STEP_FAILED
    assert "apply-error" in out.failure_signature


def test_success_verificato(tmp_path: Path):
    out = outcome_from_scaffold_result(
        _goal(), tmp_path, "run_1",
        {"status": "verified_success", "success": True, "files_written": ["a.py"]},
        apply_fn=None,
    )
    assert out.status == STEP_CHANGED
    assert out.produced_changes is True


def test_fallito_ha_firma_stabile(tmp_path: Path):
    r = {"success": False, "error": "SyntaxError: invalid syntax"}
    a = outcome_from_scaffold_result(_goal(), tmp_path, "run_1", r, None)
    b = outcome_from_scaffold_result(_goal(), tmp_path, "run_2", r, None)
    assert a.status == STEP_FAILED
    assert a.failure_signature == b.failure_signature  # stesso errore -> stessa firma


# --- integrazione col loop (stub run_scaffold + apply) --------------------

def test_loop_scaffold_completo(tmp_path: Path):
    # 1o step: awaiting_approval, l'apply scrive il file -> criterio soddisfatto.
    goal = _goal(mode=MODE_SCAFFOLD, path="main.py")

    def fake_run_scaffold(task, project_path, run_id):
        return {"status": "awaiting_approval"}

    def fake_apply(project_path, run_id):
        (Path(project_path) / "main.py").write_text("print('ok')\n", encoding="utf-8")

    executor = scaffolder_executor(fake_run_scaffold, apply_fn=fake_apply,
                                   run_id_factory=lambda: "run_x")
    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS
    assert (tmp_path / "main.py").exists()
    assert len(res.attempts) == 1


# --- ruolo Tester ---------------------------------------------------------

def test_tester_prompt_contiene_obiettivo():
    prompt = TESTER_TASK.format(objective="una funzione is_prime")
    assert "is_prime" in prompt
    assert "ROMPERLO" in prompt  # e' adversariale, non confermativo


def test_tester_executor_etichetta_strategy(tmp_path: Path):
    applied = []
    goal = _goal(mode=MODE_SCAFFOLD, path="test_x.py")

    def fake_run_tester(task, project_path, run_id):
        return {"status": "awaiting_approval"}

    def fake_apply(p, r):
        applied.append((p, r))

    executor = make_tester_executor(fake_run_tester, apply_fn=fake_apply, run_id_factory=lambda: "run_t")
    out = executor(goal, tmp_path, _ctx())
    assert out.status == STEP_CHANGED
    assert out.strategy == "tester"
    assert applied == [(str(tmp_path), "run_t")]


def test_outcome_from_run_result_strategy_param(tmp_path: Path):
    out = outcome_from_run_result(
        _goal(), tmp_path, "run_1",
        {"success": False, "error": "boom"}, None, strategy="tester",
    )
    assert out.status == STEP_FAILED
    assert out.strategy == "tester"


def test_loop_tester_valida_con_test_generati(tmp_path: Path):
    # Il Tester "scrive" un test file (via apply) che soddisfa file_exists.
    goal = _goal(mode=MODE_SCAFFOLD, path="test_hardened.py")

    def fake_run_tester(task, project_path, run_id):
        return {"status": "awaiting_approval"}

    def fake_apply(project_path, run_id):
        (Path(project_path) / "test_hardened.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    executor = make_tester_executor(fake_run_tester, apply_fn=fake_apply, run_id_factory=lambda: "run_t")
    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_SUCCESS
    assert res.attempts[0].strategy == "tester"


def test_loop_maintenance_manuale_va_in_attesa(tmp_path: Path):
    goal = _goal(mode=MODE_MAINTENANCE, approval=APPROVAL_MANUAL, path="main.py")

    def fake_run_scaffold(task, project_path, run_id):
        return {"status": "awaiting_approval"}

    # apply non deve essere chiamato in manuale: se lo fosse, scriverebbe il file.
    def fake_apply(project_path, run_id):
        (Path(project_path) / "main.py").write_text("x\n", encoding="utf-8")

    executor = scaffolder_executor(fake_run_scaffold, apply_fn=fake_apply,
                                   run_id_factory=lambda: "run_x")
    res = run_goal(goal, tmp_path, executor)
    assert res.status == RESULT_NEEDS_APPROVAL
    assert not (tmp_path / "main.py").exists()  # non applicato
