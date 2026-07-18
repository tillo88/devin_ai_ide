#!/usr/bin/env python3
"""
Test-only pins — orchestrator coverage slice 3 (2026-07-18).

Quattro aree finora al buio, pinnate contro il comportamento ATTUALE del
sorgente (nessuna modifica a devin/ in questo file):

1. SANDBOX-SYNC EXCLUSIONS (_sync_sandbox_to_project, orchestrator.py
   L151-177): le esclusioni effettive sono
     excluded_parts    = {"workspace", "venv", ".venv", "env", ".git",
                          "__pycache__", ".pytest_cache", "node_modules",
                          "dist", "build", "logs", ".devin", ".devin_chat",
                          ".devin_cache", ".devin_state"}
     excluded_suffixes = {".pyc", ".pyo", ".rej", ".orig", ".tmp",
                          ".bak", ".gguf"}
   piu' lo skip su contenuto identico (byte-compare L173-174). Pinnato:
   workspace/x.py, .devin_state/run.json, *.gguf e *.rej NON copiati;
   b.py cambiato aggiornato; a.py identico NON ritoccato (contenuto e
   mtime invariati — il byte-compare evita la copia).

2. RESUME WITH SAVED PLAN + ATTEMPT COUNTER (run() L892-922, L1028-1039,
   L1132-1134): stato seedato via StatePersistence.save con plan
   serializzato e attempt=2 (max_retries=3). Il run riprende SENZA
   ri-pianificare (il prompt "TASK" del Planner non viene mai inviato),
   ricostruisce il Plan dai campi steps/raw_response salvati, ripristina
   task e last_error, ed esegue SOLO il tentativo rimanente (3/3).

3. _run_pytest_gate FALLBACK BRANCHES (L377-416): (a) output "No module
   named pytest" -> fallback al comando unittest (L407-410); (b) ramo
   timeout (L399-401) faked alzando subprocess.TimeoutExpired — nessuna
   attesa reale; (c) eccezione generica sul primo comando -> si passa al
   successivo (L402-405). subprocess.run interamente finto.

4. _maybe_web_reference CAP PER RUN — FLIP (2026-07-18, decisione owner):
   il pin precedente congelava il quirk per-lifetime (_web_searches_done
   mai resettato tra un run e l'altro sullo stesso Orchestrator). Ora
   run()/run_scaffold() chiamano _reset_web_search_budget() all'avvio e
   ogni run riparte col budget pieno max_per_run (default 2, config
   web_search.agent_search.max_per_run) — allineato al nome del config
   key. Il cap DENTRO un singolo run resta invariato.

Modelli e launcher mockati: niente GPU, rete o llama-server. Fixture
autocontenute (stile repo, copiate da test_stalled_guard.py).
"""
import os
import sys
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
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


# ============================================================
# 1. SANDBOX-SYNC EXCLUSIONS
# ============================================================

def test_sync_sandbox_exclusions_and_identical_skip():
    """Esclusioni effettive di _sync_sandbox_to_project (L155-160): file in
    workspace/ e .devin_state/, suffissi .gguf/.rej NON copiati; file
    identico per contenuto NON ricopiato (mtime preservata); file
    modificato aggiornato. Pattern unbound-call come
    test_security_regressions.test_sync_copies_non_python_files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        sandbox = base / "sandbox"
        project = base / "project"
        (sandbox / "workspace").mkdir(parents=True)
        (sandbox / ".devin_state").mkdir()
        project.mkdir()

        # Esclusi per PART del path (excluded_parts, L155-159)
        (sandbox / "workspace" / "x.py").write_text("print('sandbox-only')\n")
        (sandbox / ".devin_state" / "run.json").write_text("{}")
        # Esclusi per SUFFISSO (excluded_suffixes, L160)
        (sandbox / "model.gguf").write_bytes(b"GGUF" + b"\0" * 64)
        (sandbox / "calc.py.rej").write_text("rejected hunk\n")
        # Identico al progetto -> skip su byte-compare (L173-174)
        identical = "def add(a, b):\n    return a + b\n"
        (sandbox / "a.py").write_text(identical)
        (project / "a.py").write_text(identical)
        # Modificato -> aggiornato
        (sandbox / "b.py").write_text("def mul(a, b):\n    return a * b\n")
        (project / "b.py").write_text("def mul(a, b):\n    return a - b\n")

        # Mtime antica su a.py: se la copia fosse eseguita cambierebbe.
        old_mtime = 1_500_000_000
        os.utime(project / "a.py", (old_mtime, old_mtime))

        Orchestrator._sync_sandbox_to_project(
            object(), str(sandbox), str(project))

        assert not (project / "workspace" / "x.py").exists(), \
            "workspace/ NON deve essere sincronizzato (excluded_parts)"
        assert not (project / ".devin_state" / "run.json").exists(), \
            ".devin_state/ NON deve essere sincronizzato (excluded_parts)"
        assert not (project / "model.gguf").exists(), \
            ".gguf NON deve essere sincronizzato (excluded_suffixes)"
        assert not (project / "calc.py.rej").exists(), \
            ".rej NON deve essere sincronizzato (excluded_suffixes)"

        assert (project / "b.py").read_text() == "def mul(a, b):\n    return a * b\n", \
            "b.py modificato nel sandbox deve aggiornare il progetto"

        assert (project / "a.py").read_text() == identical
        assert (project / "a.py").stat().st_mtime == old_mtime, \
            "a.py identico: byte-compare skip — il file NON va ritoccato"
        print("✓ Sync sandbox: esclusioni pin, file identico intatto, modificato aggiornato")


# ============================================================
# 2. RESUME WITH SAVED PLAN + ATTEMPT COUNTER
# ============================================================

def test_resume_with_saved_plan_skips_planner_and_counts_attempts():
    """Stato interrotto con plan serializzato + attempt=2 (max_retries=3):
    run(run_id=stesso) ripristina task/last_error/plan (L912-922), salta il
    Planner (L1028-1039) ed esegue SOLO l'ultimo tentativo (3/3)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        from devin.core.state_persistence import StatePersistence
        saved_plan = {"steps": ["Fix calc.py add()", "Run main.py"],
                      "raw_response": "1. Fix calc.py add()\n2. Run main.py"}
        StatePersistence(str(project), "run_resume_plan").save({
            "task": "Fix the bug in calc.py",
            "attempt": 2,
            "last_error": "boom from the crashed attempt",
            "last_patch": "",
            "plan": saved_plan,
            "context_length": 42,
            "max_retries": 3,
            "model_source": "local",
        })

        coder_calls = {"n": 0, "saw_last_error": False, "saw_plan": False}

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                # Prompt del Planner (planner.py L145+): se arriva, il run
                # sta RI-pianificando invece di usare il piano salvato.
                raise AssertionError("planner prompt sent during resume — re-planning!")
            if "CURRENT CODE" in content:
                coder_calls["n"] += 1
                # last_error ripristinato -> feedback_block del Coder
                # (coder.py L39-45); plan.raw_response ripristinato ->
                # sezione PLAN (coder.py L47-56).
                if "boom from the crashed attempt" in content:
                    coder_calls["saw_last_error"] = True
                if saved_plan["raw_response"] in content:
                    coder_calls["saw_plan"] = True
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
            try:
                orch.git_ops.commit = MagicMock()
                # Il banner "Resuming previous run" (L894) passa da self._log
                # PRIMA che la closure log() esista: va catturato via callback.
                sse_msgs = []
                orch.sse_callback = lambda msg, level: sse_msgs.append(msg)
                result = orch.run("task nuovo IGNORATO in resume",
                                  project_path=str(project),
                                  run_id="run_resume_plan")
            finally:
                lp.stop()
                ap.stop()
        finally:
            orch_mod.LOG_DIR = orig_log_dir

        assert result["success"] is True, f"resume non completato: {result.get('error')}"
        assert result["status"] == "success"

        # Plan ricostruito dal dict salvato (L917-922) e ritornato nel result.
        assert result["plan"]["steps"] == saved_plan["steps"]
        assert result["plan"]["raw_response"] == saved_plan["raw_response"]

        # Un SOLO tentativo eseguito: attempt riparte da 2 -> 3/3 (L1132-1133).
        assert coder_calls["n"] == 1, \
            f"atteso 1 solo tentativo residuo (3/3), fatti {coder_calls['n']}"
        assert coder_calls["saw_last_error"], \
            "last_error salvato NON ripristinato nel feedback del Coder"
        assert coder_calls["saw_plan"], \
            "raw_response del piano salvato NON arrivato al Coder"

        msgs = [entry["msg"] for entry in result["logs"]]
        assert any("Resuming previous run run_resume_plan (attempt 3/3)" in m
                   for m in sse_msgs), f"banner di resume assente: {sse_msgs[:5]}"
        assert any("Resumed plan: 2 steps" in m for m in msgs)
        assert any("Attempt 3/3" in m for m in msgs)
        print("✓ Resume con piano salvato: no re-plan, attempt 3/3, task/errore ripristinati")


