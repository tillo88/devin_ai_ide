import tempfile
import time
from pathlib import Path

import pytest

from devin.core.orchestrator import Orchestrator, _safe_project_target
from devin.ui.fast_app import (
    _validated_project_path,
    _register_allowed_root, _safe_under_allowed, _ALLOWED_ROOTS,
)
# _validate_public_url vive nel router projects (split plan fetta 11): la
# guardia SSRF si e' spostata con gli handler from_url/crawl che la usano.
from devin.ui.routers.projects import _validate_public_url
from devin.engine.patcher import apply_patch
from devin.engine.runner import Runner


def test_new_file_patch_is_not_applied_twice():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        patch = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+print(1)
+print(2)
"""
        result = apply_patch(patch, root)
        assert result["success"], result
        assert (root / "new.py").read_text() == "print(1)\nprint(2)\n"


def test_patch_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "sandbox"
        root.mkdir()
        patch = """diff --git a/../escaped.txt b/../escaped.txt
new file mode 100644
--- /dev/null
+++ b/../escaped.txt
@@ -0,0 +1 @@
+escaped
"""
        result = apply_patch(patch, root)
        assert not result["success"]
        assert "Unsafe patch path" in result["error"]
        assert not (root.parent / "escaped.txt").exists()


def test_scaffold_target_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError):
            _safe_project_target(tmpdir, "../escaped.py")


def test_api_project_path_rejects_arbitrary_filesystem_locations():
    with pytest.raises(Exception) as exc_info:
        _validated_project_path("/etc", allow_general=False)
    assert getattr(exc_info.value, "status_code", None) == 403


def test_knowledge_url_rejects_loopback():
    with pytest.raises(ValueError):
        _validate_public_url("http://127.0.0.1/private")


def test_register_allowed_root_accepts_real_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir).resolve()
        child = root / "sub" / "file.txt"
        child.parent.mkdir()
        child.write_text("ok")
        try:
            assert _register_allowed_root(str(root)) is True
            assert root in _ALLOWED_ROOTS
            # Un file sotto la root registrata deve passare il gate
            assert _safe_under_allowed(str(child)) == child.resolve()
        finally:
            _ALLOWED_ROOTS.discard(root)


def test_register_allowed_root_rejects_missing_directory_loudly(capsys):
    """Una root inesistente deve fallire ESPLICITAMENTE (return False + log),
    non sparire in silenzio lasciando l'utente con 403 inspiegabili."""
    missing = Path(tempfile.gettempdir()) / "devin-definitely-missing-dir-12345"
    assert not missing.exists()
    result = _register_allowed_root(str(missing))
    assert result is False
    assert missing.resolve() not in _ALLOWED_ROOTS
    assert _safe_under_allowed(str(missing / "file.txt")) is None
    out = capsys.readouterr().out
    assert "[SECURITY]" in out and "NON registrata" in out


def test_runner_enforces_timeout():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text(
            "import time\nwhile True:\n    time.sleep(0.1)\n"
        )
        started = time.monotonic()
        result = Runner(timeout=1).run(str(root))
        elapsed = time.monotonic() - started
        assert not result.success
        assert "Timeout" in result.error
        assert elapsed < 5


def test_sync_copies_non_python_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        sandbox = base / "sandbox"
        project = base / "project"
        sandbox.mkdir()
        project.mkdir()
        (sandbox / "index.html").write_text("<h1>updated</h1>")
        Orchestrator._sync_sandbox_to_project(
            object(), str(sandbox), str(project)
        )
        assert (project / "index.html").read_text() == "<h1>updated</h1>"
