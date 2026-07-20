#!/usr/bin/env python3
"""
Regression tests for whole-file edit mode and scaffold failure wiring
(coverage slice 2, 2026-07-18) — sei comportamenti finora al buio:

WHOLE-FILE MODE (orchestrator.py L1040-1051 + L1140-1182): ogni test run()
esistente forza whole_file_enabled=False, quindi il ramo intero era scoperto.
1. SUCCESS PATH: progetto piccolo + whole_file_enabled=True -> il Coder
   risponde con '### FILE: calc.py' + fenced block -> apply_full_files ->
   runner verde -> status "success" e calc.py del progetto aggiornato.
2. EMPTY-OUTPUT GUARD (L1158-1163): risposta senza blocchi parsabili ->
   full_files={} -> last_error col contratto esatto "You returned NO
   files..." e MAI una chiamata ad apply_full_files. NOTA vs. brief: il
   guard non porta a "failed" ma a "stalled" — l'errore e' identico a ogni
   tentativo e la no-progress guard (L1086-1129, gia' pinnata in
   test_stalled_guard.py) scatta correttamente al 2° fallimento identico.
3. MODE SELECTION (_small_project L847-861 + select L1044-1051): boundary
   esatta su max_lines (300 righe -> whole-file, 301 -> diff; lista vuota
   ed eccezione -> diff) + integrazione: progetto con file grande e
   whole_file_enabled=True resta in unified diff.

SCAFFOLD FAILURE WIRING (run_scaffold L595-794): il quality gate in se' e'
coperto altrove (test_scaffold_resilience.py); qui si pinnano le
CONSEGUENZE a valle del gate rosso.
4. GATE RED -> HEAL LOOP -> STILL RED (L719-743, 762-767, 773-775): gate
   verified_failure, heal loop esaurito senza verde -> status "failed",
   entry "<quality_gate>" in files_failed, NESSUN commit tentato.
5. MEMORY POLARITY (L758 + _remember_scaffold_outcome L554-587): stesso
   scenario rosso con Memory fake -> outcome registrato come
   verified_failure con polarity:negative — il fallimento diventa evidenza
   strutturata, non silenzio. (Il contratto di _remember_scaffold_outcome
   isolato e' gia' coperto; qui e' pinnato il wiring end-to-end.)
6. EMPTY-PLAN EXIT (L630-641): planner senza piano -> uscita immediata
   {"success": False, "status": "failed", "error": "empty file plan"},
   zero chiamate al Coder.
7-8. EARLY-RETURN STATUS CONTRACT (slice 3, fix L607/L618-622): le uscite
   anticipate "No models available" e "local planner unavailable" portano
   status "failed" come il main path — prima mancava la chiave e i consumer
   dovevano indovinare il vocabolario.

Modelli e launcher mockati: niente GPU, rete o llama-server. Fixture
autocontenute (copiate da test_orchestrator_run_guards.py, stile repo).
"""
import sys
import json
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

# Formato atteso da Coder._parse_full_files (coder.py L78-93):
# '### FILE: <path>' + fenced code block col contenuto COMPLETO.
WHOLE_FILE_ANSWER = """Ecco il file corretto:

### FILE: calc.py
```python
def add(a, b):
    return a + b
```
"""

# Contratto esatto del guard empty-files (orchestrator.py L1159-1161).
NO_FILES_CONTRACT = "You returned NO files in the required format."

