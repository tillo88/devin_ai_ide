#!/usr/bin/env python3
"""
Regression tests for the run() no-progress guard and status reporting
(2026-07-18).

- Identical failure repeated across attempts -> early stop with
  status "stalled" instead of burning all retries.
- Distinct failures -> loop keeps trying until max retries ("failed").
- Every run() return path carries an explicit "status" key (fast_app's
  _finish_run_events defaulted to "failed" for ALL runs, even successes).
Modelli e launcher mockati: niente GPU, rete o llama-server.
"""
import sys
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from devin.core.orchestrator import Orchestrator, LauncherStatus

DIFF_PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def _make_orchestrator(project: Path, mock_local):
    config = {
        "context": {"max_chars": 100000, "semantic_search_enabled": False},
        "coder": {"whole_file_enabled": False},
        "models": {
            "local_models_dir": str(project / "models"),
            "llama_server_path": "/bin/echo",
            "auto_start_local": False,
            "local_models": {}
        }
    }
    config_path = project / "config.json"
    config_path.write_text(json.dumps(config))

    mock_client = MagicMock()
    mock_client.refresh = MagicMock()
    mock_client.health.return_value = {}
    mock_client.local = mock_local

    mock_launcher = MagicMock()
    mock_launcher.ensure_models.return_value = LauncherStatus(
        rig_available=False,
        rig_host="",
        rig_ports=[],
        local_running={
            "8000": {"name": "coder", "port": 8000, "status": "running"},
            "8001": {"name": "planner", "port": 8001, "status": "running"},
        },
        model_source="local",
        errors=[],
    )
    mock_launcher.get_status.return_value = mock_launcher.ensure_models.return_value
    mock_launcher.shutdown_all = MagicMock()

    launcher_patch = patch('devin.core.orchestrator.LocalModelLauncher')
    ai_patch = patch('devin.core.orchestrator.AIClient')
    MockLauncher = launcher_patch.start()
    MockAI = ai_patch.start()
    MockLauncher.from_config.return_value = mock_launcher
    MockAI.return_value = mock_client

    orch = Orchestrator(config_path=str(config_path), project_path=str(project))
    return orch, launcher_patch, ai_patch


def _make_project(tmpdir: str) -> Path:
    project = Path(tmpdir) / "project"
    project.mkdir()
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (project / "main.py").write_text("from calc import add\nprint(add(2, 3))\n")
    return project


