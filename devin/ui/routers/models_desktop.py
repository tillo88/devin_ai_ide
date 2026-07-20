"""Router models_desktop: stato/kill dei modelli locali + cleanup chiusura GUI.

Quinto router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

Design (rischio 1 del piano — test che monkeypatchano privati di fast_app):
- gli helper `_known_local_model_servers`, `_shutdown_known_local_model_servers`,
  `_rig_self_hosted`, `_get_launcher` e lo stato run-core (`active_runs`,
  `runs_lock`) RESTANO in fast_app e sono risolti con lazy import DENTRO gli
  handler, cioe' a call time: i 6 test che fanno
  `monkeypatch.setattr(fast_app, ...)` + `fast_app.api_desktop_close_cleanup()`
  continuano a funzionare invariati (fast_app re-esporta i 3 handler).
- `_training_job_snapshot` vive in routers/training.py (direzione sicura).
- `threading.Timer` e' chiamato come attributo di modulo: il test che patcha
  `fast_app.threading.Timer` muta lo stesso oggetto modulo globale.
"""

import os
import threading

from fastapi import APIRouter

from devin.ui.routers.training import _training_job_snapshot

router = APIRouter()


@router.get("/api/models/status")
async def api_models_status():
    from devin.ui.fast_app import _get_launcher  # lazy: patchabile su fast_app
    launcher = _get_launcher()
    if not launcher:
        return {"running": False, "models": []}
    status = launcher.get_status()
    return {
        "running": bool(status.local_running),
        "models": list(status.local_running.values()),
        "source": status.model_source
    }


@router.post("/api/models/kill")
async def api_models_kill():
    from devin.ui.fast_app import (  # lazy: patchabili su fast_app
        _get_launcher,
        _known_local_model_servers,
        _shutdown_known_local_model_servers,
    )
    launcher = _get_launcher()
    try:
        tracked = []
        if launcher:
            status = launcher.get_status()
            tracked = list(status.local_running.keys())
            launcher.shutdown_all()
        known = list(_known_local_model_servers().keys())
        killed_known = _shutdown_known_local_model_servers()
        killed = sorted(set(tracked + known + killed_known))
        return {"status": "killed", "local_models": killed, "count": len(killed)}
    except Exception as e:
        return {"error": str(e)}


@router.post("/api/desktop/close_cleanup")
async def api_desktop_close_cleanup():
    """Called by the Tauri shell when the desktop window closes.

    2026-07-15: oltre a spegnere i model server locali, di default spegne
    anche il BACKEND locale (richiesta utente: la GUI chiusa non deve
    lasciare processi orfani; il backend locale esiste solo per servire la
    GUI). Guardie:
      - DEVIN_DESKTOP_CLOSE_STOPS_BACKEND=0 -> comportamento precedente
        (backend resta su, comodo in sviluppo per non perdere lo stato);
      - rig_self_hosted=true -> MAI auto-stop (il backend e' sul rig, serve
        anche ad altri client);
      - run/training attivi -> niente auto-stop (non uccidere un job a meta').
    Remote rig models are not controlled here.
    """
    from devin.ui.fast_app import (  # lazy: stato/helper single-owner in fast_app
        _get_launcher,
        _known_local_model_servers,
        _rig_self_hosted,
        _shutdown_known_local_model_servers,
        active_runs,
        runs_lock,
        starting_runs,
    )
    enabled = os.getenv("DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS", "1").strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        return {"status": "skipped", "reason": "disabled_by_env", "local_models": [], "count": 0, "backend": "kept"}
    launcher = _get_launcher()
    try:
        tracked = []
        if launcher:
            status = launcher.get_status()
            tracked = list(status.local_running.keys())
        known = list(_known_local_model_servers().keys())
        local_models = sorted(set(tracked + known))
        killed = []
        if local_models:
            if launcher:
                launcher.shutdown_all()
            killed_known = _shutdown_known_local_model_servers()
            killed = sorted(set(local_models + killed_known))

        # --- auto-stop del backend locale ---
        # OPT-IN via env: e' il LAUNCHER desktop a esportare
        # DEVIN_DESKTOP_CLOSE_STOPS_BACKEND=1 quando avvia il backend headless
        # per la GUI. Cosi' un backend avviato a mano (sviluppo, pytest) non
        # si suicida mai da solo.
        stop_backend = os.getenv("DEVIN_DESKTOP_CLOSE_STOPS_BACKEND", "0").strip().lower() in {"1", "true", "yes", "on"}
        backend = "kept"
        with runs_lock:
            busy = bool(active_runs or starting_runs)
        busy = busy or any(job.get("status") == "running" for job in _training_job_snapshot())
        if stop_backend and not _rig_self_hosted() and not busy:
            backend = "stopping"
            # esci DOPO aver risposto alla GUI (stesso pattern os._exit del Ctrl+C)
            threading.Timer(1.5, os._exit, args=[0]).start()
        elif stop_backend and busy:
            backend = "kept_busy_run"

        return {"status": "killed" if killed else "skipped",
                "reason": "desktop_window_closed" if killed else "no_local_models",
                "local_models": killed, "count": len(killed), "backend": backend}
    except Exception as e:
        return {"status": "error", "error": str(e), "local_models": [], "count": 0, "backend": "kept"}
