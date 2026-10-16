"""Compattazione della chat, dal router: si vede, riusa la conversazione e controlla il riassunto.

16/10: con 131k di contesto la chat tagliava a 20 messaggi e il "riassunto"
in produzione era sempre la coda testuale (regola "solo con istanza fresca").
Ora: conversazione intera finche' ci sta; oltre la soglia DEVIN scrive il
checkpoint, che si accetta solo se non cita cose assenti dalla chat.
"""
from __future__ import annotations

import asyncio
import json

import devin.ui.fast_app as fast_app
from devin.core.chat_persistence import ChatPersistence
from devin.ui.routers import chat as chat_router


class FintoAI:
    def __init__(self, riassunto):
        self.config = {"models": {"runtime_context_tokens": 8192}, "chat": {}}
        self.riassunto = riassunto
        self.ultima_fine = "stop"
        self.chiamate_local = []
        self.inviati = []

    def _get_endpoints(self, mode):
        return "http://rig", "devin-model"

    def local(self, messages, mode="reasoning", timeout=None, sforzo="", max_tokens=None):
        self.chiamate_local.append({"messages": messages, "sforzo": sforzo, "max_tokens": max_tokens})
        return self.riassunto

    def stream_eventi(self, messages, mode="reasoning", sforzo=""):
        self.inviati.append(messages)
        yield {"tipo": "risposta", "testo": "fatto"}


def _storia(n, peso):
    return [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"messaggio {i} su modulo_{i}.py " + "y" * peso} for i in range(n)]


def _chiama(tmp_path, monkeypatch, ai, storia):
    persist = ChatPersistence(str(tmp_path), chat_id="c1")
    for m in storia:
        persist.append(m["role"], m["content"])
    monkeypatch.setattr(fast_app, "_get_ai_client", lambda: ai)
    monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
    monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: p)
    monkeypatch.setattr(fast_app, "_build_project_context", lambda *a, **k: ([], {}))
    monkeypatch.setattr(fast_app, "_get_automem", lambda: None)

    async def scenario():
        risposta = await chat_router.api_chat(chat_router.ChatRequest(
            message="e adesso?", project_path=str(tmp_path), chat_id="c1", mode="reasoning"))
        pezzi = []
        async for pezzo in risposta.body_iterator:
            pezzi.append(pezzo if isinstance(pezzo, str) else pezzo.decode())
        return "".join(pezzi)

    return asyncio.run(scenario()), persist


def _evento(sse, nome):
    for blocco in sse.split("\n\n"):
        if blocco.startswith(f"event: {nome}\n"):
            return json.loads(blocco.split("data: ", 1)[1])
    return None


def test_conversazione_corta_va_intera_senza_compattare(tmp_path, monkeypatch):
    ai = FintoAI("non usato")
    sse, _ = _chiama(tmp_path, monkeypatch, ai, _storia(30, 10))
    assert _evento(sse, "compaction_start") is None
    assert ai.chiamate_local == []
    # 30 messaggi + quello nuovo: niente piu' tetto a 20
    assert sum(1 for m in ai.inviati[0] if m["role"] in {"user", "assistant"}) == 31


def test_oltre_soglia_devin_riassume_in_coda_e_il_segno_arriva(tmp_path, monkeypatch):
    riassunto = "## Fatti\nSi lavora su `modulo_2.py` e modulo_4.py. " + "dettaglio " * 20
    ai = FintoAI(riassunto)
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(16, 1500))
    inizio = _evento(sse, "compaction_start")
    esito = _evento(sse, "compaction")
    assert inizio["messages"] == 8
    assert esito["generation"] == "model" and esito["summarized_messages"] == 8
    assert esito["summary"].startswith("## Fatti")
    # il riassunto si chiede in coda alla conversazione intera (prefisso in cache)
    chiesta = ai.chiamate_local[0]
    assert chiesta["sforzo"] == "low" and chiesta["max_tokens"] == 6144
    assert chiesta["messages"][1]["content"].startswith("messaggio 0")
    assert "ISTRUZIONE DI SERVIZIO" in chiesta["messages"][-1]["content"]
    # la risposta parte da checkpoint + ultimi 8 + messaggio nuovo
    inviati = ai.inviati[0]
    assert "CONTINUITY CHECKPOINT" in inviati[0]["content"]
    assert [m["content"][:12] for m in inviati[1:3]] == ["messaggio 8 ", "messaggio 9 "]
    assert inviati[-1]["content"] == "e adesso?"
    assert persist.get_continuity()["validation"] == "accepted"


def test_riassunto_con_file_inventati_ripiega_e_lo_dice(tmp_path, monkeypatch):
    ai = FintoAI("## Fatti\nCreato `devin/nonesiste.py`. " + "dettaglio " * 20)
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(16, 1500))
    esito = _evento(sse, "compaction")
    assert esito["generation"] == "deterministic"
    assert "devin/nonesiste.py" in esito["rejected_reason"]
    assert "verbatim evidence" in persist.get_continuity()["summary"]


def test_tetto_raggiunto_dal_riassunto_si_dice(tmp_path, monkeypatch):
    ai = FintoAI("## Fatti\n" + "a" * 200)
    ai.ultima_fine = "length"
    sse, _ = _chiama(tmp_path, monkeypatch, ai, _storia(16, 1500))
    assert "tetto di 6144 token" in _evento(sse, "compaction")["rejected_reason"]