def _user_content(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def test_identical_failure_stalls_early():
    """Same error every attempt -> 'stalled' after 2 coder calls, not 3."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        calls = {"coder": 0}

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                calls["coder"] += 1
                raise RuntimeError("Connection refused")  # sempre identico
            if "ERROR" in content:
                return "Change minus to plus"  # feedback Critic costante
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "stalled", f"expected stalled, got: {result}"
        assert calls["coder"] == 2, f"guard should stop before attempt 3, got {calls['coder']} coder calls"
        print("✓ No-progress guard: stallo rilevato al 2° fallimento identico")


def test_distinct_failures_run_all_attempts():
    """Different errors each attempt -> no stall, fails at max retries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        calls = {"coder": 0}

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                calls["coder"] += 1
                raise RuntimeError(f"boom variant {calls['coder']}")  # sempre diverso
            if "ERROR" in content:
                # Il Critic eco-blocca l'errore variabile: feedback diverso a ogni giro
                m = re.search(r"boom variant \d+", content)
                return f"Suggestion based on: {m.group(0) if m else 'generic'}"
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "failed", f"expected failed, got: {result.get('status')}"
        assert "Max retries" in result["error"]
        assert calls["coder"] == 3, f"expected 3 attempts, got {calls['coder']}"
        print("✓ No-progress guard: errori diversi NON stallano, max retries raggiunto")


def test_alternating_failures_stall_on_period2_cycle():
    """Errors alternating A,B,A,B -> the single-signature guard never fires;
    the period-2 extension must stop the loop as 'stalled' (4th failure)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        calls = {"coder": 0}

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                calls["coder"] += 1
                # Alterna due errori distinti: A,B,A,B,...
                variant = "alpha" if calls["coder"] % 2 == 1 else "beta"
                raise RuntimeError(f"boom {variant}")
            if "ERROR" in content:
                # Il Critic eco-blocca l'errore: feedback alternato come l'errore
                m = re.search(r"boom (alpha|beta)", content)
                return f"Suggestion based on: {m.group(0) if m else 'generic'}"
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project),
                              max_attempts=6)
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "stalled", f"expected stalled, got: {result.get('status')}"
        assert "period 2" in result["error"]
        assert calls["coder"] == 4, \
            f"cycle confirmed at the 4th failure (A,B,A,B), got {calls['coder']} coder calls"
        print("✓ No-progress guard: ciclo A,B,A,B rilevato come stalled")


def test_run_returns_status_key_on_success():
    """Successful runs must report status 'success' (fast_app defaulted to 'failed')."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                return DIFF_PATCH
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"], f"run failed unexpectedly: {result.get('error')}"
        assert result["status"] == "success"
        print("✓ Status reporting: success esplicito nel risultato di run()")


def test_scaffold_reports_syntax_only_status():
    """Uno scaffold senza test eseguibili: success=True ma status='syntax_only'
    e nessuna registrazione in memoria (evidenza insufficiente)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            # Patch diretta degli agenti: niente dipendenza dai prompt interni
            orch.planner.plan_scaffold = lambda task: [
                {"filename": "hello.py", "spec": "stampa hello"}
            ]
            orch.coder.generate_file = lambda fname, spec, project_context="": "print('hello')\n"

            result = orch.run_scaffold("Crea script hello", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is True
        assert result["status"] == "syntax_only", f"status inatteso: {result.get('status')}"
        assert result["memory_outcome"] == "not_recorded"
        assert (project / "hello.py").read_text() == "print('hello')\n"
        print("✓ Scaffold: syntax_only riportato esplicitamente, memoria non contaminata")


def test_resume_preserves_interrupted_run_log():
    """Riprendendo un run interrotto, il log del crash NON viene troncato:
    il nuovo header viene aggiunto in coda (prima write_text lo cancellava)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        from devin.core.state_persistence import StatePersistence
        sp = StatePersistence(str(project), "run_test_resume")
        sp.save({
            "task": "Fix the bug in calc.py",
            "attempt": 1,
            "max_retries": 3,
            "plan": None,
            "last_error": "boom",
        })

        import devin.core.orchestrator as orch_mod
        log_dir = project / "logs"
        log_dir.mkdir()
        old_log = log_dir / "run_test_resume.log"
        old_log.write_text("Run started: run_test_resume\nTask: old\n[ERROR] crash\n",
                           encoding="utf-8")

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                return DIFF_PATCH
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orig_log_dir = orch_mod.LOG_DIR
        orch_mod.LOG_DIR = log_dir
        try:
            orch, lp, ap = _make_orchestrator(project, mock_local)
            try:
                result = orch.run("task nuovo ignorato in resume",
                                  project_path=str(project), run_id="run_test_resume")
            finally:
                lp.stop()
                ap.stop()
        finally:
            orch_mod.LOG_DIR = orig_log_dir

        content = old_log.read_text(encoding="utf-8")
        assert "[ERROR] crash" in content, "il log del crash e' stato troncato dal resume"
        assert "Run resumed" in content
        assert result["success"], f"resume non completato: {result.get('error')}"
        print("✓ Resume: log del crash preservato, run completato dallo stato salvato")


if __name__ == "__main__":
    print("🧪 Test no-progress guard e status reporting\n")
    test_identical_failure_stalls_early()
    test_distinct_failures_run_all_attempts()
    test_alternating_failures_stall_on_period2_cycle()
    test_run_returns_status_key_on_success()
    test_scaffold_reports_syntax_only_status()
    test_resume_preserves_interrupted_run_log()
    print("\n🎉 Tutti i test passati!")
