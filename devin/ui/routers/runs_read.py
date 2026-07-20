"""Router runs_read: superficie di sola lettura dei run (lista, eventi,
SSE log, retention/cleanup log).

Dodicesimo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md) — fetta A del runs core; il nucleo mutante
(run/resume/stop/scaffold) resta in fast_app per la fetta B. Move puro:
path e comportamento identici.

`active_runs`/`runs_lock`, `LOG_DIR` e lo store `_run_events` RESTANO in
fast_app (single-owner del run-core) risolti con lazy import a call time:
il test che monkeypatcha `fast_app._run_events` continua a valere.
`LogRetentionPolicy`/`cleanup_logs` restano importati anche in fast_app
(li usa lo startup hook); qui arrivano diretti da devin.core.log_retention.
fast_app re-esporta `api_run_events` (shim: i test lo chiamano).
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from devin.core.log_retention import (
    LogRetentionPolicy,
    cleanup_logs,
    mark_log_opened,
)

router = APIRouter()


@router.get("/api/runs/active")
async def api_runs_active():
    """
    Run realmente in esecuzione ORA (oggetti Orchestrator vivi in memoria), non una
    euristica sul contenuto del log. Un run vecchio/crashato che non ha scritto la
    riga finale 'status: ...' ha status='unknown' in /api/runs ma NON e' qui dentro:
    evita che la dashboard lo mostri come 'in esecuzione' per sempre.
    """
    from devin.ui.fast_app import active_runs, runs_lock, starting_runs  # lazy: run-core
    with runs_lock:
        return {"active_run_ids": sorted(set(starting_runs) | set(active_runs))}


@router.get("/api/logs/retention")
async def api_logs_retention():
    import os
    from devin.ui.fast_app import LOG_DIR, active_runs, runs_lock  # lazy
    policy = LogRetentionPolicy.from_env()
    with runs_lock:
        active = list(active_runs.keys())
    summary = await asyncio.to_thread(
        cleanup_logs,
        LOG_DIR,
        policy=policy,
        active_run_ids=active,
        dry_run=True,
    )
    return {
        "policy": {
            "enabled": policy.enabled,
            "retention_days": policy.retention_days,
            "keep_recent_runs": policy.keep_recent_runs,
            "env": {
                "DEVIN_LOG_AUTOCLEAN": os.getenv("DEVIN_LOG_AUTOCLEAN", "1"),
                "DEVIN_LOG_RETENTION_DAYS": os.getenv("DEVIN_LOG_RETENTION_DAYS", "14"),
                "DEVIN_LOG_KEEP_RECENT_RUNS": os.getenv("DEVIN_LOG_KEEP_RECENT_RUNS", "50"),
            },
        },
        "summary": summary,
    }


@router.post("/api/logs/cleanup")
async def api_logs_cleanup(request: Request):
    from devin.ui.fast_app import LOG_DIR, active_runs, runs_lock  # lazy
    data = await request.json()
    dry_run = bool(data.get("dry_run", True))
    policy = LogRetentionPolicy.from_env()
    with runs_lock:
        active = list(active_runs.keys())
    summary = await asyncio.to_thread(
        cleanup_logs,
        LOG_DIR,
        policy=policy,
        active_run_ids=active,
        dry_run=dry_run,
    )
    return {"ok": True, "summary": summary}


@router.get("/api/runs")
async def api_runs():
    from devin.ui.fast_app import LOG_DIR  # lazy
    if not LOG_DIR.exists():
        return []
    runs = []
    for f in sorted(LOG_DIR.glob("run_*.log"), reverse=True):
        stat = f.stat()
        content = f.read_text(encoding="utf-8", errors="ignore")
        statuses = re.findall(
            r"(?im)^status:\s*([a-z_]+)\s*$", content
        )
        status = statuses[-1].lower() if statuses else "unknown"
        runs.append({
            "run_id": f.stem,
            "file": str(f.name),
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "status": status,
            "preview": content[:500]
        })
    return runs[:50]


@router.get("/api/run/{run_id}/events")
async def api_run_events(run_id: str, after_seq: Optional[int] = None, limit: int = 500):
    """Structured run timeline for the Codex-like work stream / Mind panel."""
    from devin.ui.fast_app import _run_events  # lazy: patchabile su fast_app
    try:
        safe_limit = max(1, min(int(limit), 1000))
        return {"run_id": run_id, "events": _run_events.list(run_id, after_seq=after_seq, limit=safe_limit)}
    except ValueError:
        return {"error": "invalid run_id"}


@router.get("/api/run/{run_id}/events/stream")
async def api_run_events_stream(run_id: str, after_seq: Optional[int] = None):
    """SSE stream of structured run events. Keeps /stream/{run_id} for legacy log text."""
    from devin.ui.fast_app import _run_events, active_runs, runs_lock, starting_runs  # lazy
    try:
        _run_events.path_for(run_id)
    except ValueError:
        return JSONResponse({"error": "invalid run_id"}, status_code=400)

    async def generate():
        last_seq = after_seq if after_seq is not None else -1
        idle_polls = 0
        while True:
            events = _run_events.list(run_id, after_seq=last_seq, limit=100)
            for event in events:
                last_seq = int(event.get("seq", last_seq))
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event.get("type") == "run_finished":
                    return
            with runs_lock:
                alive = run_id in active_runs or run_id in starting_runs
            if not alive:
                idle_polls += 1
                if idle_polls > 10:
                    yield "event: done\ndata: " + json.dumps({"run_id": run_id, "last_seq": last_seq}) + "\n\n"
                    return
            else:
                idle_polls = 0
            await asyncio.sleep(0.3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/run/{run_id}/log")
async def api_run_log(run_id: str, download: int = 0):
    """#27: con ?download=1 restituisce il .log come file (Content-Disposition),
    altrimenti testo leggibile nel browser (prima tornava sempre JSON, che il tab
    'View'/'Log' mostrava grezzo). Containment su LOG_DIR contro traversal via run_id."""
    from devin.ui.fast_app import LOG_DIR  # lazy
    log_path = (LOG_DIR / f"{run_id}.log").resolve()
    if LOG_DIR.resolve() not in log_path.parents or not log_path.exists():
        return {"error": "not found"}
    mark_log_opened(LOG_DIR, log_path.name)
    if download:
        return FileResponse(str(log_path), media_type="text/plain; charset=utf-8",
                            filename=f"{run_id}.log")
    return PlainTextResponse(log_path.read_text(encoding="utf-8", errors="ignore"))


@router.get("/stream/{run_id}")
async def stream_log(run_id: str):
    from devin.ui.fast_app import LOG_DIR, active_runs, runs_lock, starting_runs  # lazy
    log_path = LOG_DIR / f"{run_id}.log"

    async def generate():
        for _ in range(20):
            if log_path.exists():
                break
            await asyncio.sleep(0.5)
            yield f"data: {json.dumps({'type': 'wait', 'msg': 'Waiting for log file...'})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'Log file not found'})}\n\n"
            return

        # FIX audit #9 (2026-07-10): prima c'era f.seek(0, 2) — scartava TUTTE le
        # righe gia' scritte (su run veloci si perdeva anche il footer 'status:',
        # la UI restava appesa) e il while non terminava MAI: connessioni SSE
        # zombie che si accumulavano (era il vero motivo per cui Ctrl+C richiedeva
        # os._exit). Ora: lettura da inizio file, chiusura sul footer di stato o
        # quando il run non e' piu' attivo.
        import re as _re
        status_re = _re.compile(
            r"^status:\s*(success|failed|timeout|stopped|stalled|"
            r"awaiting_approval|applied_uncommitted|rejected|rolled_back)\s*$"
        )
        dead_polls = 0
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            while True:
                line = f.readline()
                if line:
                    payload = json.dumps({"type": "log", "line": line.rstrip("\n")})
                    yield f"data: {payload}\n\n"
                    if status_re.match(line.strip()):
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        return
                    continue
                # nessuna riga nuova: il run e' ancora vivo?
                with runs_lock:
                    alive = run_id in active_runs or run_id in starting_runs
                if not alive:
                    dead_polls += 1
                    if dead_polls > 10:  # ~3s di grazia per l'ultimo flush su disco
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"
                        return
                else:
                    dead_polls = 0
                await asyncio.sleep(0.3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )
