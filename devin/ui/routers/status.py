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


@router.get("/api/steward/status")
async def api_steward_status(project_path: str = "", chat_id: str = ""):
    """Context Steward CS3 - read-only snapshot derived from the deterministic
    core over the CURRENT chat history. No own state, no side effects: the panel
    reflects the core, never drives it (docs/CONTEXT_STEWARD_PLAN.md CS3)."""
    from devin.core.chat_persistence import ChatPersistence
    from devin.core.steward_coordinator import StewardCoordinator
    from devin.ui.fast_app import CONFIG_PATH

    import json as _json
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            config = _json.load(fh)
    except Exception:
        config = {}

    # Senza progetto (general chat) non c'e' persistenza per-progetto: history
    # vuota, stato IDLE. Con progetto, si legge la conversazione reale.
    history = []
    has_checkpoint = False
    if project_path:
        persistence = ChatPersistence(project_path, chat_id=chat_id or None)
        history = persistence.load()
        has_checkpoint = bool(persistence.get_continuity())

    local_cfgs = config.get("models", {}).get("local_models", {})
    contexts = [int(c.get("ctx_size")) for c in local_cfgs.values()
                if isinstance(c, dict) and str(c.get("ctx_size", "")).isdigit()]
    context_size = min(contexts) if contexts else 8192

    coordinator = StewardCoordinator(task_id=chat_id or "general", settings=config)
    coordinator.observe_history(history, context_size=context_size)
    # A persisted continuity checkpoint means a compaction already happened.
    if has_checkpoint:
        coordinator.note_action("checkpoint di continuita' presente")
    snapshot = coordinator.snapshot()
    snapshot["context_size"] = context_size
    snapshot["history_messages"] = len(history)
    return snapshot


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
