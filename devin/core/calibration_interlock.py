"""Fail-closed admission gate for AI Rig calibration windows.

The calibration controller owns a small JSON file in ``/run/ai-rig``.  When
that file is active, the DEVIN backend rejects new model-consuming chat and
Goal requests, while already accepted work is allowed to drain.  A read-only
status endpoint reports when no chat stream, accepted Goal, starting run or
active run remains, so the external controller can stop/change the model only
when ``safe_to_stop_model`` is true.

This module deliberately does not stop services or models and does not create
or remove the interlock file.  Those operations belong to the privileged AI
Rig calibration controller.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from starlette.responses import JSONResponse

INTERLOCK_SCHEMA = "calibration_interlock_v1"
STATUS_SCHEMA = "calibration_interlock_status_v1"
INTERLOCK_PATH_ENV = "DEVIN_CALIBRATION_INTERLOCK_FILE"
DEFAULT_INTERLOCK_PATH = "/run/ai-rig/calibration-interlock.json"
MAX_INTERLOCK_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 64 * 1024

STATUS_PATH = "/api/calibration/interlock"

# Exact mutating/model-consuming admissions.  Read-only history, diagnostics,
# health and stop/recovery endpoints remain available while the gate is armed.
PROTECTED_POST_PATHS = frozenset(
    {
        "/api/chat",
        "/api/chat/vision",
        "/api/chat/document",
        "/api/run",
        "/api/run/resume",
        "/api/chat/scaffold",
        "/api/chat/generate_patch",
    }
)

_ACCEPTED_GOAL_STATUSES = frozenset({"started", "queued", "running", "resumed"})
_TERMINAL_RUN_STATUSES = frozenset(
    {
        "success",
        "verified_success",
        "syntax_only",
        "failed",
        "timeout",
        "stopped",
        "stalled",
        "awaiting_approval",
        "applied_uncommitted",
        "rejected",
        "rolled_back",
    }
)
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STATUS_RE = re.compile(r"(?im)^status:\s*([a-z_]+)\s*$")


@dataclass(frozen=True)
class InterlockState:
    path: str
    status: str
    blocked: bool
    valid: bool
    active: bool
    reason: str = ""
    owner: str = ""
    calibration_run_id: str = ""
    created_at: str = ""
    error: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_interlock_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = str(env.get(INTERLOCK_PATH_ENV, "") or "").strip()
    return Path(configured or DEFAULT_INTERLOCK_PATH)


def _invalid_state(path: Path, error: str) -> InterlockState:
    # Invalid/unreadable state blocks admissions, but never claims that it is
    # safe to stop the model.  An operator must repair or remove the file.
    return InterlockState(
        path=str(path),
        status="invalid_fail_closed",
        blocked=True,
        valid=False,
        active=False,
        error=error,
    )


def read_interlock(path: str | Path | None = None) -> InterlockState:
    target = Path(path) if path is not None else resolve_interlock_path()
    try:
        stat = target.lstat()
    except FileNotFoundError:
        return InterlockState(
            path=str(target),
            status="open",
            blocked=False,
            valid=True,
            active=False,
        )
    except OSError as exc:
        return _invalid_state(target, f"cannot stat interlock: {exc}")

    if target.is_symlink():
        return _invalid_state(target, "interlock must not be a symlink")
    if not target.is_file():
        return _invalid_state(target, "interlock is not a regular file")
    if stat.st_size > MAX_INTERLOCK_BYTES:
        return _invalid_state(target, "interlock exceeds size limit")

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _invalid_state(target, f"cannot read valid JSON: {exc}")

    if not isinstance(payload, dict):
        return _invalid_state(target, "interlock payload must be an object")
    if payload.get("schema") != INTERLOCK_SCHEMA:
        return _invalid_state(target, "interlock schema mismatch")
    if not isinstance(payload.get("active"), bool):
        return _invalid_state(target, "interlock active must be boolean")

    active = payload["active"]
    return InterlockState(
        path=str(target),
        status="active" if active else "inactive",
        blocked=active,
        valid=True,
        active=active,
        reason=str(payload.get("reason") or "")[:500],
        owner=str(payload.get("owner") or "")[:200],
        calibration_run_id=str(payload.get("calibration_run_id") or "")[:200],
        created_at=str(payload.get("created_at") or "")[:100],
    )


class AdmissionRegistry:
    """Process-local accounting for accepted work that may still use the model."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chat_requests = 0
        self._goal_requests = 0
        self._pending_goal_ids: set[str] = set()

    def begin(self, kind: str) -> None:
        with self._lock:
            if kind == "chat":
                self._chat_requests += 1
            else:
                self._goal_requests += 1

    def finish(self, kind: str) -> None:
        with self._lock:
            if kind == "chat":
                self._chat_requests = max(0, self._chat_requests - 1)
            else:
                self._goal_requests = max(0, self._goal_requests - 1)

    def register_pending_goal(self, run_id: str) -> None:
        value = str(run_id or "").strip()
        if not _RUN_ID_RE.fullmatch(value):
            return
        with self._lock:
            self._pending_goal_ids.add(value)

    def reconcile_pending(
        self,
        *,
        starting_run_ids: set[str],
        active_run_ids: set[str],
        terminal_check,
    ) -> list[str]:
        with self._lock:
            candidates = set(self._pending_goal_ids)

        removable = set()
        for run_id in candidates:
            # Keep the acceptance bridge for the whole run.  starting/active
            # prove that work is alive; only terminal log evidence may clear it.
            if run_id in starting_run_ids or run_id in active_run_ids:
                continue
            try:
                if terminal_check(run_id):
                    removable.add(run_id)
            except Exception:
                # Unknown state remains pending: drain reporting is fail-closed.
                pass

        if removable:
            with self._lock:
                self._pending_goal_ids.difference_update(removable)

        with self._lock:
            return sorted(self._pending_goal_ids)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_chat_requests": self._chat_requests,
                "goal_admissions_in_progress": self._goal_requests,
                "pending_goal_ids": sorted(self._pending_goal_ids),
            }


