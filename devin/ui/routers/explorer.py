"""Router explorer: file browsing/lettura/salvataggio (audit #8/#10/#15/#26).

Secondo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

La guardia anti-traversal `_safe_under_allowed` RESTA in fast_app (condivisa
da projects/workspace/chat/runs/training e importata dai test di sicurezza):
qui e' importata lazy dentro gli handler — import top-level da fast_app
creerebbe un circolo (fast_app include questo router; e un eventuale import
diretto del router per primo renderebbe il circolo fatale). `_ALLOWED_ROOTS`
resta un singleton di fast_app: l'identita' del set e' preservata perche'
i test lo mutano (test_security_regressions).
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


# Cockpit editor (C3.1): API separate dal Monaco storico. Il browser riceve
# solo path relativi al work_dir autorizzato e non dispone di alcuna write.
PROJECT_TREE_MAX_FILES = 1500
PROJECT_TREE_MAX_WALK = 30000
PROJECT_TREE_MAX_DEPTH = 12
PROJECT_FILE_MAX_BYTES = 256 * 1024
PROJECT_TREE_SKIP_DIRS = frozenset({
    ".git", ".devin", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".idea", ".vscode", ".aws", ".gnupg", ".ssh",
    "target", "dist", "build",
    "coverage", "htmlcov", "logs", "memory_backups",
})
PROJECT_TEXT_EXTENSIONS = frozenset({
    ".bat", ".c", ".cc", ".cfg", ".conf", ".cpp", ".cs", ".css",
    ".csv", ".dockerfile", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".kt", ".less", ".lua", ".md",
    ".mjs", ".ps1", ".py", ".pyi", ".rb", ".rs", ".rst", ".sass",
    ".scss", ".sh", ".sql", ".svelte", ".toml", ".ts", ".tsx", ".txt",
    ".vue", ".xml", ".yaml", ".yml",
})
PROJECT_TEXT_FILENAMES = frozenset({
    "dockerfile", "gemfile", "license", "makefile", "procfile", "readme",
})
PROJECT_SENSITIVE_FILENAMES = frozenset({
    ".env", ".envrc", ".netrc", ".npmrc", ".pypirc", "credentials",
    "credentials.json", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa",
    "secrets.json",
})
PROJECT_SENSITIVE_SUFFIXES = frozenset({".kdbx", ".key", ".p12", ".pem", ".pfx"})


def _is_sensitive_project_path(relative_path: Path | PurePosixPath) -> bool:
    """Keep common secrets out of both the tree and the read endpoint."""
    for part in relative_path.parts:
        lowered = part.lower()
        if lowered in PROJECT_SENSITIVE_FILENAMES or lowered.startswith(".env."):
            return True
        if Path(lowered).suffix in PROJECT_SENSITIVE_SUFFIXES:
            return True
    return False


def _is_probably_text_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in PROJECT_TEXT_EXTENSIONS
        or name in PROJECT_TEXT_FILENAMES
        or any(name.startswith(f"{base}.") for base in PROJECT_TEXT_FILENAMES)
    )


def _resolve_project_execution_root(project_path: str) -> tuple[Path, str]:
    """Resolve project metadata first, then its separately validated work_dir."""
    from devin.core.project_space import ProjectSpace
    from devin.ui.fast_app import _validated_project_path  # lazy: no cycle

    validated_project = _validated_project_path(project_path, allow_general=False)
    try:
        project_root = Path(validated_project).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="progetto non disponibile") from exc
    work_dir = (ProjectSpace(str(project_root)).get_work_dir() or "").strip()
    if not work_dir:
        return project_root, "project"
    validated_work_dir = _validated_project_path(work_dir, allow_general=False)
    try:
        return Path(validated_work_dir).resolve(strict=True), "work_dir"
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="cartella di lavoro non disponibile") from exc


def _scan_project_tree(
    root: Path,
    *,
    max_files: int = PROJECT_TREE_MAX_FILES,
    max_walk: int = PROJECT_TREE_MAX_WALK,
    max_depth: int = PROJECT_TREE_MAX_DEPTH,
) -> tuple[list[dict], bool]:
    """Bounded, non-following scan returning relative metadata only."""
    root = root.resolve(strict=True)
    files: list[dict] = []
    walked = 0
    truncated = False

    for current, directories, filenames in os.walk(root, followlinks=False):
        walked += 1
        if walked > max_walk:
            truncated = True
            break
        current_path = Path(current)
        try:
            relative_dir = current_path.relative_to(root)
        except ValueError:
            continue
        depth = len(relative_dir.parts)
        if depth >= max_depth:
            if directories:
                truncated = True
            directories[:] = []
        else:
            directories[:] = sorted(
                name for name in directories
                if name.lower() not in PROJECT_TREE_SKIP_DIRS
                and not (current_path / name).is_symlink()
            )

        for filename in sorted(filenames):
            walked += 1
            if walked > max_walk:
                truncated = True
                directories[:] = []
                break
            item = current_path / filename
            if item.is_symlink():
                continue
            try:
                relative = item.relative_to(root)
            except ValueError:
                continue
            if _is_sensitive_project_path(relative):
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            if not item.is_file():
                continue
            files.append({
                "name": item.name,
                "path": relative.as_posix(),
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "is_text": _is_probably_text_path(item),
            })
            if len(files) >= max_files:
                truncated = True
                directories[:] = []
                break
        if truncated and (walked > max_walk or len(files) >= max_files):
            break

    files.sort(key=lambda entry: entry["path"].lower())
    return files, truncated


def _safe_project_relative_file(root: Path, relative_path: str) -> tuple[Path, str]:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw or (len(raw) >= 2 and raw[1] == ":"):
        raise HTTPException(status_code=400, detail="path relativo non valido")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise HTTPException(status_code=400, detail="path relativo non valido")
    if _is_sensitive_project_path(pure):
        raise HTTPException(status_code=403, detail="file sensibile non leggibile dal cockpit")

    root = root.resolve(strict=True)
    try:
        target = root.joinpath(*pure.parts).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="file non trovato") from exc
    if target == root or root not in target.parents:
        raise HTTPException(status_code=403, detail="file fuori dalla cartella di lavoro")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file non trovato")
    return target, pure.as_posix()


def _read_project_text_file(path: Path, max_bytes: int = PROJECT_FILE_MAX_BYTES) -> tuple[str, bool, int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if b"\x00" in payload:
        raise HTTPException(status_code=415, detail="file binario non visualizzabile")
    truncated = len(payload) > max_bytes
    payload = payload[:max_bytes]
    try:
        content = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # A valid UTF-8 code point may straddle the byte cap. Drop only that
        # incomplete tail; decoding errors elsewhere still fail closed.
        if truncated and exc.start >= len(payload) - 4:
            content = payload[:exc.start].decode("utf-8-sig")
        else:
            raise HTTPException(status_code=415, detail="file non UTF-8 non visualizzabile") from exc
    return content, truncated, size


@router.get("/api/project/tree")
async def api_project_tree(project_path: str = ""):
    """Read-only tree for the selected project's authorized execution root."""
    root, scope = _resolve_project_execution_root(project_path)
    files, truncated = await asyncio.to_thread(_scan_project_tree, root)
    return {
        "scope": scope,
        "root_name": root.name,
        "files": files,
        "count": len(files),
        "truncated": truncated,
        "limits": {
            "max_files": PROJECT_TREE_MAX_FILES,
            "max_depth": PROJECT_TREE_MAX_DEPTH,
        },
    }