# ============================================================
# 3. _run_pytest_gate FALLBACK BRANCHES
# ============================================================

def test_pytest_gate_falls_back_to_unittest_when_pytest_missing(monkeypatch, tmp_path):
    """Output 'No module named pytest' (L407) -> il gate passa al comando
    unittest discover (L409-410) e usa il SUO returncode."""
    calls = []

    def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        calls.append(argv)
        if argv[2] == "pytest":
            return MagicMock(stdout="", stderr="/usr/bin/python: No module named pytest",
                             returncode=1)
        return MagicMock(stdout="OK\n", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = Orchestrator._run_pytest_gate(object(), tmp_path)

    assert result["success"] is True
    assert result["command"] == "unittest", f"comando inatteso: {result}"
    assert [c[2] for c in calls] == ["pytest", "unittest"], \
        f"fallback non tentato nell'ordine atteso: {calls}"
    print("✓ Pytest gate: pytest mancante -> fallback unittest")


def test_pytest_gate_generic_error_continues_to_next_command(monkeypatch, tmp_path):
    """Eccezione generica sul primo comando (L402-405): registrata in `last`
    e si continua col comando successivo."""
    def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        if argv[2] == "pytest":
            raise FileNotFoundError("python non trovato")
        return MagicMock(stdout="Ran 0 tests", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = Orchestrator._run_pytest_gate(object(), tmp_path)

    assert result["success"] is True
    assert result["command"] == "unittest"
    print("✓ Pytest gate: eccezione sul primo comando -> prossimo comando")


def test_pytest_gate_timeout_branch(monkeypatch, tmp_path):
    """Ramo timeout (L399-401): subprocess.TimeoutExpired -> return
    immediato {'success': False, 'command': 'pytest', 'output': 'timeout
    dopo 180s'} SENZA tentare unittest. Finto: nessuna attesa reale."""
    def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = Orchestrator._run_pytest_gate(object(), tmp_path)

    assert result["success"] is False
    assert result["command"] == "pytest", f"command inatteso: {result}"
    assert result["output"] == "timeout dopo 180s", f"output inatteso: {result}"
    print("✓ Pytest gate: timeout -> failed immediato, niente fallback")


def test_pytest_gate_all_runners_unavailable(monkeypatch, tmp_path):
    """Entrambi i runner in eccezione -> il dict `last` finale (L392, L416)
    riporta l'ultima eccezione col nome del tipo."""
    def fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None, env=None):
        raise OSError("exec failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = Orchestrator._run_pytest_gate(object(), tmp_path)

    assert result["success"] is False
    assert result["command"] == "unittest", f"command inatteso: {result}"
    assert result["output"] == "OSError: exec failed", f"output inatteso: {result}"
    print("✓ Pytest gate: nessun runner disponibile -> last con l'eccezione")


# ============================================================
# 4. _maybe_web_reference CAP PER RUN (fix 2026-07-18, ex-quirk pinnato)
# ============================================================

def test_web_reference_cap_resets_per_run(monkeypatch):
    """FLIP del pin `test_web_reference_cap_is_per_orchestrator_lifetime_not_per_run`
    (decisione owner 2026-07-18): il budget max_per_run ora vale DAVVERO per
    run — run()/run_scaffold() chiamano _reset_web_search_budget() all'avvio.
    Due "run" consecutivi sullo STESSO oggetto ricevono ciascuno il budget
    pieno (2 ricerche col default), mentre il cap DENTRO un run resta
    invariato. Pattern unbound-call invariato rispetto al pin originale."""
    import devin.ai.web_search as web_search

    monkeypatch.setattr(web_search, "is_searchable_error", lambda error: True)
    searches = {"n": 0}

    def fake_search(query, config, max_chars=None):
        searches["n"] += 1
        return "REFERENCE BLOCK"

    monkeypatch.setattr(web_search, "search_coding_context", fake_search)

    # Bare-orchestrator: bastano ai_client.config (default: enabled True,
    # max_per_run 2) e _log. Pattern unbound-call.
    stub = SimpleNamespace(
        ai_client=SimpleNamespace(config={}),
        _log=lambda *a, **k: None,
    )

    err = "ModuleNotFoundError: No module named 'fantasy_pkg'"
    # Run 1: due ricerche, poi cap raggiunto (invariato).
    first = Orchestrator._maybe_web_reference(stub, err)
    second = Orchestrator._maybe_web_reference(stub, err)
    third = Orchestrator._maybe_web_reference(stub, err)

    assert "WEB REFERENCE" in first and "REFERENCE BLOCK" in first
    assert "WEB REFERENCE" in second
    assert third == "", "cap max_per_run=2 non rispettato dentro il run"
    assert searches["n"] == 2
    assert stub._web_searches_done == 2

    # Run 2 sullo STESSO oggetto: l'entry point azzera il budget all'avvio
    # (run()/run_scaffold() -> _reset_web_search_budget()).
    Orchestrator._reset_web_search_budget(stub)
    assert stub._web_searches_done == 0

    fourth = Orchestrator._maybe_web_reference(stub, err)
    fifth = Orchestrator._maybe_web_reference(stub, err)
    sixth = Orchestrator._maybe_web_reference(stub, err)

    assert "WEB REFERENCE" in fourth, "budget NON resettato: il quirk per-lifetime e' tornato?"
    assert "WEB REFERENCE" in fifth
    assert sixth == "", "cap max_per_run=2 non rispettato nel secondo run"
    assert searches["n"] == 4, "due run devono avere ciascuno il budget pieno"
    print("✓ Web-ref cap: reset per run (max_per_run), cap intra-run invariato")


def test_run_scaffold_entry_resets_web_search_budget(tmp_path):
    """Wiring reale dell'entry point: run_scaffold() azzera il contatore PRIMA
    di qualunque early-return (qui: nessun modello disponibile). Stub minimo:
    git_ops/_log/ensure_models; Path.mkdir gira davvero su tmp_path."""
    stub = SimpleNamespace(
        project_path=str(tmp_path),
        git_ops=SimpleNamespace(project_path=""),
        _log=lambda *a, **k: None,
        ensure_models=lambda: SimpleNamespace(model_source="unavailable"),
        _web_searches_done=7,
    )
    # unbound-call: il metodo reale va legato a mano sullo stub
    stub._reset_web_search_budget = lambda: Orchestrator._reset_web_search_budget(stub)
    result = Orchestrator.run_scaffold(stub, task="qualunque", project_path=str(tmp_path))
    assert result["success"] is False  # nessun modello: early return
    assert stub._web_searches_done == 0, "run_scaffold non ha resettato il budget web"
    print("✓ Web-ref cap: run_scaffold resetta il budget anche su early-return")


if __name__ == "__main__":
    print("🧪 Test sync exclusions / resume-with-plan / pytest-gate / web-ref quirk (slice 3)\n")
    # I test gate/quirk usano la fixture monkeypatch di pytest: esecuzione
    # standalone delegata a pytest stesso.
    import pytest
    pytest.main([__file__, "-q", "--capture=no"])
