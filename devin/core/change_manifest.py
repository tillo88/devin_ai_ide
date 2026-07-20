"""Deterministic, reviewable promotion of verified sandbox changes.

The candidate code runs in a sandbox.  This module is the trust boundary that
describes the resulting changes, detects a stale source tree, applies an
approved manifest, and keeps enough evidence to roll the application back.
"""

from __future__ import annotations

import hashlib
import difflib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from contextlib import contextmanager


SCHEMA = "change_manifest_v1"
EXCLUDED_PARTS = {
    "workspace", "venv", ".venv", "env", ".git", "__pycache__",
    ".pytest_cache", "node_modules", "dist", "build", "logs",
    ".devin", ".devin_chat", ".devin_cache", ".devin_state",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".rej", ".orig", ".tmp", ".bak", ".gguf"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


class ChangeManifestError(RuntimeError):
    """Raised when a pending change set is invalid, stale, or unsafe."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not value or len(value) > 128 or not all(c.isalnum() or c in "_-" for c in value):
        raise ChangeManifestError(f"unsafe run_id: {run_id!r}")
    return value


def _safe_target(root: Path, relative_path: str) -> Path:
    raw = str(relative_path or "").strip()
    if not raw or Path(raw).is_absolute():
        raise ChangeManifestError(f"unsafe relative path: {relative_path!r}")
    target = (root / raw).resolve()
    if target == root or root not in target.parents:
        raise ChangeManifestError(f"path escapes project: {relative_path!r}")
    return target


def _included(relative: Path) -> bool:
    name = relative.name.lower()
    return (
        not any(part in EXCLUDED_PARTS for part in relative.parts)
        and relative.suffix.lower() not in EXCLUDED_SUFFIXES
        and relative.suffix.lower() not in SENSITIVE_SUFFIXES
        and name != ".env"
        and not name.startswith(".env.")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _files(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    # Prune runtime/vendor trees before descending: a project venv or model
    # directory can contain millions of files and must not affect review time.
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(directories):
            path = current_path / name
            relative = path.relative_to(root)
            if not _included(relative):
                continue
            if path.is_symlink():
                raise ChangeManifestError(
                    f"symlinks are not promotable: {relative.as_posix()}"
                )
            kept_directories.append(name)
        directories[:] = kept_directories

        for name in sorted(filenames):
            path = current_path / name
            relative = path.relative_to(root)
            if not _included(relative):
                continue
            if path.is_symlink():
                raise ChangeManifestError(
                    f"symlinks are not promotable: {relative.as_posix()}"
                )
            if not path.is_file():
                continue
            key = relative.as_posix()
            result[key] = {
                "sha256": _sha256(path),
                "size": path.stat().st_size,
                "mode": _mode(path),
            }
    return result


def _manifest_dir(project: Path, run_id: str) -> Path:
    return project / ".devin_state" / "pending_changes" / _safe_run_id(run_id)


def manifest_path(project_path: str | Path, run_id: str) -> Path:
    project = Path(project_path).expanduser().resolve()
    return _manifest_dir(project, run_id) / "manifest.json"


def _canonical_digest(entries: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(list(entries), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def _decision_lock(project: Path, run_id: str):
    """Serialize apply/reject/rollback across processes; kernel releases on crash."""
    import fcntl

    path = _manifest_dir(project, run_id) / "decision.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ChangeManifestError("another decision is already in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def build_change_manifest(
    project_path: str | Path,
    sandbox_path: str | Path,
    run_id: str,
) -> dict[str, Any]:
    """Compare source and verified sandbox and persist a deterministic manifest."""
    project = Path(project_path).expanduser().resolve()
    sandbox = Path(sandbox_path).expanduser().resolve()
    if not project.is_dir() or not sandbox.is_dir():
        raise ChangeManifestError("project and sandbox must be existing directories")
    if project == sandbox:
        raise ChangeManifestError("sandbox must differ from project")

    before = _files(project)
    after = _files(sandbox)
    entries: list[dict[str, Any]] = []
    for relative in sorted(set(before) | set(after)):
        old = before.get(relative)
        new = after.get(relative)
        if old == new:
            continue
        if old is None:
            operation = "create"
        elif new is None:
            operation = "delete"
        else:
            operation = "modify"
        entries.append({
            "path": relative,
            "operation": operation,
            "before": old,
            "after": new,
        })

    counts = {name: sum(1 for item in entries if item["operation"] == name)
              for name in ("create", "modify", "delete")}
    manifest = {
        "schema": SCHEMA,
        "run_id": _safe_run_id(run_id),
        "status": "pending",
        "created_at": _utc_now(),
        "project_path": str(project),
        "sandbox_path": str(sandbox),
        "entries": entries,
        "counts": counts,
        "entry_digest": _canonical_digest(entries),
    }
    _write_json_atomic(manifest_path(project, run_id), manifest)
    return manifest


def load_change_manifest(project_path: str | Path, run_id: str) -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    path = manifest_path(project, run_id)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChangeManifestError(f"pending manifest not found for {run_id}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ChangeManifestError(f"invalid manifest for {run_id}: {exc}") from exc
    if manifest.get("schema") != SCHEMA or manifest.get("run_id") != _safe_run_id(run_id):
        raise ChangeManifestError("manifest identity/schema mismatch")
    if Path(str(manifest.get("project_path", ""))).resolve() != project:
        raise ChangeManifestError("manifest belongs to another project")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or manifest.get("entry_digest") != _canonical_digest(entries):
        raise ChangeManifestError("manifest entries failed integrity check")
    return manifest


def preview_change_manifest(
    project_path: str | Path,
    run_id: str,
    *,
    max_file_bytes: int = 256_000,
    max_diff_chars: int = 500_000,
) -> dict[str, Any]:
    """Return a bounded unified diff for a still-pending verified manifest."""
    project = Path(project_path).expanduser().resolve()
    manifest = load_change_manifest(project, run_id)
    sandbox = _assert_pending_inputs(project, manifest)
    chunks: list[str] = []
    previews: list[dict[str, Any]] = []
    remaining = max(1, int(max_diff_chars))
    truncated = False
    for entry in manifest["entries"]:
        relative = entry["path"]
        before_path = _safe_target(project, relative)
        after_path = _safe_target(sandbox, relative)
        before_bytes = before_path.read_bytes() if before_path.exists() else b""
        after_bytes = after_path.read_bytes() if after_path.exists() else b""
        binary = (
            len(before_bytes) > max_file_bytes
            or len(after_bytes) > max_file_bytes
            or b"\x00" in before_bytes
            or b"\x00" in after_bytes
        )
        diff_text = ""
        if not binary:
            try:
                before_text = before_bytes.decode("utf-8").splitlines(keepends=True)
                after_text = after_bytes.decode("utf-8").splitlines(keepends=True)
                diff_text = "".join(difflib.unified_diff(
                    before_text,
                    after_text,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                ))
            except UnicodeDecodeError:
                binary = True
        if binary:
            diff_text = (
                f"--- a/{relative}\n+++ b/{relative}\n"
                f"Binary file changed ({entry['operation']})\n"
            )
        if len(diff_text) > remaining:
            diff_text = diff_text[:remaining] + "\n... diff preview truncated ...\n"
            truncated = True
        chunks.append(diff_text)
        previews.append({
            "path": relative,
            "operation": entry["operation"],
            "binary": binary,
            "before_size": (entry.get("before") or {}).get("size", 0),
            "after_size": (entry.get("after") or {}).get("size", 0),
        })
        remaining -= len(diff_text)
        if remaining <= 0:
            truncated = True
            break
    return {
        "schema": manifest["schema"],
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "counts": manifest["counts"],
        "entries": previews,
        "unified_diff": "".join(chunks),
        "truncated": truncated,
        "entry_digest": manifest["entry_digest"],
    }


def _fingerprint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ChangeManifestError(f"unsafe/non-file target: {path}")
    return {"sha256": _sha256(path), "size": path.stat().st_size, "mode": _mode(path)}


def _assert_pending_inputs(project: Path, manifest: dict[str, Any]) -> Path:
    if manifest.get("status") != "pending":
        raise ChangeManifestError(f"manifest is {manifest.get('status')}, not pending")
    sandbox = Path(str(manifest.get("sandbox_path", ""))).resolve()
    if not sandbox.is_dir() or sandbox == project:
        raise ChangeManifestError("verified sandbox is missing or unsafe")
    for entry in manifest["entries"]:
        relative = entry.get("path")
        operation = entry.get("operation")
        if operation not in {"create", "modify", "delete"}:
            raise ChangeManifestError(f"unknown operation for {relative!r}")
        target = _safe_target(project, relative)
        source = _safe_target(sandbox, relative)
        if _fingerprint(target) != entry.get("before"):
            raise ChangeManifestError(f"source changed after verification: {relative}")
        expected_after = entry.get("after")
        actual_after = _fingerprint(source)
        if actual_after != expected_after:
            raise ChangeManifestError(f"sandbox changed after verification: {relative}")
    return sandbox


def apply_change_manifest(project_path: str | Path, run_id: str) -> dict[str, Any]:
    """Apply one approved manifest with stale checks and rollback-on-error."""
    project = Path(project_path).expanduser().resolve()
    with _decision_lock(project, run_id):
        manifest = load_change_manifest(project, run_id)
        sandbox = _assert_pending_inputs(project, manifest)
        base_dir = _manifest_dir(project, run_id)
        backup_dir = base_dir / "backup"
        rollback_dir = base_dir / "failed_apply"
        completed: list[dict[str, Any]] = []
        try:
            for entry in manifest["entries"]:
                relative = entry["path"]
                target = _safe_target(project, relative)
                source = _safe_target(sandbox, relative)
                backup = _safe_target(backup_dir.resolve(), relative)
                if target.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                if entry["operation"] == "delete":
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.name}.devin-{_safe_run_id(run_id)}.tmp")
                    shutil.copy2(source, temporary)
                    os.replace(temporary, target)
                completed.append(entry)
        except Exception as exc:
            rollback_dir.mkdir(parents=True, exist_ok=True)
            for entry in reversed(completed):
                target = _safe_target(project, entry["path"])
                backup = _safe_target(backup_dir.resolve(), entry["path"])
                try:
                    if backup.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(backup, target)
                    elif target.exists():
                        failed = _safe_target(rollback_dir.resolve(), entry["path"])
                        failed.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(target, failed)
                except OSError:
                    pass
            raise ChangeManifestError(f"apply failed and was rolled back: {exc}") from exc

        manifest["status"] = "applied"
        manifest["applied_at"] = _utc_now()
        manifest["backup_path"] = str(backup_dir)
        _write_json_atomic(manifest_path(project, run_id), manifest)
        return manifest


def reject_change_manifest(project_path: str | Path, run_id: str) -> dict[str, Any]:
    project = Path(project_path).expanduser().resolve()
    with _decision_lock(project, run_id):
        manifest = load_change_manifest(project, run_id)
        if manifest.get("status") != "pending":
            raise ChangeManifestError(f"manifest is {manifest.get('status')}, not pending")
        manifest["status"] = "rejected"
        manifest["rejected_at"] = _utc_now()
        _write_json_atomic(manifest_path(project, run_id), manifest)
        return manifest


def rollback_change_manifest(project_path: str | Path, run_id: str) -> dict[str, Any]:
    """Restore an applied manifest, refusing to overwrite later user changes."""
    project = Path(project_path).expanduser().resolve()
    with _decision_lock(project, run_id):
        manifest = load_change_manifest(project, run_id)
        if manifest.get("status") != "applied":
            raise ChangeManifestError(f"manifest is {manifest.get('status')}, not applied")
        backup_dir = Path(str(manifest.get("backup_path", ""))).resolve()
        displaced_dir = _manifest_dir(project, run_id) / "rollback_displaced"
        for entry in manifest["entries"]:
            target = _safe_target(project, entry["path"])
            if _fingerprint(target) != entry.get("after"):
                raise ChangeManifestError(f"project changed after apply: {entry['path']}")
            backup = _safe_target(backup_dir, entry["path"])
            if entry.get("before") is not None and _fingerprint(backup) != entry.get("before"):
                raise ChangeManifestError(f"backup missing or corrupt: {entry['path']}")

        for entry in reversed(manifest["entries"]):
            target = _safe_target(project, entry["path"])
            backup = _safe_target(backup_dir, entry["path"])
            if entry.get("before") is None:
                displaced = _safe_target(displaced_dir.resolve(), entry["path"])
                displaced.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, displaced)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
        manifest["status"] = "rolled_back"
        manifest["rolled_back_at"] = _utc_now()
        _write_json_atomic(manifest_path(project, run_id), manifest)
        return manifest