@router.get("/api/project/file")
async def api_project_file(project_path: str = "", path: str = ""):
    """Bounded text read; deliberately has no project-scoped save sibling."""
    root, scope = _resolve_project_execution_root(project_path)
    target, relative = _safe_project_relative_file(root, path)
    content, truncated, size = await asyncio.to_thread(_read_project_text_file, target)
    return {
        "scope": scope,
        "path": relative,
        "content": content,
        "language": target.suffix.lstrip(".").lower() or "text",
        "size": size,
        "bytes_read": len(content.encode("utf-8")),
        "truncated": truncated,
        "read_only": True,
    }


def _scan_project_files(project_path: str, max_files: int = 2000, max_walk: int = 50000) -> list:
    """Scansiona i file di un progetto per il file explorer.

    #15 audit: prima faceva sorted(path.rglob('*')) materializzando l'INTERO
    albero (una cartella con venv/node_modules a mano puo' avere 100k+ voci →
    secondi di blocco e MB di JSON). Ora: iterazione senza sort anticipato, tetto
    duro sull'attraversamento (max_walk) e cap sui file restituiti (max_files),
    sort solo sul risultato gia' limitato. Chiamata via asyncio.to_thread dagli
    endpoint async (non blocca l'event loop)."""
    path = Path(project_path).expanduser()
    if not path.exists() or not path.is_dir():
        return []

    files = []
    walked = 0
    try:
        for item in path.rglob("*"):
            walked += 1
            if walked > max_walk:
                print(f"[Explorer] cap attraversamento ({max_walk}) raggiunto in {path}")
                break
            if not item.is_file():
                continue
            if any(p.startswith(".") or p in ("__pycache__", "venv", ".venv", "node_modules") for p in item.parts):
                continue
            try:
                st = item.stat()
            except OSError:
                continue
            rel = item.relative_to(path)
            files.append({
                "name": item.name,
                "path": str(rel),
                "full_path": str(item),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "is_python": item.suffix == ".py",
                "is_text": item.suffix in (".py", ".json", ".yaml", ".yml", ".txt", ".md", ".sh", ".bat")
            })
            if len(files) >= max_files:
                print(f"[Explorer] cap file ({max_files}) raggiunto in {path}")
                break
    except Exception as e:
        print(f"[Explorer] Error scanning {path}: {e}")

    files.sort(key=lambda f: f["path"])
    return files


