import subprocess

from devin.engine.git_ops import GitOps


def _git(project, *args):
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True
    ).stdout


def test_commit_excludes_runtime_state_and_run_sandboxes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "user.name", "Test")
    (project / "app.py").write_text("before = True\n", encoding="utf-8")
    _git(project, "add", "app.py")
    _git(project, "commit", "-m", "base")

    (project / "app.py").write_text("after = True\n", encoding="utf-8")
    state = project / ".devin_state" / "pending_changes" / "run_x"
    state.mkdir(parents=True)
    (state / "manifest.json").write_text("{}", encoding="utf-8")
    sandbox = project / "workspace" / "sandboxes" / "run_x"
    sandbox.mkdir(parents=True)
    (sandbox / "candidate.py").write_text("unsafe = True\n", encoding="utf-8")

    result = GitOps(str(project)).commit("patch", "approved")

    assert result["success"] is True, result
    names = _git(project, "show", "--pretty=format:", "--name-only", "HEAD").splitlines()
    assert names == ["app.py"]
    status = _git(project, "status", "--short")
    assert ".devin_state/" in status
    assert "workspace/" in status

    repeated = GitOps(str(project)).commit("patch", "approved again")
    assert repeated["success"] is True
    assert repeated["no_changes"] is True