_registry = AdmissionRegistry()


class _AdmissionLease:
    def __init__(self, registry: AdmissionRegistry, kind: str) -> None:
        self.registry = registry
        self.kind = kind
        self.released = False
        registry.begin(kind)

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.registry.finish(self.kind)


def _terminal_log_checker(log_dir: Path):
    def check(run_id: str) -> bool:
        if not _RUN_ID_RE.fullmatch(run_id):
            return False
        path = (log_dir / f"{run_id}.log").resolve()
        if log_dir.resolve() not in path.parents or not path.is_file():
            return False
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > MAX_CAPTURE_BYTES:
                handle.seek(size - MAX_CAPTURE_BYTES)
            tail = handle.read(MAX_CAPTURE_BYTES).decode("utf-8", errors="ignore")
        statuses = _STATUS_RE.findall(tail)
        return bool(statuses and statuses[-1].lower() in _TERMINAL_RUN_STATUSES)

    return check


def runtime_status_payload() -> dict[str, Any]:
    state = read_interlock()
    runtime_ok = True
    runtime_error = ""
    starting_ids: set[str] = set()
    active_ids: set[str] = set()
    log_dir: Path | None = None

    try:
        from devin.ui import fast_app

        with fast_app.runs_lock:
            starting_ids = set(fast_app.starting_runs)
            active_ids = set(fast_app.active_runs)
        log_dir = Path(fast_app.LOG_DIR)
    except Exception as exc:
        runtime_ok = False
        runtime_error = str(exc)

    if runtime_ok and log_dir is not None:
        pending_ids = _registry.reconcile_pending(
            starting_run_ids=starting_ids,
            active_run_ids=active_ids,
            terminal_check=_terminal_log_checker(log_dir),
        )
    else:
        pending_ids = _registry.snapshot()["pending_goal_ids"]

    activity = _registry.snapshot()
    activity.update(
        {
            "starting_run_ids": sorted(starting_ids),
            "active_run_ids": sorted(active_ids),
            "pending_goal_ids": pending_ids,
            "runtime_snapshot_ok": runtime_ok,
            "runtime_snapshot_error": runtime_error,
        }
    )

    drained = bool(
        runtime_ok
        and activity["active_chat_requests"] == 0
        and activity["goal_admissions_in_progress"] == 0
        and not activity["pending_goal_ids"]
        and not activity["starting_run_ids"]
        and not activity["active_run_ids"]
    )
    safe_to_stop = bool(state.valid and state.active and drained)

    return {
        "schema": STATUS_SCHEMA,
        "interlock": state.public_dict(),
        "activity": activity,
        "drained": drained,
        "safe_to_stop_model": safe_to_stop,
    }


def _blocked_response(state: InterlockState, request_kind: str) -> JSONResponse:
    code = (
        "calibration_interlock_active"
        if state.valid and state.active
        else "calibration_interlock_invalid_fail_closed"
    )
    return JSONResponse(
        {
            "error": "new chat and Goal work is temporarily blocked for calibration",
            "code": code,
            "retryable": True,
            "request_kind": request_kind,
            "interlock": state.public_dict(),
        },
        status_code=423,
        headers={"Cache-Control": "no-store"},
    )


def _accepted_goal_from_response(content_type: str, body: bytes) -> str:
    if "application/json" not in content_type.lower() or not body:
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    run_id = str(payload.get("run_id") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    if status not in _ACCEPTED_GOAL_STATUSES or not _RUN_ID_RE.fullmatch(run_id):
        return ""
    return run_id


class CalibrationInterlockMiddleware:
    """Pure ASGI admission/drain accounting; safe for SSE responses."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")

        if method == "GET" and path == STATUS_PATH:
            response = JSONResponse(
                runtime_status_payload(),
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        if method != "POST" or path not in PROTECTED_POST_PATHS:
            await self.app(scope, receive, send)
            return

        state = read_interlock()
        request_kind = "chat" if path in {
            "/api/chat", "/api/chat/vision", "/api/chat/document"
        } else "goal"
        if state.blocked:
            await _blocked_response(state, request_kind)(scope, receive, send)
            return

        lease = _AdmissionLease(_registry, request_kind)
        status_code = 200
        content_type = ""
        captured = bytearray()

        async def wrapped_send(message):
            nonlocal status_code, content_type
            message_type = message.get("type")
            if message_type == "http.response.start":
                status_code = int(message.get("status", 200))
                for raw_name, raw_value in message.get("headers", []):
                    if raw_name.lower() == b"content-type":
                        content_type = raw_value.decode("latin-1", errors="ignore")
            elif message_type == "http.response.body":
                chunk = message.get("body", b"") or b""
                if len(captured) < MAX_CAPTURE_BYTES:
                    captured.extend(chunk[: MAX_CAPTURE_BYTES - len(captured)])
                if not message.get("more_body", False):
                    if status_code < 400:
                        run_id = _accepted_goal_from_response(
                            content_type, bytes(captured)
                        )
                        if run_id:
                            _registry.register_pending_goal(run_id)
                    lease.release()
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            lease.release()
