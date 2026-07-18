"""Router autocomplete: suggerimenti codice (single-shot + SSE per Monaco).

Ottavo router estratto da fast_app.py (split plan 2026-07-18,
docs/FAST_APP_SPLIT_PLAN.md). Move puro: path e comportamento identici.

`_get_autocomplete` RESTA in fast_app (accessor lazy condiviso, vedi piano
"stato mutabile condiviso") e viene risolto con lazy import a call time.
Nessun test tocca questi handler: nessuno shim necessario.
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class AutocompleteRequest(BaseModel):
    code: str


@router.post("/api/autocomplete")
async def api_autocomplete(req: AutocompleteRequest):
    from devin.ui.fast_app import _get_autocomplete  # lazy: accessor condiviso
    if not req.code:
        return {"suggestion": ""}

    try:
        auto = _get_autocomplete()
        suggestion = auto.suggest(req.code)
        return {"suggestion": suggestion or ""}
    except Exception as e:
        return {"suggestion": "", "error": str(e)}


class AutocompleteStreamRequest(BaseModel):
    code: str
    language: str = "python"
    cursor_position: int = None


@router.post("/api/autocomplete/stream")
async def api_autocomplete_stream(req: AutocompleteStreamRequest):
    """
    Autocomplete con streaming SSE per Monaco Editor.
    Usa il modello Coder locale (backup leggero) per suggerimenti rapidi.
    """
    from devin.ui.fast_app import _get_autocomplete  # lazy: accessor condiviso
    if not req.code:
        return {"error": "empty code"}

    auto = _get_autocomplete()

    async def generate_sse():
        try:
            yield f"event: meta\ndata: {json.dumps({'language': req.language, 'mode': 'coder'})}\n\n"

            token_count = 0
            for chunk in auto.suggest_stream(req.code, language=req.language, cursor_position=req.cursor_position):
                token_count += 1
                yield f"data: {json.dumps({'token': chunk})}\n\n"
                await asyncio.sleep(0)

            yield f"event: done\ndata: {json.dumps({'tokens': token_count})}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