def _read_file_content(file_path: str, max_chars: int = 10000) -> str:
    """Legge il contenuto di un file di testo."""
    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        return ""

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n# [...file truncated...]"
        return content
    except Exception:
        return "# [Error reading file]"


@router.get("/api/explore")
async def api_explore(path: str = ""):
    """Esplora file di un progetto."""
    from devin.ui.fast_app import _safe_under_allowed  # lazy: no import circolare
    if not path:
        return {"error": "missing path"}

    safe = _safe_under_allowed(path)
    if safe is None:
        return {"error": "path non consentito: solo progetti in workspace/ o cartelle collegate dal picker"}

    # #10/#15: scansione in thread — su alberi grandi bloccava l'event loop
    files = await asyncio.to_thread(_scan_project_files, str(safe))
    return {
        "path": path,
        "files": files,
        "count": len(files)
    }


@router.get("/api/file")
async def api_file(path: str = ""):
    """Legge contenuto di un file."""
    from devin.ui.fast_app import _safe_under_allowed  # lazy: no import circolare
    if not path:
        return {"error": "missing path"}

    safe = _safe_under_allowed(path)
    if safe is None:
        return {"error": "path non consentito: solo progetti in workspace/ o cartelle collegate dal picker"}

    content = _read_file_content(str(safe))
    return {
        "path": path,
        "content": content,
        "language": Path(path).suffix.lstrip(".") or "text"
    }


@router.post("/api/file/save")
async def api_file_save(request: Request):
    """#26 audit: salvataggio REALE dall'editor Monaco (prima il bottone 💾 era
    un alert 'non implementato'). Scrittura ATOMICA (temp + replace) con backup
    .bak della versione precedente. Path validato dalla stessa guardia di #8:
    solo dentro workspace/ o cartelle collegate dal picker."""
    from devin.ui.fast_app import _safe_under_allowed  # lazy: no import circolare
    data = await request.json()
    safe = _safe_under_allowed(data.get("path", ""))
    if safe is None:
        return {"error": "path non consentito: solo file in workspace/ o cartelle collegate"}
    content = data.get("content", "")

    def _write():
        if safe.exists():
            try:
                safe.with_suffix(safe.suffix + ".bak").write_bytes(safe.read_bytes())
            except Exception:
                pass  # il backup è best-effort, non deve bloccare il salvataggio
        tmp = safe.with_suffix(safe.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(safe)  # atomico: nessun file mezzo-scritto se qualcosa va storto

    try:
        await asyncio.to_thread(_write)
        return {"status": "saved", "path": str(safe), "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"error": f"salvataggio fallito: {e}"}
