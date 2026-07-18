#!/usr/bin/env python3
"""
Regression tests for the run() stop/retry guards (coverage slice 1,
2026-07-18) — quattro comportamenti di Orchestrator.run() finora al buio:

1. TIMEOUT (orchestrator.py L1059-1070): il check `max_seconds` in testa al
   loop deve chiudere il run con status "timeout", footer `status: timeout`
   nel run-log e stato persistito `final_status: "timeout"`.
2. USER STOP (L1073-1084, stop() L142-149): se `_should_stop` viene alzato
   durante il planner, il check in testa al loop ferma il run PRIMA del primo
   tentativo del Coder — status "stopped", zero chiamate coder.
3. CRITIC FEEDBACK -> RETRY (L1275-1308): runner rosso al 1° tentativo, il
   Critic analizza l'errore e il suo feedback finisce nel prompt del 2°
   tentativo del Coder, che applica la patch giusta -> "success" al 2° giro,
   con il file di progetto sincronizzato dal sandbox.
4. CRITIC OFFLINE BOUNDED (L1307-1308): se il ramo "ERROR" esplode a ogni
   giro il loop NON si impianta e NON propaga eccezioni — termina "failed"
   dopo esattamente max_retries esecuzioni del runner.

Modelli e launcher mockati: niente GPU, rete o llama-server. Fixture
autocontenute (copiate da test_stalled_guard.py, come da stile del repo).
NOTA fixture: i test 3-4 sovrascrivono main.py con una `assert` — il fixture
base (`print(add(2, 3))`) esce sempre con codice 0 e il Runner non potrebbe
mai fallire.
"""
import sys
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from devin.core.orchestrator import Orchestrator, LauncherStatus
from devin.core.state_persistence import StatePersistence

DIFF_PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

# Si applica pulita ma NON corregge il bug: l'assert del progetto resta rossa.
NOOP_PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a - b  # still buggy
"""


def _noop_main_patch(n: int) -> str:
    """Patch che riscrive l'assert di main.py con un messaggio DISTINTO per
    tentativo: stderr del runner diverso a ogni giro -> la no-progress guard
    (firme identiche -> 'stalled') non scatta e il loop arriva a max_retries."""
    return f"""diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1,2 +1,2 @@
 from calc import add
-assert add(2, 3) == 5
+assert add(2, 3) == 5, "boom attempt {n}"
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


def _make_assert_project(tmpdir: str) -> Path:
    """Come _make_project, ma main.py ha una assert che resta ROSSA finche'
    calc.py non e' corretto — senza di essa il Runner uscirebbe sempre 0."""
    project = _make_project(tmpdir)
    (project / "main.py").write_text("from calc import add\nassert add(2, 3) == 5\n")
    return project


