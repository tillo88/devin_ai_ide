"""Regression tests for chat generate-patch work-dir routing."""

import asyncio
from pathlib import Path

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


class _FakeRunEvents:
    def __init__(self):
        self.started, self.finished = [], []

    def start(self, run_id, **kwargs):
        self.started.append((run_id, kwargs))

    def finish(self, run_id, **kwargs):
        self.finished.append((run_id, kwargs))


def _run_generate_patch(monkeypatch, tmp_path, work_dir, empty=False, scaffold_result=None, extra=None):
    project = tmp_path / "devin-project"
    project.mkdir()
    if not empty:
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
            captured["auto_apply"] = getattr(self, "auto_apply_verified", None)
            captured["conversation"] = conversation_text
            return {"success": True, "status": "success"}

        def run_scaffold(self, task, project_path, run_id):
            captured["scaffold_path"] = project_path
            captured["auto_apply"] = getattr(self, "auto_apply_verified", None)
            return scaffold_result

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
    events = _FakeRunEvents()
    captured["events"] = events
    monkeypatch.setattr(fast_app, "_run_events", events)
    captured["log_dir"] = log_dir

    response = asyncio.run(chat_router.api_chat_generate_patch(
        _Request({"project_path": str(project), **(extra or {})})))
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


def test_build_from_chat_scaffold_awaiting_approval_is_not_failed_and_tells_the_chat(monkeypatch, tmp_path):
    result = {"success": False, "verified": True, "status": "awaiting_approval",
              "files_written": ["scadenze.py", "test_scadenze.py", "README.md"],
              "files_failed": [], "total_planned": 3,
              "quality_gate": {"status": "verified_success", "app_check": {
                  "stato": "riuscita", "passi": [{"esito": "ok", "nome": "a"}, {"esito": "ok", "nome": "b"}]}}}
    response, captured, project = _run_generate_patch(
        monkeypatch, tmp_path, None, empty=True, scaffold_result=result)
    assert response["mode"] == "scaffold"
    log = (captured["log_dir"] / f"{response['run_id']}.log").read_text(encoding="utf-8")
    assert "status: awaiting_approval" in log
    assert "status: failed" not in log
    events = captured["events"]
    assert events.started and events.started[0][1]["mode"] == "chat_scaffold"
    run_id, finish = events.finished[0]
    assert finish["status"] == "awaiting_approval"
    message = finish["summary"]["chat_message"]
    assert "3/3 file" in message and "`test_scadenze.py`" in message and "Applica" in message
    assert "app provata: 2/2 controlli ok" in message
    history = ChatPersistence(str(project)).load()
    assert history[-1]["role"] == "assistant" and history[-1]["content"] == message


def test_build_from_chat_failure_names_the_file_and_the_reason(monkeypatch, tmp_path):
    result = {"success": False, "status": "failed", "files_written": ["a.py"],
              "files_failed": [{"filename": "test_a.py", "error": "tetto di 12288 token esaurito"}],
              "total_planned": 2, "quality_gate": {"status": "syntax_only"}}
    response, captured, _ = _run_generate_patch(
        monkeypatch, tmp_path, None, empty=True, scaffold_result=result)
    _, finish = captured["events"].finished[0]
    assert finish["status"] == "failed"
    message = finish["summary"]["chat_message"]
    assert "Non riuscito" in message and "`test_a.py`" in message and "tetto di 12288" in message


def test_build_from_chat_patch_mode_also_finishes_the_run(monkeypatch, tmp_path):
    response, captured, _ = _run_generate_patch(monkeypatch, tmp_path, None)
    _, finish = captured["events"].finished[0]
    assert finish["status"] == "success" and finish["mode"] == "chat_patch"


def test_build_from_chat_applies_by_default_and_can_ask_first(monkeypatch, tmp_path):
    (tmp_path / "a").mkdir()
    response, captured, _ = _run_generate_patch(monkeypatch, tmp_path / "a", None)
    assert response["applica"] == "auto" and captured["auto_apply"] is True
    (tmp_path / "b").mkdir()
    response, captured, _ = _run_generate_patch(monkeypatch, tmp_path / "b", None,
                                                extra={"applica": "revisione"})
    assert response["applica"] == "revisione" and captured["auto_apply"] is False
    (tmp_path / "c").mkdir()
    response, _, _ = _run_generate_patch(monkeypatch, tmp_path / "c", None, extra={"applica": "boh"})
    assert "applica deve essere" in response["error"]


def test_with_the_agent_an_existing_project_also_goes_through_the_sandbox_build(monkeypatch, tmp_path):
    result = {"success": True, "status": "verified_success", "files_written": ["placeholder.py"],
              "files_failed": [], "total_planned": 1, "quality_gate": {"status": "verified_success"}}

    class Flag:
        agent_mode = True

    project = tmp_path / "p"
    project.mkdir()
    (project / "placeholder.py").write_text("VALUE = 1\n", encoding="utf-8")
    ChatPersistence(str(project)).save([{"role": "user", "content": "Aggiungi una funzione"}])
    seen = {}

    class _Orch(Flag):
        def __init__(self, config_path, project_path, sse_callback):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run_scaffold(self, task, project_path, run_id):
            seen["task"] = task
            return result

        def run_from_conversation(self, *a, **k):
            raise AssertionError("con l'agente non si usa la catena patch")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(fast_app, "LOG_DIR", log_dir)
    monkeypatch.setattr(fast_app, "Orchestrator", _Orch)
    monkeypatch.setattr(fast_app, "ProjectSpace", lambda path: type("PS", (), {"get_work_dir": lambda self: ""})())
    monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: str(Path(p).resolve()))
    monkeypatch.setattr(fast_app, "_make_run_callback", lambda *a, **k: None)
    monkeypatch.setattr(fast_app, "_run_events", _FakeRunEvents())
    monkeypatch.setattr(chat_router.threading, "Thread", _InlineThread)
    response = asyncio.run(chat_router.api_chat_generate_patch(_Request({"project_path": str(project)})))
    assert response["mode"] == "patch"
    assert seen["task"].startswith("Applica al progetto esistente")