WRONG_IMPL = "def add(a, b):\n    return a - b\n"
FAILING_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _make_orchestrator(project: Path, mock_local, coder_cfg=None):
    config = {
        "context": {"max_chars": 100000, "semantic_search_enabled": False},
        "coder": coder_cfg if coder_cfg is not None else {"whole_file_enabled": False},
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


def _patch_scaffold_agents(orch, files_by_name):
    """Direct-patching degli agenti (pattern di test_stalled_guard.py L236-238):
    niente dipendenza dai prompt interni di planner/coder."""
    orch.planner.plan_scaffold = lambda task: [
        {"filename": name, "spec": f"spec di {name}"} for name in files_by_name
    ]
    orch.coder.generate_file = (
        lambda fname, spec, project_context="": files_by_name[fname]
    )


def test_whole_file_success_updates_project_file():
    """Progetto piccolo + whole_file_enabled=True -> il Coder riscrive il
    file intero ('### FILE:' + fence), niente diff/patcher: status 'success'
    e calc.py del progetto aggiornato col contenuto nuovo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                # Il prompt whole-file recita 'CURRENT CODE (the exact
                # current content...)' — stessa chiave di routing del diff.
                return WHOLE_FILE_ANSWER
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local, coder_cfg={"whole_file_enabled": True})
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"], f"run failed unexpectedly: {result.get('error')}"
        assert result["status"] == "success"
        assert "return a + b" in (project / "calc.py").read_text(), \
            "calc.py non aggiornato dal ramo whole-file"
        assert any("WHOLE-FILE" in entry["msg"] for entry in result["logs"]), \
            "mode select (L1048-1049) non ha scelto whole-file su progetto piccolo"
        print("✓ Whole-file: file intero riscritto, progetto aggiornato, success")


def test_review_mode_keeps_verified_changes_pending():
    """In review mode un runner verde non scrive né committa il progetto."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                return WHOLE_FILE_ANSWER
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local, coder_cfg={"whole_file_enabled": True})
        orch.change_application_mode = "review"
        orch.git_ops.commit = MagicMock()
        try:
            result = orch.run(
                "Fix the bug in calc.py",
                project_path=str(project),
                run_id="run_review_mode",
            )
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["verified"] is True
        assert result["applied"] is False
        assert result["status"] == "awaiting_approval"
        assert "return a - b" in (project / "calc.py").read_text()
        assert result["change_manifest"]["counts"]["modify"] == 1
        assert result["change_manifest"]["entries"][0]["path"] == "calc.py"
        orch.git_ops.commit.assert_not_called()
        state = json.loads(
            (project / ".devin_state" / "run_review_mode.json").read_text()
        )
        assert state["final_status"] == "awaiting_approval"
        assert state["verified"] is True and state["applied"] is False