def _user_content(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def test_run_timeout_status_and_state():
    """Coder troppo lento (~2s) con max_seconds=1 -> al check in testa al 2°
    giro il run chiude 'timeout': risultato, footer del run-log e stato
    persistito devono concordare."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                time.sleep(2)  # brucia il budget di 1s
                raise RuntimeError("coder too slow")
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        import devin.core.orchestrator as orch_mod
        log_dir = project / "logs"
        log_dir.mkdir()
        orig_log_dir = orch_mod.LOG_DIR
        orch_mod.LOG_DIR = log_dir
        try:
            orch, lp, ap = _make_orchestrator(project, mock_local)
            try:
                result = orch.run("Fix the bug in calc.py", project_path=str(project),
                                  max_seconds=1, run_id="run_test_timeout")
            finally:
                lp.stop()
                ap.stop()
        finally:
            orch_mod.LOG_DIR = orig_log_dir

        assert result["success"] is False
        assert result["status"] == "timeout", f"expected timeout, got: {result.get('status')}"
        assert result["error"] == "Timeout: max_seconds exceeded"

        log_content = (log_dir / "run_test_timeout.log").read_text(encoding="utf-8")
        assert "\nstatus: timeout\n" in log_content, \
            f"footer 'status: timeout' mancante nel run-log:\n{log_content[-300:]}"

        state = StatePersistence(str(project), "run_test_timeout").load()
        assert state is not None, "stato del run non persistito"
        assert state["final_status"] == "timeout", f"stato: {state.get('final_status')}"
        print("✓ Timeout guard: status/footer/stato concordi su 'timeout'")


def test_run_user_stop_before_first_attempt():
    """_should_stop alzato DURANTE il planner -> il check in testa al loop
    ferma il run prima del 1° tentativo del Coder: zero chiamate coder."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        calls = {"coder": 0}
        holder = {}  # l'orch non esiste ancora quando definiamo il mock

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                # stop() (L142-149) fa anche runner.stop(): qui basta il flag,
                # e' l'UNICA cosa che il check di run() (L1073) osserva.
                holder["orch"]._should_stop = True
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                calls["coder"] += 1
                return DIFF_PATCH
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        import devin.core.orchestrator as orch_mod
        log_dir = project / "logs"
        log_dir.mkdir()
        orig_log_dir = orch_mod.LOG_DIR
        orch_mod.LOG_DIR = log_dir
        try:
            orch, lp, ap = _make_orchestrator(project, mock_local)
            holder["orch"] = orch
            try:
                result = orch.run("Fix the bug in calc.py", project_path=str(project),
                                  run_id="run_test_stop")
            finally:
                lp.stop()
                ap.stop()
        finally:
            orch_mod.LOG_DIR = orig_log_dir

        assert result["success"] is False
        assert result["status"] == "stopped", f"expected stopped, got: {result.get('status')}"
        assert result["error"] == "Run stopped by user"
        assert calls["coder"] == 0, \
            f"lo stop deve precedere il 1° tentativo, got {calls['coder']} coder calls"

        log_content = (log_dir / "run_test_stop.log").read_text(encoding="utf-8")
        assert "\nstatus: stopped\n" in log_content

        state = StatePersistence(str(project), "run_test_stop").load()
        assert state is not None and state["final_status"] == "stopped"
        print("✓ Stop guard: run fermato prima del Coder, stato 'stopped'")


def test_runner_failure_critic_feedback_retry_success():
    """1° tentativo: patch che lascia l'assert rossa -> runner fallisce ->
    Critic analizza -> il SUO feedback entra nel prompt del 2° tentativo del
    Coder, che produce la patch giusta -> 'success' e file sincronizzato."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_assert_project(tmpdir)
        coder_prompts = []

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                coder_prompts.append(content)
                return NOOP_PATCH if len(coder_prompts) == 1 else DIFF_PATCH
            if "ERROR" in content:
                return "CRITIC_FEEDBACK: change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"], f"run failed unexpectedly: {result.get('error')}"
        assert result["status"] == "success"
        assert len(coder_prompts) == 2, \
            f"atteso successo al 2° tentativo, got {len(coder_prompts)} coder calls"
        assert "CRITIC_FEEDBACK: change minus to plus" in coder_prompts[1], \
            "il 2° prompt del Coder NON contiene il feedback del Critic"
        assert "CRITIC_FEEDBACK" not in coder_prompts[0]
        # Sandbox risincronizzato sul progetto dopo il successo (L1249-1253)
        assert "return a + b" in (project / "calc.py").read_text(), \
            "calc.py del progetto non sincronizzato dal sandbox"
        print("✓ Critic feedback: retry con feedback nel prompt, progetto sincronizzato")


def test_critic_offline_loop_bounded():
    """Critic KO a ogni giro (ramo 'ERROR' che raise): il catch warn-only
    (L1307-1308) deve tenerlo bounded — niente hang, niente eccezioni,
    'failed' dopo esattamente max_retries esecuzioni del runner."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_assert_project(tmpdir)
        calls = {"coder": 0}

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                calls["coder"] += 1
                # Messaggio d'assert diverso a ogni giro: firme d'errore
                # distinte -> la no-progress guard non scatta (comportamento
                # gia' pinnato in test_stalled_guard.py).
                return _noop_main_patch(calls["coder"])
            if "ERROR" in content:
                raise RuntimeError("critic offline")  # Critic irraggiungibile
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        runner_calls = {"n": 0}
        orig_runner_run = orch.runner.run

        def counting_run(*args, **kwargs):
            runner_calls["n"] += 1
            return orig_runner_run(*args, **kwargs)

        orch.runner.run = counting_run
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project),
                              max_attempts=3)
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "failed", \
            f"expected failed (bounded loop), got: {result.get('status')}"
        assert "Max retries" in result["error"]
        assert runner_calls["n"] == 3, \
            f"expected exactly max_retries runner executions, got {runner_calls['n']}"
        assert calls["coder"] == 3
        print("✓ Critic offline: loop bounded, 'failed' a max_retries senza hang")


if __name__ == "__main__":
    print("🧪 Test run() stop/retry guards (coverage slice 1)\n")
    test_run_timeout_status_and_state()
    test_run_user_stop_before_first_attempt()
    test_runner_failure_critic_feedback_retry_success()
    test_critic_offline_loop_bounded()
    print("\n🎉 Tutti i test passati!")
