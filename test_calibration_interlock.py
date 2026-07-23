from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

from devin.core import calibration_interlock as ci
from devin.ui import token_gate


def _scope(method: str, path: str):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 5000),
    }


async def _invoke(app, method: str, path: str, body: bytes = b""):
    received = False
    messages = []

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(_scope(method, path), receive, send)
    status = next(
        item["status"] for item in messages if item["type"] == "http.response.start"
    )
    response_body = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    return status, response_body, messages


def _write_lock(path: Path, *, active=True):
    path.write_text(
        json.dumps(
            {
                "schema": ci.INTERLOCK_SCHEMA,
                "active": active,
                "reason": "formal KVarN calibration",
                "owner": "ai-rig-calibration",
                "calibration_run_id": "kvarn-formal-1",
                "created_at": "2026-07-24T00:00:00+02:00",
            }
        ),
        encoding="utf-8",
    )


def _fake_runtime(monkeypatch, tmp_path, training_jobs=None):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    fast_app = SimpleNamespace(
        runs_lock=threading.Lock(),
        starting_runs=set(),
        active_runs={},
        LOG_DIR=log_dir,
    )
    jobs = [] if training_jobs is None else training_jobs
    training = SimpleNamespace(_training_job_snapshot=lambda: list(jobs))
    monkeypatch.setitem(sys.modules, "devin.ui.fast_app", fast_app)
    monkeypatch.setitem(sys.modules, "devin.ui.routers.training", training)
    return fast_app, jobs


@pytest.fixture(autouse=True)
def _isolated_registry_and_path(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "_registry", ci.AdmissionRegistry())
    monkeypatch.setenv(ci.INTERLOCK_PATH_ENV, str(tmp_path / "interlock.json"))
    _fake_runtime(monkeypatch, tmp_path)


def test_interlock_absent_active_inactive_and_corrupt(tmp_path):
    path = tmp_path / "interlock.json"

    opened = ci.read_interlock(path)
    assert opened.status == "open"
    assert opened.valid is True
    assert opened.blocked is False

    _write_lock(path, active=True)
    active = ci.read_interlock(path)
    assert active.status == "active"
    assert active.valid is True
    assert active.blocked is True
    assert active.calibration_run_id == "kvarn-formal-1"

    _write_lock(path, active=False)
    inactive = ci.read_interlock(path)
    assert inactive.status == "inactive"
    assert inactive.blocked is False

    path.write_text("{broken", encoding="utf-8")
    corrupt = ci.read_interlock(path)
    assert corrupt.status == "invalid_fail_closed"
    assert corrupt.valid is False
    assert corrupt.blocked is True


def test_active_interlock_blocks_only_new_model_work(tmp_path):
    path = tmp_path / "interlock.json"
    _write_lock(path)

    async def app(scope, receive, send):
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = ci.CalibrationInterlockMiddleware(app)

    for protected_path in ("/api/chat", "/api/run", "/api/training/run"):
        blocked_status, blocked_body, _ = asyncio.run(
            _invoke(middleware, "POST", protected_path)
        )
        assert blocked_status == 423
        blocked = json.loads(blocked_body)
        assert blocked["code"] == "calibration_interlock_active"
        assert blocked["retryable"] is True

    stop_status, stop_body, _ = asyncio.run(
        _invoke(middleware, "POST", "/api/stop")
    )
    assert stop_status == 200
    assert json.loads(stop_body) == {"ok": True}


def test_corrupt_interlock_blocks_but_never_reports_safe(tmp_path):
    path = tmp_path / "interlock.json"
    path.write_text("not json", encoding="utf-8")

    payload = ci.runtime_status_payload()

    assert payload["interlock"]["status"] == "invalid_fail_closed"
    assert payload["drained"] is True
    assert payload["safe_to_stop_model"] is False


def test_sse_chat_is_counted_until_final_body():
    stream_started = asyncio.Event()
    release_stream = asyncio.Event()

    async def streaming_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"data: one\n\n",
                "more_body": True,
            }
        )
        stream_started.set()
        await release_stream.wait()
        await send(
            {
                "type": "http.response.body",
                "body": b"data: done\n\n",
                "more_body": False,
            }
        )

    middleware = ci.CalibrationInterlockMiddleware(streaming_app)

    async def scenario():
        task = asyncio.create_task(_invoke(middleware, "POST", "/api/chat"))
        await stream_started.wait()
        assert ci._registry.snapshot()["active_chat_requests"] == 1
        release_stream.set()
        await task
        assert ci._registry.snapshot()["active_chat_requests"] == 0

    asyncio.run(scenario())


def test_goal_response_requires_terminal_evidence_before_drain(
    tmp_path, monkeypatch
):
    fast_app, _ = _fake_runtime(monkeypatch, tmp_path)
    log_dir = fast_app.LOG_DIR

    async def goal_app(scope, receive, send):
        await JSONResponse({"run_id": "run_goal_1", "status": "started"})(
            scope, receive, send
        )

    middleware = ci.CalibrationInterlockMiddleware(goal_app)
    status, _, _ = asyncio.run(_invoke(middleware, "POST", "/api/run"))
    assert status == 200

    pending = ci.runtime_status_payload()
    assert pending["activity"]["pending_goal_ids"] == ["run_goal_1"]
    assert pending["drained"] is False

    # starting/active visibility never clears the acceptance bridge: only a
    # terminal footer may do it.
    fast_app.starting_runs.add("run_goal_1")
    starting = ci.runtime_status_payload()
    assert starting["activity"]["pending_goal_ids"] == ["run_goal_1"]
    assert starting["activity"]["starting_run_ids"] == ["run_goal_1"]
    assert starting["drained"] is False

    fast_app.starting_runs.clear()
    (log_dir / "run_goal_1.log").write_text(
        "Run started\nstatus: success\n", encoding="utf-8"
    )
    _write_lock(tmp_path / "interlock.json")
    drained = ci.runtime_status_payload()
    assert drained["activity"]["pending_goal_ids"] == []
    assert drained["drained"] is True
    assert drained["safe_to_stop_model"] is True


def test_training_queue_is_part_of_drain(tmp_path, monkeypatch):
    jobs = [{"job_id": "training_1", "status": "queued"}]
    _fake_runtime(monkeypatch, tmp_path, training_jobs=jobs)
    _write_lock(tmp_path / "interlock.json")

    busy = ci.runtime_status_payload()
    assert busy["activity"]["active_training_job_ids"] == ["training_1"]
    assert busy["drained"] is False
    assert busy["safe_to_stop_model"] is False

    jobs[0]["status"] = "finished"
    drained = ci.runtime_status_payload()
    assert drained["activity"]["active_training_job_ids"] == []
    assert drained["drained"] is True
    assert drained["safe_to_stop_model"] is True


def test_fast_app_token_gate_keeps_interlock_active_when_token_auth_is_disabled(
    tmp_path, monkeypatch
):
    _write_lock(tmp_path / "interlock.json")
    monkeypatch.setattr(token_gate, "resolve_api_token", lambda: "")

    async def app(scope, receive, send):
        await JSONResponse({"unexpected": True})(scope, receive, send)

    middleware = token_gate.TokenGateMiddleware(app)
    status, body, _ = asyncio.run(_invoke(middleware, "POST", "/api/run"))

    assert status == 423
    assert json.loads(body)["code"] == "calibration_interlock_active"
