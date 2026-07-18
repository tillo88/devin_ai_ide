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
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()


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
