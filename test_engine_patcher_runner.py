"""
Regression tests for devin/engine/patcher.py and devin/engine/runner.py —
the two modules that touch the filesystem and spawn processes.

patcher: path-traversal guards (diff and whole-file modes), diff structure
validation, strict/fuzzy Python fallback semantics, end-to-end apply.
runner: entrypoint discovery heuristics, exit-code mapping, hard timeout
(process-group kill), Runner.stop() safety.

Runs real subprocesses on tiny temp projects; no network, no GPU, no models.
"""
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from devin.engine.patcher import (
    _safe_target_path,
    _validate_patch_paths,
    _validate_diff_structure,
    _clean_patch_text,
    _parse_hunks,
    _apply_diff_python,
    _apply_diff_python_fuzzy,
    apply_patch,
    Patcher,
)
from devin.engine.runner import run_project, Runner, _find_likely_entrypoint


MODIFY_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

NEW_FILE_DIFF = """diff --git a/new_mod.py b/new_mod.py
--- /dev/null
+++ b/new_mod.py
@@ -0,0 +1,2 @@
+def hello():
+    return "hi"
"""


# ------------------------------------------------------------- path safety

class TestPathSafety:
    def test_safe_relative_path_accepted(self, tmp_path):
        target = _safe_target_path(tmp_path, "src/mod.py")
        assert target == (tmp_path / "src/mod.py").resolve()

    @pytest.mark.parametrize("bad", [
        "/etc/passwd",
        "../outside.py",
        "sub/../../outside.py",
        "",
        None,
    ])
    def test_unsafe_paths_rejected(self, tmp_path, bad):
        with pytest.raises(ValueError):
            _safe_target_path(tmp_path, bad)

    def test_validate_patch_paths_rejects_traversal(self, tmp_path):
        evil = MODIFY_DIFF.replace("a/calc.py", "a/../evil.py").replace(
            "b/calc.py", "b/../evil.py")
        with pytest.raises(ValueError):
            _validate_patch_paths(evil, tmp_path)

    def test_validate_patch_paths_requires_a_path(self, tmp_path):
        with pytest.raises(ValueError):
            _validate_patch_paths("@@ -1 +1 @@\n-x\n+y\n", tmp_path)

    def test_apply_patch_traversal_returns_error_not_exception(self, tmp_path):
        evil = MODIFY_DIFF.replace("a/calc.py", "a/../evil.py").replace(
            "b/calc.py", "b/../evil.py")
        result = apply_patch(evil, tmp_path)
        assert result["success"] is False
        assert "Unsafe" in result["error"]


# ------------------------------------------------------ structure validation

class TestDiffStructure:
    def test_malformed_hunk_line_reported(self):
        bad = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\nno-prefix-here\n"
        assert _validate_diff_structure(bad) is not None

    def test_valid_diff_passes(self):
        assert _validate_diff_structure(_clean_patch_text(MODIFY_DIFF)) is None

    def test_clean_strips_markdown_fences(self):
        fenced = "```diff\n" + MODIFY_DIFF + "```\n"
        cleaned = _clean_patch_text(fenced)
        assert "```" not in cleaned
        assert cleaned.startswith("diff --git")

    def test_empty_patch_rejected(self, tmp_path):
        assert apply_patch("", tmp_path)["success"] is False
        assert apply_patch("   \n  ", tmp_path)["success"] is False

    def test_malformed_patch_rejected(self, tmp_path):
        # Il cleaner scarta le righe non prefissate PRIMA della validazione
        # strutturale, quindi un hunk corrotto arriva ai tool come diff vuoto:
        # il contratto osservabile e' success=False con errore, mai eccezione.
        bad = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\nno-prefix\n"
        result = apply_patch(bad, tmp_path)
        assert result["success"] is False
        assert result["error"]


# ---------------------------------------------------------------- hunks

class TestParseHunks:
    def test_headers_not_swallowed_as_content(self):
        hunks, new_files = _parse_hunks(MODIFY_DIFF)
        assert len(hunks) == 1
        filepath, hunk = hunks[0]
        assert filepath == "calc.py"
        # nessuna riga header ('--- a/...', '+++ b/...') dentro l'hunk
        assert not any(l.startswith("-- ") or l.startswith("++ ") for l in hunk)
        assert new_files == set()

    def test_new_file_detected_from_dev_null_header(self):
        hunks, new_files = _parse_hunks(NEW_FILE_DIFF)
        assert new_files == {"new_mod.py"}
        assert hunks[0][0] == "new_mod.py"


# ------------------------------------------------------- python fallbacks

