"""Riscaldamento della cache del modello (21/10).

Alessandro: "preferisco aspettare 2 minuti in piu' all'inizio e poi essere a
posto". All'avvio il backend fa leggere al modello il prefisso comune della chat
(istruzioni + descrizione degli strumenti, ~10k token); le chat lo riusano dalla
cache del server. Qui si fissa che il prefisso del riscaldamento e' IDENTICO a
quello di una chat vera, e che il riscaldamento non lascia tracce.
"""
from __future__ import annotations

import asyncio
import json

import devin.ui.fast_app as fast_app
from devin.core.chat_persistence import ChatPersistence
from devin.ui.routers import chat as chat_router
from test_chat_compattazione import FintoAI


class AIRegistra(FintoAI):
    def __init__(self):
        super().__init__("non usato")
        self.chiamate = []

    def stream_eventi(self, messages, mode="reasoning", sforzo="", tools=None, **kw):
        self.chiamate.append({"messaggi": [dict(m) for m in messages],
                              "strumenti": [t["function"]["name"] for t in (tools or [])],
                              "max_tokens": kw.get("max_tokens"), "sforzo": sforzo})
        yield {"tipo": "uso", "testo": "", "prompt_tokens": 10400, "cache_tokens": 0}
        if kw.get("max_tokens") == 1:
            yield {"tipo": "ragionamento", "testo": "ok"}
            return
        yield {"tipo": "risposta", "testo": "fatto"}


def _ambiente(tmp_path, monkeypatch, ai):
    monkeypatch.setattr(fast_app, "_get_ai_client", lambda: ai)
    monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
    monkeypatch.setattr(fast_app, "_build_project_context", lambda *a, **k: ([], {}))
    monkeypatch.setattr(fast_app, "_get_automem", lambda: None)
    monkeypatch.setattr(fast_app, "GENERAL_CHAT_PROJECT_KEY", str(tmp_path / "generale"))
    monkeypatch.setattr(chat_router, "_scaffold_web_reference", lambda *a, **k: None)


def _consuma(risposta):
    async def tutto():
        return "".join([p if isinstance(p, str) else p.decode() async for p in risposta.body_iterator])
    return asyncio.run(tutto())


def test_il_prefisso_del_riscaldamento_e_quello_di_una_chat_vera(tmp_path, monkeypatch, capsys):
    ai = AIRegistra()
    _ambiente(tmp_path, monkeypatch, ai)
    esito = asyncio.run(fast_app.riscalda_cache_del_modello())
    assert esito == "completato"
    riscaldamento = ai.chiamate[0]
    _consuma(asyncio.run(chat_router.api_chat(chat_router.ChatRequest(message="come stai oggi?"))))
    vera = ai.chiamate[1]

    assert riscaldamento["messaggi"][0] == vera["messaggi"][0], "stesso system: stesso prefisso"
    assert riscaldamento["messaggi"][0]["role"] == "system"
    assert riscaldamento["strumenti"] == vera["strumenti"] and riscaldamento["strumenti"], "stessi strumenti"
    assert riscaldamento["sforzo"] == vera["sforzo"]
    assert riscaldamento["max_tokens"] == 1 and vera["max_tokens"] is None
    uscita = capsys.readouterr().out
    assert "[Chat] lettura riscaldamento: 10400 token" in uscita
    assert "[Chat] riscaldamento completato" in uscita


def test_il_riscaldamento_non_lascia_tracce(tmp_path, monkeypatch):
    ai = AIRegistra()
    _ambiente(tmp_path, monkeypatch, ai)
    sse = _consuma(asyncio.run(chat_router.api_chat(chat_router.ChatRequest(
        message="ciao", riscaldamento=True, project_path="/da/ignorare", chat_id="c9",
        images=["data:image/png;base64,AAA"], use_web_search=True))))
    assert len(ai.chiamate) == 1, "una sola lettura, niente giri con strumenti"
    assert not (tmp_path / "generale").exists() or ChatPersistence(str(tmp_path / "generale")).load() == []
    eventi = [b for b in sse.split("\n\n") if b.startswith("event: done")]
    assert eventi and json.loads(eventi[0].split("data: ", 1)[1]) == {"riscaldamento": True}
    assert "data: {\"token\"" not in sse, "niente testo all'app"


def test_all_avvio_parte_solo_se_non_e_spento(monkeypatch):
    partiti = []

    async def finto():
        partiti.append(1)
        return "completato"

    monkeypatch.setattr(fast_app, "riscalda_cache_del_modello", finto)

    async def avvio():
        await fast_app._riscaldamento_all_avvio()
        await asyncio.sleep(0)

    monkeypatch.setenv("DEVIN_RISCALDAMENTO", "0")
    asyncio.run(avvio())
    assert partiti == []
    monkeypatch.setenv("DEVIN_RISCALDAMENTO", "1")
    asyncio.run(avvio())
    assert partiti == [1]


def test_un_errore_del_riscaldamento_finisce_nel_journal(tmp_path, monkeypatch, capsys):
    class Rotta(AIRegistra):
        def stream_eventi(self, *a, **kw):
            raise ConnectionError("rig non raggiungibile")
            yield  # pragma: no cover

    _ambiente(tmp_path, monkeypatch, Rotta())
    esito = asyncio.run(fast_app.riscalda_cache_del_modello())
    assert esito.startswith("non riuscito:") and "rig non raggiungibile" in esito
    assert "[Chat] riscaldamento non riuscito" in capsys.readouterr().out
