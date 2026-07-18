from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_run_id(run_id: str) -> str:
    run_id = str(run_id or "").strip()
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"unsafe run_id: {run_id!r}")
    return run_id


def classify_log_event(message: str, level: str = "info") -> str:
    text = (message or "").strip()
    lower = text.lower()
    level = (level or "info").lower()

    if level in {"error", "fatal"}:
        return "error"
    if "checking model availability" in lower or "models ready" in lower:
        return "models"
    if "building context" in lower or lower.startswith("context:"):
        return "context"
    if "planner analyzing" in lower or lower.startswith("plan:") or "piano:" in lower:
        return "plan"
    if "coder generating" in lower or "creating " in lower:
        return "act"
    if "patcher applying" in lower or "patch applied" in lower:
        return "patch"
    if "runner executing" in lower or "execution successful" in lower:
        return "verify"
    if "quality gate" in lower:
        if "fallito" in lower or "failed" in lower:
            return "quality_gate_failed"
        if "superato" in lower or "passed" in lower:
            return "quality_gate_passed"
        return "quality_gate"
    if "esito strutturato memoria" in lower or "memory" in lower:
        return "memory"
    if "commit" in lower:
        return "commit"
    if "stop requested" in lower or "interrotto" in lower:
        return "stopped"
    if level in {"warning", "warn"}:
        return "warning"
    return "log"


class RunEventStore:
    """Append-only JSONL event store for Codex-like run timelines."""

    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        return self.log_dir / f"{safe_run_id(run_id)}.events.jsonl"

    def append(
        self,
        run_id: str,
        event_type: str,
        *,
        level: str = "info",
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self.path_for(run_id)
        seq = 0
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                seq = sum(1 for _ in fh)
        record = {
            "seq": seq,
            "ts": _utc_now(),
            "run_id": safe_run_id(run_id),
            "type": str(event_type or "log"),
            "level": str(level or "info"),
            "message": str(message or ""),
            "data": data or {},
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def append_log(self, run_id: str, message: str, level: str = "info") -> dict[str, Any]:
        return self.append(
            run_id,
            classify_log_event(message, level),
            level=level,
            message=message,
        )

    def list(self, run_id: str, *, after_seq: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        path = self.path_for(run_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record.setdefault("seq", line_no)
                if after_seq is not None and int(record.get("seq", line_no)) <= after_seq:
                    continue
                events.append(record)
                if len(events) >= limit:
                    break
        return events

    def start(self, run_id: str, *, mode: str, task: str, project_path: str) -> dict[str, Any]:
        return self.append(
            run_id,
            "run_started",
            level="info",
            message=f"{mode} started: {task[:200]}",
            data={"mode": mode, "task": task, "project_path": project_path},
        )

    def finish(self, run_id: str, *, status: str, mode: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"status": status}
        if mode:
            data["mode"] = mode
        return self.append(
            run_id,
            "run_finished",
            level="info" if status == "success" else "warning",
            message=f"run finished: {status}",
            data=data,
        )
