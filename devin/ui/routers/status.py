"""Router status: health, desktop readiness, mind status, models info.

Sesto router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

Design (come models_desktop, rischio 1 del piano): gli helper condivisi
(`_get_ai_client`, `_get_launcher`, `_build_mind_status`, `_get_vram_info`,
`_get_model_detail`, `_known_local_model_servers`) e le costanti `ROOT` /
`LOG_DIR` RESTANO in fast_app — sono usati anche da chat/pages/runs e
monkeypatchati dai test su fast_app — risolti con lazy import a call time.
`_desktop_windows_paths` e' usato SOLO da readiness e si sposta qui.
fast_app re-esporta i 4 handler (shim: i test chiamano
`fast_app.api_desktop_readiness()`).
"""

import os

from fastapi import APIRouter

router = APIRouter()


def _desktop_windows_paths() -> dict[str, str]:
    username = os.getenv("USER") or os.getenv("USERNAME") or "tillo"
    root = f"C:\\Users\\{username}\\AppData\\Local\\DEVIN"
    return {
        "root": root,
        "host": root + "\\desktop-host",
        "launcher": root + "\\DEVIN Desktop.cmd",
        "logs": root + "\\logs",
        "desktop_launch_log": root + "\\logs\\desktop-launch.log",
        "tauri_log": root + "\\logs\\tauri-dev.log",
    }


@router.get("/api/health")
async def api_health():
    """Health check rig/locale per il polling badge in dashboard (index.html)."""
    from devin.ui.fast_app import _get_ai_client  # lazy: patchabile su fast_app
    return _get_ai_client().health()


@router.get("/api/desktop/readiness")
async def api_desktop_readiness():
    from devin.ui.fast_app import (  # lazy: helper/costanti single-owner
        LOG_DIR,
        ROOT,
        _known_local_model_servers,
    )
    paths = _desktop_windows_paths()
    local_servers = _known_local_model_servers()
    return {
        "desktop_host": paths,
        "backend": {
            "url": "http://127.0.0.1:5000/app",
            "health": "ok",
            "wsl_repo": str(ROOT),
            "headless_log": str(LOG_DIR / "fast_app_headless.log"),
        },
        "close_cleanup": {
            "enabled": os.getenv("DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS", "1").strip().lower() not in {"0", "false", "no", "off"},
            "policy": "kill_known_local_devin_model_servers_only",
            "remote_rig_safe": True,
        },
        "local_model_servers": local_servers,
        "validation_links": {
            "runs": "/app/diagnostics#runs",
            "training": "/app/diagnostics#training",
            "knowledge": "/app/diagnostics#knowledge",
            "sandbox": "/app/diagnostics#sandbox",
            "memory": "/app/diagnostics#memory",
        },
    }


@router.get("/api/mind/status")
async def api_mind_status():
    """High-level cognitive state for the future Codex-like Mind panel."""
    from devin.ui.fast_app import _build_mind_status  # lazy: usato anche dalle pages
    return _build_mind_status()


@router.get("/api/models/info")
async def api_models_info():
    from devin.ui.fast_app import (  # lazy: condivisi con chat/pages
        _get_launcher,
        _get_model_detail,
        _get_vram_info,
    )
    launcher = _get_launcher()
    if not launcher:
        return {"running": False, "models": [], "vram": _get_vram_info()}

    status = launcher.get_status()
    models = []
    for alias, info in status.local_running.items():
        models.append(_get_model_detail(alias, info))

    return {
        "running": bool(status.local_running),
        "models": models,
        "vram": _get_vram_info(),
        "source": status.model_source
    }