def test_whole_file_empty_output_stalls_without_apply():
    """Coder whole-file che risponde senza blocchi '### FILE:' -> il guard
    L1158-1163 mette il contratto esatto in last_error e riprova SENZA mai
    chiamare apply_full_files; errore identico a ogni giro -> la no-progress
    guard chiude 'stalled' al 2° fallimento (composizione corretta dei due
    guard, non un bug: il brief diceva 'failed', il codice reale stall-and-stop)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        calls = {"coder": 0}

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                calls["coder"] += 1
                return "I would fix the operator, but I cannot format files."
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local, coder_cfg={"whole_file_enabled": True})
        apply_spy = MagicMock(side_effect=orch.patcher.apply_full_files)
        orch.patcher.apply_full_files = apply_spy
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "stalled", \
            f"errore identico ripetuto deve chiudere 'stalled', got: {result.get('status')}"
        assert NO_FILES_CONTRACT in result["error"], \
            f"contratto empty-files assente dall'errore: {result.get('error')}"
        assert calls["coder"] == 2, \
            f"stallo atteso al 2° fallimento identico, got {calls['coder']} coder calls"
        assert apply_spy.call_count == 0, \
            "apply_full_files NON deve mai essere chiamato con output vuoto"
        assert (project / "calc.py").read_text() == "def add(a, b):\n    return a - b\n", \
            "il file di progetto non deve essere toccato"
        print("✓ Whole-file empty-output: contratto NO files, zero apply, stalled bounded")


def test_edit_mode_selection_boundary():
    """_small_project (L847-861): boundary esatta — file da max_lines righe
    -> whole-file, max_lines+1 -> diff; lista vuota ed eccezione del context
    engine -> diff (fail-safe). Integrazione: progetto con file grande e
    whole_file_enabled=True resta in unified diff (L1050-1051)."""
    def bare_orch(files):
        orch = Orchestrator.__new__(Orchestrator)
        orch.context_engine = MagicMock()
        orch.context_engine.collect_project_files.return_value = files
        return orch

    # count("\n") + 1 == righe (L859): 299 newline -> 300 righe esatte.
    small = bare_orch([{"content": "x" + "\n" * 299}])
    assert small._small_project(300) is True, "300 righe esatte devono stare sotto soglia"
    big = bare_orch([{"content": "x" + "\n" * 299},
                     {"content": "x" + "\n" * 300}])  # 301 righe: oltre soglia
    assert big._small_project(300) is False, "UN file da 301 righe basta a negare whole-file"
    empty = bare_orch([])
    assert empty._small_project(300) is False, "progetto senza file -> diff (fail-safe)"
    broken = bare_orch(None)
    broken.context_engine.collect_project_files.side_effect = RuntimeError("fs ko")
    assert broken._small_project(300) is False, "eccezione nel collect -> diff (fail-safe)"

    # Integrazione lato "sopra soglia": whole_file_enabled=True ma progetto
    # grande -> il run resta in unified diff e completa via DIFF_PATCH.
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        (project / "big.py").write_text("".join(f"# riga {i}\n" for i in range(310)))

        def mock_local(messages, mode="reasoning", timeout=None):
            content = _user_content(messages)
            if "TASK" in content:
                return "RESULT: ACTION_NEEDED\n1. Fix calc.py"
            if "CURRENT CODE" in content:
                return DIFF_PATCH
            if "ERROR" in content:
                return "Change minus to plus"
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local, coder_cfg={"whole_file_enabled": True})
        try:
            result = orch.run("Fix the bug in calc.py", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"], f"run diff-mode failed: {result.get('error')}"
        assert any("unified diff" in entry["msg"] for entry in result["logs"]), \
            "progetto sopra soglia deve restare in unified diff"
        assert "return a + b" in (project / "calc.py").read_text()
        print("✓ Mode selection: boundary 300/301, fail-safe, grande -> unified diff")


def test_scaffold_gate_red_heal_exhausted_fails_without_commit():
    """Gate rosso -> heal loop (budget 1) rigenera ma resta rosso ->
    status 'failed', entry '<quality_gate>' in files_failed (L737-743) e
    NESSUN commit tentato (commit solo se zero fallimenti, L762-767)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local,
            coder_cfg={"whole_file_enabled": False, "self_heal_max_iterations": 1})
        try:
            _patch_scaffold_agents(orch, {
                "calc.py": WRONG_IMPL,        # rigenerato dal heal loop: resta sbagliato
                "test_calc.py": FAILING_TEST,  # pytest-style: il gate lo esegue davvero
            })
            orch.git_ops.commit = MagicMock()

            result = orch.run_scaffold("Crea una calcolatrice", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "failed", f"status inatteso: {result.get('status')}"
        assert result["quality_gate"]["status"] == "verified_failure"
        gate_entries = [f for f in result["files_failed"] if f["filename"] == "<quality_gate>"]
        assert gate_entries, f"'<quality_gate>' assente da files_failed: {result['files_failed']}"
        assert "pytest failed" in gate_entries[0]["error"], \
            f"errore del gate inatteso: {gate_entries[0]['error'][:200]}"
        orch.git_ops.commit.assert_not_called()
        print("✓ Scaffold: gate rosso post-heal -> failed, <quality_gate>, nessun commit")


def test_scaffold_review_mode_keeps_green_candidate_in_sandbox():
    """Uno scaffold verde in review mode non scrive né committa il progetto.

    La sandbox verificata produce lo stesso manifest pending usato dalla
    manutenzione; soltanto Apply potrà promuovere i file reali.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local,
            coder_cfg={"whole_file_enabled": False, "self_heal_loop": False},
        )
        try:
            _patch_scaffold_agents(orch, {
                "calc.py": "def add(a, b):\n    return a + b\n",
                "test_calc.py": (
                    "from calc import add\n\n"
                    "def test_add():\n    assert add(2, 3) == 5\n"
                ),
            })
            orch.change_application_mode = "review"
            orch.git_ops.commit = MagicMock()

            result = orch.run_scaffold(
                "Correggi la calcolatrice con test reali",
                project_path=str(project),
                run_id="run_scaffold_review",
            )
        finally:
            lp.stop()
            ap.stop()

        assert result["status"] == "awaiting_approval"
        assert result["verified"] is True
        assert result["applied"] is False
        assert "return a - b" in (project / "calc.py").read_text()
        assert not (project / "test_calc.py").exists()
        assert {item["path"] for item in result["change_manifest"]["entries"]} == {
            "calc.py", "test_calc.py"
        }
        orch.git_ops.commit.assert_not_called()

        state = json.loads(
            (project / ".devin_state" / "run_scaffold_review.json").read_text()
        )
        assert state["final_status"] == "awaiting_approval"
        assert state["verified"] is True
        assert state["applied"] is False


def test_scaffold_failure_memory_negative_polarity():
    """Stesso scenario rosso con Memory fake: il fallimento del gate diventa
    evidenza strutturata — store_local chiamato con status:verified_failure
    e polarity:negative (_remember_scaffold_outcome L554-587), non silenzio."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)
        captured = {}

        class Memory:
            def store_local(self, content, **kwargs):
                captured["content"] = content
                captured.update(kwargs)
                return "local_stored"

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(
            project, mock_local,
            coder_cfg={"whole_file_enabled": False, "self_heal_max_iterations": 1})
        try:
            _patch_scaffold_agents(orch, {
                "calc.py": WRONG_IMPL,
                "test_calc.py": FAILING_TEST,
            })
            orch.git_ops.commit = MagicMock()
            orch.memory_client = Memory()

            result = orch.run_scaffold("Crea una calcolatrice", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["status"] == "failed"
        assert result["memory_outcome"] == "local_stored", \
            f"fallimento verificato NON registrato: {result.get('memory_outcome')}"
        assert "status:verified_failure" in captured["tags"]
        assert "polarity:negative" in captured["tags"], \
            f"polarità negativa assente: {captured['tags']}"
        assert "do not repeat" in captured["content"]
        print("✓ Scaffold: fallimento -> memoria verified_failure/polarity:negative")


def test_scaffold_empty_plan_exits_without_generation():
    """Planner senza piano valido -> uscita immediata (L630-641):
    {"success": False, "status": "failed", "error": "empty file plan"} e
    ZERO chiamate al Coder (nessuna generazione tentata, nessun commit)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            orch.planner.plan_scaffold = lambda task: []
            orch.coder.generate_file = MagicMock(return_value="print('x')\n")
            orch.git_ops.commit = MagicMock()

            result = orch.run_scaffold("Task ambiguo", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "failed", \
            f"early return senza status coerente col main path: {result}"
        assert result["error"] == "empty file plan", f"diagnostica inattesa: {result}"
        orch.coder.generate_file.assert_not_called()
        orch.git_ops.commit.assert_not_called()
        print("✓ Scaffold: piano vuoto -> exit immediato (status failed), zero generazioni")


def test_scaffold_no_models_returns_failed_status():
    """ensure_models() -> model_source 'unavailable' -> early return
    (L605-607) con status 'failed' nel dict — stesso vocabolario del main
    path, il consumer non deve indovinare. Zero chiamate al Planner."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            orch.ensure_models = MagicMock(return_value=LauncherStatus(
                rig_available=False, rig_host="", rig_ports=[],
                local_running={}, model_source="unavailable",
                errors=["no models"]))
            orch.planner.plan_scaffold = MagicMock()

            result = orch.run_scaffold("Qualunque task", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "failed", \
            f"early return senza status coerente col main path: {result}"
        assert result["error"] == "No models available", f"diagnostica inattesa: {result}"
        orch.planner.plan_scaffold.assert_not_called()
        print("✓ Scaffold: nessun modello -> status failed, planner mai chiamato")


def test_scaffold_local_planner_unavailable_returns_failed_status():
    """Modalita' seriale VRAM (degraded + serialize_vram) con swap planner
    fallito -> early return (L616-622) 'local planner unavailable' con
    status 'failed'. Il Planner NON viene chiamato."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = _make_project(tmpdir)

        def mock_local(messages, mode="reasoning", timeout=None):
            return "OK"

        orch, lp, ap = _make_orchestrator(project, mock_local)
        try:
            # ensure_models() ricalcola _degraded_mode dagli health flag
            # dell'AI client (L330): il flag va piantato DENTRO la chiamata.
            def fake_ensure_models():
                orch._degraded_mode = True
                return LauncherStatus(
                    rig_available=False, rig_host="", rig_ports=[],
                    local_running={"8000": {"name": "coder", "port": 8000,
                                            "status": "running"}},
                    model_source="local", errors=[])
            orch.ensure_models = fake_ensure_models
            orch.serialize_vram = True
            orch._check_vram_and_swap = MagicMock(return_value=False)
            orch.planner.plan_scaffold = MagicMock()

            result = orch.run_scaffold("Qualunque task", project_path=str(project))
        finally:
            lp.stop()
            ap.stop()

        assert result["success"] is False
        assert result["status"] == "failed", \
            f"early return senza status coerente col main path: {result}"
        assert result["error"] == "local planner unavailable", \
            f"diagnostica inattesa: {result}"
        orch.planner.plan_scaffold.assert_not_called()
        print("✓ Scaffold: swap planner fallito -> status failed, planner mai chiamato")


# ---------------------------------------------------------------------------
# orchestrator coverage slice 4 (2026-07-18): heal-loop edge cases
# (_scaffold_heal_loop L501-552). Bare orchestrator: basta il quality gate
# reale + coder finto (pattern di test_scaffold_heal_loop_fixes_red_suite).
# ---------------------------------------------------------------------------

def _bare_heal_orch(project: Path):
    orch = Orchestrator.__new__(Orchestrator)
    orch.project_path = str(project)
    orch._should_stop = False
    orch._heal_logs = []
    orch._log = lambda msg, level="info": orch._heal_logs.append(msg)
    return orch


def _write_red_calc_project(project: Path):
    (project / "calc.py").write_text(WRONG_IMPL)
    (project / "test_calc.py").write_text(FAILING_TEST)


def test_heal_loop_stops_mid_loop_on_user_stop():
    """_should_stop alzato DURANTE la rigenerazione: il loop interno salta i
    file restanti (L520-521) e run_loop esce 'stopped' in testa al giro
    successivo (L546 + loop_runner L83-84): budget NON consumato, ritorna
    l'ultimo gate senza raise."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        _write_red_calc_project(project)
        (project / "helper.py").write_text("def helper():\n    return 1\n")
        orch = _bare_heal_orch(project)
        calls = {"coder": 0}

        class StopCoder:
            def generate_file(self, fname, spec, project_context=""):
                calls["coder"] += 1
                orch._should_stop = True  # stop utente a meta' giro
                return WRONG_IMPL

        orch.coder = StopCoder()
        initial = orch._scaffold_quality_gate(["calc.py", "helper.py", "test_calc.py"])
        assert initial["status"] == "verified_failure"

        healed = orch._scaffold_heal_loop(
            initial, ["calc.py", "helper.py"],
            {"calc.py": "somma", "helper.py": "helper"}, "",
            max_iterations=5)

        assert calls["coder"] == 1, \
            f"helper.py non deve essere rigenerato dopo lo stop, got {calls['coder']}"
        assert (project / "helper.py").read_text() == "def helper():\n    return 1\n"
        assert healed["status"] == "verified_failure"
        assert healed is not initial, "stopped ritorna il gate del giro, non l'input"
        assert any("stopped" in m for m in orch._heal_logs)
        print("✓ Heal loop: stop utente a meta' giro -> uscita 'stopped', budget intatto")


def test_heal_loop_pre_stopped_returns_input_quality():
    """_should_stop gia' alzato prima del loop: run_loop esce a 0 iterazioni
    con last_result None -> il fallback L552 ritorna il quality dict di
    INPUT (identita'), coder mai chiamato."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        _write_red_calc_project(project)
        orch = _bare_heal_orch(project)
        orch._should_stop = True
        orch.coder = MagicMock()

        initial = orch._scaffold_quality_gate(["calc.py", "test_calc.py"])
        healed = orch._scaffold_heal_loop(
            initial, ["calc.py"], {"calc.py": "somma"}, "", max_iterations=3)

        assert healed is initial, "0 iterazioni -> ritorna il quality in input"
        orch.coder.generate_file.assert_not_called()
        assert any("stopped" in m for m in orch._heal_logs)
        print("✓ Heal loop: pre-stopped -> 0 iterazioni, quality di input, zero regen")


def test_heal_loop_exhaustion_returns_last_red_gate():
    """Budget consumato con suite ancora rossa: ritorna il gate dell'ULTIMA
    iterazione (L552, last_result) con status verified_failure e gli errori
    pytest come evidenza — senza raise."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        _write_red_calc_project(project)
        orch = _bare_heal_orch(project)
        calls = {"coder": 0}

        class StubbornCoder:
            def generate_file(self, fname, spec, project_context=""):
                calls["coder"] += 1
                return WRONG_IMPL  # sintassi ok, logica ancora sbagliata

        orch.coder = StubbornCoder()
        initial = orch._scaffold_quality_gate(["calc.py", "test_calc.py"])
        healed = orch._scaffold_heal_loop(
            initial, ["calc.py"], {"calc.py": "somma"}, "", max_iterations=2)

        assert calls["coder"] == 2, "budget interamente consumato"
        assert healed is not initial, "ritorna il gate dell'ultima iterazione"
        assert healed["status"] == "verified_failure"
        assert any("pytest failed" in e for e in healed["errors"]), \
            f"evidenza pytest attesa negli errori: {healed['errors']}"
        assert any("max_iterations" in m for m in orch._heal_logs)
        print("✓ Heal loop: esaurimento -> ultimo gate rosso come evidenza, no raise")


def test_heal_loop_rejects_broken_syntax_regen():
    """Regen con sintassi rotta (L529-530): il contenuto NON viene scritto,
    si tiene la versione precedente, il loop continua fino ad esaurimento."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        _write_red_calc_project(project)
        orch = _bare_heal_orch(project)
        calls = {"coder": 0}

        class BrokenCoder:
            def generate_file(self, fname, spec, project_context=""):
                calls["coder"] += 1
                return "def add(a, b\n    return a + b\n"  # sintassi rotta

        orch.coder = BrokenCoder()
        initial = orch._scaffold_quality_gate(["calc.py", "test_calc.py"])
        healed = orch._scaffold_heal_loop(
            initial, ["calc.py"], {"calc.py": "somma"}, "", max_iterations=2)

        assert calls["coder"] == 2, "sintassi rotta non ferma il loop, solo la scrittura"
        assert (project / "calc.py").read_text() == WRONG_IMPL, \
            "la versione precedente deve essere conservata"
        assert healed["status"] == "verified_failure"
        print("✓ Heal loop: regen con sintassi rotta scartata, file precedente tenuto")


if __name__ == "__main__":
    print("🧪 Test whole-file mode + scaffold failure wiring (coverage slice 2+3)\n")
    test_whole_file_success_updates_project_file()
    test_whole_file_empty_output_stalls_without_apply()
    test_edit_mode_selection_boundary()
    test_scaffold_gate_red_heal_exhausted_fails_without_commit()
    test_scaffold_failure_memory_negative_polarity()
    test_scaffold_empty_plan_exits_without_generation()
    test_scaffold_no_models_returns_failed_status()
    test_scaffold_local_planner_unavailable_returns_failed_status()
    print("\n🎉 Tutti i test passati!")