class TestPythonFallbacks:
    def test_strict_applies_exact_match(self, tmp_path):
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        result = _apply_diff_python(MODIFY_DIFF, tmp_path)
        assert result["success"] is True
        assert "return a + b" in (tmp_path / "calc.py").read_text()

    def test_strict_creates_new_file(self, tmp_path):
        result = _apply_diff_python(NEW_FILE_DIFF, tmp_path)
        assert result["success"] is True
        assert (tmp_path / "new_mod.py").read_text().startswith("def hello")

    def test_strict_refuses_contextless_additions_on_existing_file(self, tmp_path):
        """Pure '+' hunks with zero context on an existing file must FAIL
        (the old code inserted them blindly at the top of the file)."""
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        no_ctx = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@
+import os
"""
        result = _apply_diff_python(no_ctx, tmp_path)
        assert result["success"] is False
        assert "import os" not in (tmp_path / "calc.py").read_text()

    def test_strict_reports_failed_hunk_indices_for_fuzzy_retry(self, tmp_path):
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        drifted = MODIFY_DIFF.replace("    return a - b", "    return a * b")
        result = _apply_diff_python(drifted, tmp_path)
        assert result["success"] is False
        assert result["failed_hunks"] == [0]

    def test_fuzzy_single_line_substring_replacement(self, tmp_path):
        (tmp_path / "calc.py").write_text(
            "def add(a, b):\n    return a - b  # BUG\n")
        diff = MODIFY_DIFF.replace("    return a - b\n", "    return a - b  # BUG\n")
        result = _apply_diff_python_fuzzy(diff, tmp_path)
        assert result["success"] is True
        assert "return a + b" in (tmp_path / "calc.py").read_text()

    def test_fuzzy_only_files_processes_selected_hunks(self, tmp_path):
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        result = _apply_diff_python_fuzzy(MODIFY_DIFF, tmp_path, only_files=set())
        assert result["applied"] == 0
        assert "return a - b" in (tmp_path / "calc.py").read_text()


# ------------------------------------------------------- apply_patch end2end

class TestApplyPatch:
    def test_modify_existing_file(self, tmp_path):
        (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        result = apply_patch(MODIFY_DIFF, tmp_path)
        assert result["success"] is True, result
        assert "return a + b" in (tmp_path / "calc.py").read_text()

    def test_create_new_file(self, tmp_path):
        result = apply_patch(NEW_FILE_DIFF, tmp_path)
        assert result["success"] is True, result
        assert (tmp_path / "new_mod.py").exists()

    def test_unappliable_patch_fails_cleanly(self, tmp_path):
        (tmp_path / "calc.py").write_text("TOTALLY UNRELATED CONTENT\n")
        result = apply_patch(MODIFY_DIFF, tmp_path)
        assert result["success"] is False
        assert "calc.py" in result.get("error", "") or "failed" in result.get("error", "").lower()


class TestWholeFile:
    def test_apply_full_files_writes_and_rejects_traversal(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "a.py").write_text("x = 1\n")
        patcher = Patcher()
        sandbox = patcher.apply_full_files(
            {"b.py": "y = 2", "sub/c.py": "z = 3"},
            str(project), sandbox_root=str(tmp_path / "sandbox"))
        assert (sandbox / "b.py").read_text() == "y = 2\n"
        assert (sandbox / "sub/c.py").exists()

        with pytest.raises(RuntimeError, match="non sicuro"):
            patcher.apply_full_files({"../evil.py": "x"}, str(project),
                                     sandbox_root=str(tmp_path / "sandbox"))

    def test_apply_full_files_empty_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            Patcher().apply_full_files({}, str(tmp_path),
                                       sandbox_root=str(tmp_path / "sb"))


# ------------------------------------------------------------------ runner

def _write(path: Path, name: str, body: str):
    (path / name).write_text(textwrap.dedent(body))


class TestRunProject:
    def test_explicit_entrypoint_success(self, tmp_path):
        _write(tmp_path, "main.py", 'print("hello from main")\n')
        proc = run_project(tmp_path, entrypoint="main.py", timeout=10)
        assert proc.returncode == 0
        assert "hello from main" in proc.stdout

    def test_nonzero_exit_captured(self, tmp_path):
        _write(tmp_path, "main.py", 'import sys; sys.stderr.write("boom\\n"); sys.exit(3)\n')
        proc = run_project(tmp_path, entrypoint="main.py", timeout=10)
        assert proc.returncode == 3
        assert "boom" in proc.stderr

    def test_single_py_file_used_without_entrypoint(self, tmp_path):
        _write(tmp_path, "only.py", 'print("solo")\n')
        proc = run_project(tmp_path, timeout=10)
        assert proc.returncode == 0
        assert "solo" in proc.stdout

    def test_main_guard_heuristic_with_two_files(self, tmp_path):
        _write(tmp_path, "logic.py", "def f():\n    return 1\n")
        _write(tmp_path, "app.py", 'if __name__ == "__main__":\n    print("guarded")\n')
        found = _find_likely_entrypoint(tmp_path)
        assert len(found) == 1 and found[0].name == "app.py"
        proc = run_project(tmp_path, timeout=10)
        assert proc.returncode == 0
        assert "guarded" in proc.stdout

    def test_ambiguous_files_report_no_entrypoint(self, tmp_path):
        _write(tmp_path, "a.py", "x = 1\n")
        _write(tmp_path, "b.py", "y = 2\n")
        proc = run_project(tmp_path, timeout=10)
        assert proc.returncode == 1
        assert "no entrypoint" in proc.stderr

    def test_timeout_kills_process_with_124(self, tmp_path):
        _write(tmp_path, "main.py", "import time; time.sleep(60)\n")
        proc = run_project(tmp_path, entrypoint="main.py", timeout=2)
        assert proc.returncode == 124
        assert "Timeout" in proc.stderr


class TestRunnerClass:
    def test_run_maps_to_result(self, tmp_path):
        _write(tmp_path, "main.py", 'print("ok")\n')
        result = Runner(timeout=10).run(str(tmp_path), entrypoint="main.py")
        assert result.success is True
        assert result.error == ""

    def test_run_failure_maps_stderr(self, tmp_path):
        _write(tmp_path, "main.py", 'import sys; sys.stderr.write("nope\\n"); sys.exit(1)\n')
        result = Runner(timeout=10).run(str(tmp_path), entrypoint="main.py")
        assert result.success is False
        assert "nope" in result.error

    def test_stop_without_process_is_noop(self):
        Runner(timeout=10).stop()  # non deve sollevare

    def test_auto_install_defaults_off(self, monkeypatch):
        monkeypatch.delenv("DEVIN_AUTO_INSTALL_DEPS", raising=False)
        assert Runner().auto_install is False
        monkeypatch.setenv("DEVIN_AUTO_INSTALL_DEPS", "1")
        assert Runner().auto_install is True
