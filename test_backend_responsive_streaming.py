"""Lo streaming della chat non deve fermare il backend.

16/10, app 0.2.4: mentre DEVIN rispondeva, la barra laterale diceva "Nessun
progetto ancora". Il router iterava un generatore SINCRONO (requests) dentro
un generatore async: ogni attesa di rete bloccava l'event loop, e le altre
richieste (elenco progetti, stato, Stop) aspettavano o scadevano.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

from starlette.concurrency import iterate_in_threadpool

ROOT = Path(__file__).resolve().parent


def lento():
    for i in range(3):
        time.sleep(0.2)            # come requests che aspetta il prossimo pezzo
        yield i


def test_iterating_in_a_thread_leaves_the_event_loop_free() -> None:
    async def scenario():
        battiti = 0

        async def cuore():
            nonlocal battiti
            while True:
                await asyncio.sleep(0.02)
                battiti += 1

        task = asyncio.create_task(cuore())
        pezzi = [p async for p in iterate_in_threadpool(lento())]
        task.cancel()
        return pezzi, battiti

    pezzi, battiti = asyncio.run(scenario())
    assert pezzi == [0, 1, 2]
    assert battiti >= 15, f"l'event loop e' rimasto fermo ({battiti} battiti in 0,6 s)"


def test_routers_do_not_iterate_sync_generators_on_the_event_loop() -> None:
    chat = (ROOT / "devin/ui/routers/chat.py").read_text(encoding="utf-8")
    assert "async for evento in iterate_in_threadpool(" in chat
    assert "for evento in ai.stream_eventi(" not in chat.replace("iterate_in_threadpool(\n                    ai.stream_eventi(", "")
    auto = (ROOT / "devin/ui/routers/autocomplete.py").read_text(encoding="utf-8")
    assert "async for chunk in iterate_in_threadpool(" in auto


def test_a_failed_projects_poll_keeps_the_last_list() -> None:
    js = (ROOT / "devin/ui/static/js/codex_app.js").read_text(encoding="utf-8")
    assert 'fetchJson("/api/workspace/projects").catch(() => null)' in js
    assert "if (workspace) renderProjects(workspace);" in js
