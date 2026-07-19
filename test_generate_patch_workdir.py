"""Regression tests for chat generate-patch work-dir routing."""

import asyncio

from devin.core.chat_persistence import ChatPersistence
from devin.ui import fast_app
from devin.ui.routers import chat as chat_router


class _InlineThread:
    def __init__(self, target, daemon=True):
        self.target = target

    def start(self):
        self.target()


class _Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def _run_generate_patch(monkeypatch, tmp_path, work_dir):
    project = tmp_path / "devin-project"
    project.mkdir()
    (project / "placeholder.py").write_text("VALUE = 1\n", encoding="utf-8")
    ChatPersistence(str(project)).save([
        {"role": "user", "content": "Correggi il progetto"},
    ])

    if work_dir is not None:
        work_dir.mkdir()
        (work_dir / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    captured = {}

    class _Orchestrator:
        def __init__(self, config_path, project_path, sse_callback):
            captured["init_path"] = project_path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_from_conversation(self, conversation_text, project_path, run_id):
            captured["run_path"] = project_path
            captured["conversation"] = conversation_text
            return {"success": True, "status": "success"}

    class _ProjectSpace:
        def __init__(self, path):
            captured["metadata_path"] = path

        def get_work_dir(self):
            return str(work_dir) if work_dir is not None else ""

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    allowed = {str(project.resolve())}
    if work_dir is not None:
        allowed.add(str(work_dir.resolve()))

    def _validate(path, allow_general=False):
        resolved = str(type(project)(path).resolve())
        if resolved not in allowed:
            raise AssertionError(f"unexpected path validation: {resolved}")
        return resolved

    monkeypatch.setattr(fast_app, "LOG_DIR", log_dir)
    monkeypatch.setattr(fast_app, "ProjectSpace", _ProjectSpace)
    monkeypatch.setattr(fast_app, "Orchestrator", _Orchestrator)
    monkeypatch.setattr(fast_app, "_validated_project_path", _validate)
    monkeypatch.setattr(fast_app, "_make_run_callback", lambda *a, **k: None)
    monkeypatch.setattr(chat_router.threading, "Thread", _InlineThread)

    response = asyncio.run(chat_router.api_chat_generate_patch(
        _Request({"project_path": str(project)})))
    return response, captured, project


def test_generate_patch_routes_execution_to_linked_work_dir(monkeypatch, tmp_path):
    work_dir = tmp_path / "linked-source"
    response, captured, project = _run_generate_patch(
        monkeypatch, tmp_path, work_dir)

    assert response["status"] == "started"
    assert response["mode"] == "patch"
    assert captured["metadata_path"] == str(project.resolve())
    assert captured["init_path"] == str(work_dir.resolve())
    assert captured["run_path"] == str(work_dir.resolve())
    assert "Correggi il progetto" in captured["conversation"]


def test_generate_patch_preserves_project_path_without_work_dir(monkeypatch, tmp_path):
    response, captured, project = _run_generate_patch(
        monkeypatch, tmp_path, None)

    assert response["status"] == "started"
    assert response["mode"] == "patch"
    assert captured["init_path"] == str(project.resolve())
    assert captured["run_path"] == str(project.resolve())
