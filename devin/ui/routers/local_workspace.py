"""Authenticated bridge between the Windows desktop and a managed rig mirror.

The browser never receives a Windows path.  Tauri selects and reads the local
folder, uploads a bounded ZIP snapshot, and remembers the local path in its own
protected registry.  The backend only sees an opaque bridge id and immutable
snapshots below ``workspace/_local_mirrors``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from devin.core.change_manifest import load_change_manifest
from devin.core.project_space import ProjectSpace


router = APIRouter()

SNAPSHOT_SCHEMA = "devin_local_workspace_snapshot_v1"
EXPORT_SCHEMA = "devin_local_workspace_export_v1"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_FILES = 10_000
EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".devin", ".devin_state", ".devin_chat",
    ".devin_cache", ".venv", "venv", "env", "node_modules", "target",
    "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "logs", "models", ".ssh", ".aws", ".azure", ".gnupg",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".tmp", ".bak", ".orig", ".rej", ".gguf",
    ".pem", ".key", ".p12", ".pfx", ".kdbx",
}


class LocalWorkspaceError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bridge_id(raw: str) -> str:
    try:
        value = str(uuid.UUID(str(raw or "")))
    except (ValueError, AttributeError) as exc:
        raise LocalWorkspaceError("bridge_id non valido") from exc
    if value != str(raw or "").lower():
        raise LocalWorkspaceError("bridge_id deve essere un UUID canonico")
    return value


def _digest(raw: str, label: str = "snapshot_digest") -> str:
    value = str(raw or "").strip().lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LocalWorkspaceError(f"{label} deve essere un digest SHA-256")
    return value


def _safe_project_name(raw: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", Path(str(raw or "workspace")).name).strip(" .")
    return name[:80] or "workspace-locale"


def _safe_member(raw: str) -> PurePosixPath:
    if not raw or "\\" in raw or "\x00" in raw:
        raise LocalWorkspaceError("nome file ZIP non valido")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise LocalWorkspaceError(f"path ZIP non sicuro: {raw!r}")
    if any(":" in part or part.endswith((" ", ".")) for part in relative.parts):
        raise LocalWorkspaceError(f"path ZIP non portabile su Windows: {raw!r}")
    lowered = [part.lower() for part in relative.parts]
    name = lowered[-1]
    if any(part in EXCLUDED_PARTS for part in lowered):
        raise LocalWorkspaceError(f"contenuto escluso nello snapshot: {raw}")
    if name == ".env" or name.startswith(".env."):
        raise LocalWorkspaceError(f"file sensibile escluso dallo snapshot: {raw}")
    if name in {
        ".npmrc", ".pypirc", ".netrc", "credentials", "credentials.json",
        "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    }:
        raise LocalWorkspaceError(f"file sensibile escluso dallo snapshot: {raw}")
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        raise LocalWorkspaceError(f"tipo file escluso dallo snapshot: {raw}")
    return relative


def _validated_members(archive: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, PurePosixPath]], int]:
    files: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    expanded = 0
    for info in archive.infolist():
        relative = _safe_member(info.filename.rstrip("/"))
        key = relative.as_posix().casefold()
        if key in seen:
            raise LocalWorkspaceError(f"path duplicato nello snapshot: {relative.as_posix()}")
        seen.add(key)
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == stat.S_IFLNK:
            raise LocalWorkspaceError(f"symlink non consentito: {relative.as_posix()}")
        if info.flag_bits & 0x1:
            raise LocalWorkspaceError("archivi ZIP cifrati non consentiti")
        if info.is_dir():
            continue
        if info.file_size > MAX_FILE_BYTES:
            raise LocalWorkspaceError(f"file oltre il limite di {MAX_FILE_BYTES} byte: {relative}")
        expanded += info.file_size
        if expanded > MAX_EXPANDED_BYTES:
            raise LocalWorkspaceError("snapshot oltre il limite espanso")
        files.append((info, relative))
        if len(files) > MAX_FILES:
            raise LocalWorkspaceError("snapshot con troppi file")
    return files, expanded


def _extract_snapshot(raw: bytes, destination: Path) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise LocalWorkspaceError(f"snapshot ZIP non valido: {exc}") from exc
    with archive:
        members, expanded = _validated_members(archive)
        destination.mkdir(parents=True, exist_ok=False)
        for info, relative in members:
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("xb") as output:
                copied = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > MAX_FILE_BYTES:
                        raise LocalWorkspaceError(f"file espanso oltre il limite: {relative}")
                    output.write(chunk)
                if copied != info.file_size:
                    raise LocalWorkspaceError(f"dimensione ZIP incoerente: {relative}")
        return {"files": len(members), "bytes": expanded}


async def _read_bounded(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_ARCHIVE_BYTES:
                raise LocalWorkspaceError("snapshot compresso oltre il limite")
        except ValueError as exc:
            raise LocalWorkspaceError("Content-Length non valido") from exc
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise LocalWorkspaceError("snapshot compresso oltre il limite")
    if not payload:
        raise LocalWorkspaceError("snapshot vuoto")
    return bytes(payload)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _project_for_snapshot(workspace: Path, display_name: str, bridge: str, project_path: str) -> Path:
    from devin.ui.fast_app import _validated_project_path

    if project_path:
        project = Path(_validated_project_path(project_path, allow_general=False)).resolve()
        if project.parent != workspace.resolve() or project.name.startswith(("_", ".")):
            raise LocalWorkspaceError("il bridge locale richiede un progetto gestito nel workspace DEVIN")
        return project

    base = workspace / _safe_project_name(display_name)
    candidates = [base, workspace / f"{base.name}-{bridge[:8]}"]
    for candidate in candidates:
        metadata = candidate / ".devin" / "local_workspace.json"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate.resolve()
        try:
            existing = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("bridge_id") == bridge:
            return candidate.resolve()
    raise LocalWorkspaceError("esiste gia' un progetto con lo stesso nome")


def _public_metadata(project: Path) -> dict[str, Any] | None:
    path = project / ".devin" / "local_workspace.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        return None
    return {
        "schema": SNAPSHOT_SCHEMA,
        "bridge_id": payload.get("bridge_id"),
        "display_name": payload.get("display_name"),
        "snapshot_digest": payload.get("snapshot_digest"),
        "files": payload.get("files", 0),
        "bytes": payload.get("bytes", 0),
        "excluded_entries": payload.get("excluded_entries", 0),
        "updated_at": payload.get("updated_at"),
    }


def local_workspace_for_project(project_path: str | Path) -> dict[str, Any] | None:
    return _public_metadata(Path(project_path).expanduser().resolve())


@router.post("/api/local-workspace/snapshot")
async def api_local_workspace_snapshot(
    request: Request,
    bridge_id: str,
    display_name: str,
    snapshot_digest: str,
    project_path: str = "",
    excluded_entries: int = 0,
):
    """Accept one bounded immutable snapshot from the trusted Tauri command."""
    from devin.ui.fast_app import WORKSPACE_DIR, active_runs, runs_lock, starting_runs

    try:
        bridge = _bridge_id(bridge_id)
        digest = _digest(snapshot_digest)
        if excluded_entries < 0 or excluded_entries > MAX_FILES:
            raise LocalWorkspaceError("conteggio esclusioni non valido")
        raw = await _read_bounded(request)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != digest:
            raise LocalWorkspaceError("digest dello snapshot non corrispondente")
        with runs_lock:
            if active_runs or starting_runs:
                raise LocalWorkspaceError(
                    "sincronizzazione sospesa mentre un run e' attivo; attendi la conclusione"
                )

        mirror_root = (WORKSPACE_DIR / "_local_mirrors" / bridge).resolve()
        snapshots = mirror_root / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        version = uuid.uuid4().hex[:12]
        destination = snapshots / f"{digest[:20]}-{version}"
        stage = snapshots / f".{digest}.{uuid.uuid4().hex}.tmp"
        try:
            counts = _extract_snapshot(raw, stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

        project = _project_for_snapshot(WORKSPACE_DIR, display_name, bridge, project_path)
        ProjectSpace(str(project)).set_work_dir(str(destination))
        metadata = {
            "schema": SNAPSHOT_SCHEMA,
            "bridge_id": bridge,
            "display_name": _safe_project_name(display_name),
            "snapshot_digest": digest,
            "files": counts["files"],
            "bytes": counts["bytes"],
            "excluded_entries": excluded_entries,
            "updated_at": _utc_now(),
        }
        _write_json_atomic(project / ".devin" / "local_workspace.json", metadata)
        return {
            "status": "synced",
            "project_path": str(project),
            "local_workspace": metadata,
        }
    except (LocalWorkspaceError, zipfile.BadZipFile, OSError) as exc:
        return {"error": str(exc)}


def _file_fingerprint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise LocalWorkspaceError(f"target export non sicuro: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "sha256": digest.hexdigest(),
        "size": path.stat().st_size,
        "mode": stat.S_IMODE(path.stat().st_mode),
    }


@router.get("/api/local-workspace/export/{run_id}")
async def api_local_workspace_export(
    run_id: str,
    project_path: str,
    entry_digest: str,
):
    """Export an already-approved manifest without exposing backend paths."""
    from devin.ui.fast_app import _validated_project_path

    try:
        project = Path(_validated_project_path(project_path, allow_general=False)).resolve()
        bridge = local_workspace_for_project(project)
        if not bridge:
            raise LocalWorkspaceError("il progetto non e' collegato a un workspace locale")
        work_dir_raw = ProjectSpace(str(project)).get_work_dir()
        work_dir = Path(_validated_project_path(work_dir_raw, allow_general=False)).resolve()
        manifest = load_change_manifest(
            work_dir, run_id, expected_entry_digest=_digest(entry_digest, "entry_digest")
        )
        decision_status = manifest.get("status")
        if decision_status not in {"applied", "rolled_back"}:
            raise LocalWorkspaceError("il manifest non e' stato applicato o ripristinato sul mirror")

        entries = manifest["entries"]
        if decision_status == "rolled_back":
            inverse = {"create": "delete", "delete": "create", "modify": "modify"}
            entries = [
                {
                    **entry,
                    "operation": inverse[entry["operation"]],
                    "before": entry.get("after"),
                    "after": entry.get("before"),
                }
                for entry in manifest["entries"]
            ]

        exported = {
            "schema": EXPORT_SCHEMA,
            "bridge_id": bridge["bridge_id"],
            "run_id": run_id,
            "entry_digest": manifest["entry_digest"],
            "decision_status": decision_status,
            "entries": entries,
            "created_at": _utc_now(),
        }
        output = io.BytesIO()
        total = 0
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(
                "bridge-manifest.json",
                json.dumps(exported, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
            for entry in entries:
                if entry["operation"] == "delete":
                    continue
                relative = _safe_member(entry["path"])
                source = work_dir.joinpath(*relative.parts)
                if _file_fingerprint(source) != entry.get("after"):
                    raise LocalWorkspaceError(f"mirror modificato dopo l'approvazione: {relative}")
                total += source.stat().st_size
                if total > MAX_EXPANDED_BYTES:
                    raise LocalWorkspaceError("export oltre il limite")
                archive.write(source, f"files/{relative.as_posix()}")
        raw = output.getvalue()
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise LocalWorkspaceError("export compresso oltre il limite")
        return Response(
            content=raw,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="devin-{run_id}.zip"',
                "X-Devin-Bridge-Schema": EXPORT_SCHEMA,
            },
        )
    except (LocalWorkspaceError, ValueError, RuntimeError, OSError) as exc:
        return Response(
            content=json.dumps({"error": str(exc)}),
            status_code=409,
            media_type="application/json",
        )
