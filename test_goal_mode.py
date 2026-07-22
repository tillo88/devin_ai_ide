"""Test della fondazione Goal Mode (Fase 1): oggetto Goal + valutatore checklist.

Offline: solo filesystem + subprocess su progetti temporanei. Nessun modello.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from devin.core.goal_mode import (
    APPROVAL_AUTO,
    APPROVAL_MANUAL,
    MODE_MAINTENANCE,
    MODE_SCAFFOLD,
    Criterion,
    Goal,
    GoalError,
    evaluate_goal,
)


def _goal(criteria, **kw) -> Goal:
    return Goal(objective="obiettivo di test", acceptance=criteria, **kw)


# --- validazione ---------------------------------------------------------

def test_goal_senza_criteri_e_invalido():
    with pytest.raises(GoalError):
        _goal([]).validate()


def test_objective_vuoto_e_invalido():
    with pytest.raises(GoalError):
        Goal(objective="  ", acceptance=[Criterion("file_exists", {"path": "x"})]).validate()


def test_criterio_tipo_sconosciuto():
    with pytest.raises(GoalError):
        _goal([Criterion("boh", {})]).validate()


def test_criterio_file_exists_senza_path():
    with pytest.raises(GoalError):
        _goal([Criterion("file_exists", {})]).validate()


# --- criteri su file (read-only) -----------------------------------------

def test_file_exists_pass_e_fail(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    ev = evaluate_goal(_goal([Criterion("file_exists", {"path": "main.py"})]), tmp_path)
    assert ev.satisfied
    ev2 = evaluate_goal(_goal([Criterion("file_exists", {"path": "nope.py"})]), tmp_path)
    assert not ev2.satisfied


def test_contains_text(tmp_path: Path):
    (tmp_path / "readme.md").write_text("# Titolo\ncontenuto\n", encoding="utf-8")
    ok = evaluate_goal(_goal([Criterion("contains_text", {"path": "readme.md", "text": "Titolo"})]), tmp_path)
    assert ok.satisfied
    ko = evaluate_goal(_goal([Criterion("contains_text", {"path": "readme.md", "text": "assente"})]), tmp_path)
    assert not ko.satisfied


def test_absence_of_pattern_trova_todo(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1  # TODO: fixami\n", encoding="utf-8")
    ev = evaluate_goal(_goal([Criterion("absence_of_pattern", {"pattern": r"TODO|FIXME"})]), tmp_path)
    assert not ev.satisfied
    assert "a.py" in ev.results[0].detail


def test_absence_of_pattern_pulito(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    ev = evaluate_goal(_goal([Criterion("absence_of_pattern", {"pattern": r"TODO|FIXME"})]), tmp_path)
    assert ev.satisfied


def test_absence_of_pattern_salta_dir_escluse(tmp_path: Path):
    node = tmp_path / "node_modules"
    node.mkdir()
    (node / "junk.py").write_text("# TODO ignora questo\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("ok = True\n", encoding="utf-8")
    ev = evaluate_goal(_goal([Criterion("absence_of_pattern", {"pattern": r"TODO"})]), tmp_path)
    assert ev.satisfied  # node_modules non deve essere scansionato


# --- criteri che eseguono processi ---------------------------------------

def test_command_succeeds(tmp_path: Path):
    ok = evaluate_goal(_goal([Criterion("command_succeeds", {"argv": [sys.executable, "-c", "import sys; sys.exit(0)"]})]), tmp_path)
    assert ok.satisfied
    ko = evaluate_goal(_goal([Criterion("command_succeeds", {"argv": [sys.executable, "-c", "import sys; sys.exit(3)"]})]), tmp_path)
    assert not ko.satisfied


def test_execute_false_salta_i_comandi(tmp_path: Path):
    ev = evaluate_goal(
        _goal([Criterion("command_succeeds", {"argv": [sys.executable, "-c", "pass"]})]),
        tmp_path,
        execute=False,
    )
    assert not ev.satisfied
    assert "execute=False" in ev.results[0].detail


def test_tests_pass_verde_e_rosso(tmp_path: Path):
    green = tmp_path / "green"
    green.mkdir()
    (green / "test_ok.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    ev_green = evaluate_goal(_goal([Criterion("tests_pass", {"timeout": 60})]), green)

    red = tmp_path / "red"
    red.mkdir()
    (red / "test_ko.py").write_text("def test_ko():\n    assert 1 == 2\n", encoding="utf-8")
    ev_red = evaluate_goal(_goal([Criterion("tests_pass", {"timeout": 60})]), red)

    # pytest e' installato nell'ambiente dei test: il verde passa, il rosso no.
    assert ev_green.satisfied
    assert not ev_red.satisfied


# --- aggregazione e pending ----------------------------------------------

def test_satisfied_richiede_tutti_i_criteri(tmp_path: Path):
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    goal = _goal([
        Criterion("file_exists", {"path": "main.py"}),
        Criterion("file_exists", {"path": "manca.py"}),
    ])
    ev = evaluate_goal(goal, tmp_path)
    assert not ev.satisfied
    assert len(ev.pending) == 1


# --- politica di approvazione (D4) ---------------------------------------

def test_checkpoint_scaffold_mai():
    goal = _goal([Criterion("file_exists", {"path": "x"})], mode=MODE_SCAFFOLD)
    assert goal.requires_checkpoint() is False


def test_checkpoint_maintenance_manuale():
    goal = _goal([Criterion("file_exists", {"path": "x"})], mode=MODE_MAINTENANCE, approval_policy=APPROVAL_MANUAL)
    assert goal.requires_checkpoint() is True


def test_checkpoint_maintenance_auto():
    goal = _goal([Criterion("file_exists", {"path": "x"})], mode=MODE_MAINTENANCE, approval_policy=APPROVAL_AUTO)
    assert goal.requires_checkpoint() is False


# --- vincoli allow/deny ---------------------------------------------------

def test_path_allowed_deny_precede():
    goal = _goal([Criterion("file_exists", {"path": "x"})], deny=["secrets/*"])
    assert goal.path_allowed("src/main.py") is True
    assert goal.path_allowed("secrets/key.pem") is False


def test_path_allowed_whitelist():
    goal = _goal([Criterion("file_exists", {"path": "x"})], allow=["src/*"])
    assert goal.path_allowed("src/main.py") is True
    assert goal.path_allowed("other/main.py") is False


# --- serializzazione ------------------------------------------------------

def test_roundtrip_dict():
    goal = _goal(
        [Criterion("tests_pass", {"timeout": 90}, label="suite verde"),
         Criterion("file_exists", {"path": "README.md"})],
        mode=MODE_SCAFFOLD,
        approval_policy=APPROVAL_MANUAL,
        deny=["*.env"],
    )
    goal.validate()
    restored = Goal.from_dict(goal.to_dict())
    assert restored.objective == goal.objective
    assert restored.mode == MODE_SCAFFOLD
    assert restored.deny == ["*.env"]
    assert [c.type for c in restored.acceptance] == ["tests_pass", "file_exists"]
    assert restored.acceptance[0].params["timeout"] == 90
    assert restored.acceptance[0].label == "suite verde"
