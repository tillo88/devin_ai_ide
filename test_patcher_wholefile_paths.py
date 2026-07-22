"""apply_full_files must accept ABSOLUTE paths under the project (models often
emit them) by normalizing to sandbox-relative, and still reject paths outside
the project. This was the real 'percorso non sicuro fuori dal sandbox' failure
that killed maintenance runs. 2026-07-22.
"""
import pytest

from devin.engine.patcher import Patcher


def _project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return proj


def test_absolute_paths_under_project_are_written(tmp_path):
    proj = _project(tmp_path)
    p = Patcher()
    # Model emitted ABSOLUTE paths pointing at the project (the failure case).
    files = {
        str(proj / "is_prime.py"): "def is_prime(n):\n    return n > 1\n",
        "test_is_prime.py": "import is_prime\n",  # relative still works
    }
    sandbox = p.apply_full_files(files, str(proj), sandbox_root="workspace/sandboxes/run_test")
    assert (sandbox / "is_prime.py").exists()
    assert (sandbox / "test_is_prime.py").exists()
    assert "is_prime" in (sandbox / "is_prime.py").read_text(encoding="utf-8")


def test_absolute_path_outside_project_is_rejected(tmp_path):
    proj = _project(tmp_path)
    p = Patcher()
    outside = tmp_path / "elsewhere" / "evil.py"
    with pytest.raises(RuntimeError, match="fuori dal progetto"):
        p.apply_full_files({str(outside): "x = 1\n"}, str(proj),
                           sandbox_root="workspace/sandboxes/run_test2")
