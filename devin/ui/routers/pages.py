"""Router pages: pagine HTML Jinja (dashboard, app, diagnostics, chat, history)
+ favicon.

Decimo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

L'oggetto `templates` (Jinja2Templates), gli accessor `_get_ai_client` /
`_get_launcher` / `_get_model_detail` / `_get_vram_info`, le costanti
`ROOT` / `LOG_DIR` e lo stato `active_runs` RESTANO in fast_app, risolti con
lazy import a call time. fast_app re-esporta gli handler (shim: i test
chiamano `fast_app.codex_app_page(...)`).
"""

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Dashboard IDE principale."""
    from devin.ui.fast_app import (  # lazy: accessor/costanti/stato condivisi
        LOG_DIR,
        ROOT,
        _get_ai_client,
        _get_launcher,
        _get_model_detail,
        _get_vram_info,
        active_runs,
        templates,
    )
    client = _get_ai_client()
    health = client.health()
    launcher = _get_launcher()
    models_running = False
    models_info = []

    if launcher:
        status = launcher.get_status()
        models_running = bool(status.local_running)
        for alias, info in status.local_running.items():
            models_info.append(_get_model_detail(alias, info))

    # Lista progetti disponibili in workspace/
    workspace_path = ROOT / "workspace"
    projects = []
    if workspace_path.exists():
        for item in sorted(workspace_path.iterdir()):
            if item.is_dir():
                projects.append({
                    "name": item.name,
                    "path": str(item),
                    "has_python": any(item.rglob("*.py"))
                })

    # Run recenti
    recent_runs = []
    if LOG_DIR.exists():
        for f in sorted(LOG_DIR.glob("run_*.log"), reverse=True)[:10]:
            stat = f.stat()
            content = f.read_text(encoding="utf-8", errors="ignore")
            run_status = "unknown"
            if "status: success" in content.lower():
                run_status = "success"
            elif "status: failed" in content.lower():
                run_status = "failed"
            elif "status: timeout" in content.lower():
                run_status = "timeout"
            elif "status: stopped" in content.lower():
                run_status = "stopped"

            recent_runs.append({
                "run_id": f.stem,
                "status": run_status,
                "size": f.stat().st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "preview": content[:200]
            })

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "health": health,
            "models_running": models_running,
            "models_info": models_info,
            "vram": _get_vram_info(),
            "projects": projects,
            "recent_runs": recent_runs,
            "active_runs": list(active_runs.keys())
        }
    )


@router.get("/app", response_class=HTMLResponse)
async def codex_app_page(request: Request):
    """Codex-like SPA shell; legacy dashboard/chat stay available while this matures."""
    from devin.ui.fast_app import templates  # lazy
    return templates.TemplateResponse(request=request, name="codex_app.html", context={})


@router.get("/app/diagnostics", response_class=HTMLResponse)
async def codex_diagnostics_page(request: Request):
    """Dedicated diagnostics/training/memory hub kept out of the clean main workspace."""
    from devin.ui.fast_app import templates  # lazy
    return templates.TemplateResponse(request=request, name="codex_diagnostics.html", context={})


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    from devin.ui.fast_app import (  # lazy
        _get_ai_client,
        _get_launcher,
        _get_model_detail,
        _get_vram_info,
        templates,
    )
    client = _get_ai_client()
    launcher = _get_launcher()

    models_chat_info = {}
    if launcher:
        status = launcher.get_status()
        for alias, info in status.local_running.items():
            models_chat_info[alias] = _get_model_detail(alias, info)

    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={
            "models_info": models_chat_info,
            "vram": _get_vram_info()
        }
    )


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    from devin.ui.fast_app import LOG_DIR, templates  # lazy
    runs = []
    if LOG_DIR.exists():
        for f in sorted(LOG_DIR.glob("run_*.log"), reverse=True):
            stat = f.stat()
            content = f.read_text(encoding="utf-8", errors="ignore")
            status = "unknown"
            if "status: success" in content.lower():
                status = "success"
            elif "status: failed" in content.lower():
                status = "failed"
            elif "status: timeout" in content.lower():
                status = "timeout"
            elif "status: stopped" in content.lower():
                status = "stopped"
            runs.append({
                "run_id": f.stem,
                "file": str(f.name),
                "size": f.stat().st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "status": status,
                "preview": content[:500]
            })
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={"runs": runs[:50]}
    )


@router.get("/favicon.ico")
async def favicon():
    # Toglie il 404 ricorrente nei log — nessuna icona, risposta vuota.
    from fastapi import Response
    return Response(status_code=204)


# === PWA (2026-07-18, slice approvata: mobile = PWA + Tailscale) ===


@router.get("/manifest.webmanifest")
async def pwa_manifest():
    """Web app manifest con content type corretto (StaticFiles non conosce
    l'estensione .webmanifest e servirebbe application/octet-stream)."""
    from devin.ui.fast_app import ROOT  # lazy
    from fastapi.responses import FileResponse
    return FileResponse(
        ROOT / "devin" / "ui" / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@router.get("/sw.js")
async def pwa_service_worker():
    """Service worker a scope ROOT.

    Servirlo dal mount /static lo limiterebbe a controllare solo /static/*:
    da qui puo' intercettare /app e le API. Service-Worker-Allowed: / e'
    esplicito; Cache-Control: no-cache perche' il file SW deve SEMPRE
    rivalidarsi (e' il meccanismo di aggiornamento del SW stesso).
    """
    from devin.ui.fast_app import ROOT  # lazy
    from fastapi.responses import FileResponse
    return FileResponse(
        ROOT / "devin" / "ui" / "static" / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )
