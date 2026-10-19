"""Chat con strumenti: autonoma finche' serve, fermata solo se gira in tondo (19/10).

Il caso vero: dopo 10 giri la chat toglieva gli strumenti in silenzio; DEVIN ha
ripetuto tre volte la stessa ricerca e poi ha scritto la chiamata come testo.
"""
from __future__ import annotations

import json

from devin.ui.routers import chat as chat_router
from test_chat_compattazione import FintoAI, _chiama, _storia


def _chiamata(i, nome, **argomenti):
    return {"tipo": "chiamate", "testo": "", "chiamate": [{"id": f"c{i}", "type": "function", "function": {
        "name": nome, "arguments": json.dumps(argomenti)}}]}


def _messaggi(sse, evento):
    return [json.loads(b.split("data: ", 1)[1])["message"] for b in sse.split("\n\n")
            if b.startswith(f"event: {evento}\n")]


def _avvisi(sse):
    # gli avvisi dei server MCP dipendono dalla macchina: qui contano quelli della chat
    return [a for a in _messaggi(sse, "warning") if not a.startswith("MCP ")]


def _prepara(monkeypatch, copione):
    cercate = []

    async def finta(domanda, ai_client):
        cercate.append(domanda)
        return f"risultati per {domanda}"

    monkeypatch.setattr(chat_router, "_scaffold_web_reference", finta)
    ai = FintoAI("non usato")
    ai.copione = copione
    offerti = []
    originale = ai.stream_eventi

    def registra(messages, **kw):
        offerti.append([t["function"]["name"] for t in (kw.get("tools") or [])])
        ai.istantanee.append([dict(m) for m in messages])     # la lista poi cresce: si copia
        yield from originale(messages, **kw)

    ai.istantanee = []

    ai.stream_eventi = registra
    return ai, cercate, offerti


def test_un_lavoro_lungo_va_avanti_da_solo(tmp_path, monkeypatch):
    giri = 15                                   # piu' del vecchio tetto di 10
    ai, cercate, offerti = _prepara(monkeypatch, [
        [_chiamata(i, "cerca_web", domanda=f"argomento {i} {'dettaglio ' * i}")] for i in range(giri)
    ] + [[{"tipo": "risposta", "testo": "Ecco il risultato."}]])
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert len(cercate) == giri and len(ai.inviati) == giri + 1
    assert all("cerca_web" in nomi for nomi in offerti), "gli strumenti non spariscono"
    assert _avvisi(sse) == [] and not any("Sorvegliante" in m for m in _messaggi(sse, "info"))
    assert persist.load()[-1]["content"] == "Ecco il risultato."


def test_chi_ripete_viene_avvisato_e_poi_fermato(tmp_path, monkeypatch):
    stessa = "build123d Polygon Plane.YZ winding order clockwise"
    ai, cercate, offerti = _prepara(monkeypatch, [
        [_chiamata(0, "cerca_web", domanda=stessa)],
        [_chiamata(1, "cerca_web", domanda=stessa)],
        [_chiamata(2, "cerca_web", domanda=stessa.upper())],            # avviso
        [_chiamata(3, "cerca_web", domanda=stessa)],
        [_chiamata(4, "cerca_web", domanda=" " + stessa)],              # stop
        [{"tipo": "risposta", "testo": "Mi sono bloccato sul verso del poligono."}],
    ])
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert cercate == [stessa], "la stessa ricerca si fa una volta sola"
    assert any(m.startswith("🔁 Sorvegliante") for m in _messaggi(sse, "info"))
    avviso = ai.istantanee[3][-1]
    assert avviso["role"] == "user" and "Ti stai ripetendo" in avviso["content"]
    assert offerti[-1] == [], "dopo lo stop l'ultimo giro e' senza strumenti"
    stop = ai.istantanee[-1][-1]
    assert "fermato perche' girava in tondo" in stop["content"]
    assert any("girava in tondo" in a and "continua" in a for a in _avvisi(sse))
    assert persist.load()[-1]["content"] == "Mi sono bloccato sul verso del poligono."


def test_il_tetto_di_sicurezza_e_dichiarato(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_router, "MAX_GIRI_SICUREZZA", 4)
    ai, cercate, offerti = _prepara(monkeypatch, [
        [_chiamata(i, "cerca_web", domanda=f"tema {i} {'x' * i}")] for i in range(4)
    ] + [[{"tipo": "risposta", "testo": "fatto"}]])
    sse, _ = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert len(cercate) == 4 and offerti[-1] == []
    assert "tetto di sicurezza di 4 passaggi" in ai.istantanee[-1][-1]["content"]
    assert any("tetto di sicurezza" in a for a in _avvisi(sse))


def test_una_chiamata_scritta_come_testo_produce_un_avviso(tmp_path, monkeypatch):
    ai, _, _ = _prepara(monkeypatch, [[{"tipo": "risposta", "testo": (
        "Rifaccio il profilo:\n<tool_call>\n<function=modella_3d>\n<parameter=nome>\nsupporto\n"
        "</parameter>\n</function>\n</tool_call>")}]])
    sse, _ = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert any("chiamata a uno strumento come testo" in a for a in _avvisi(sse))


def test_una_risposta_normale_non_produce_avvisi(tmp_path, monkeypatch):
    ai, _, _ = _prepara(monkeypatch, [[{"tipo": "risposta", "testo": (
        "In build123d `extrude` e' una function del modulo, non un metodo: nessun tool_call serve, "
        "e <b>function</b> resta testo.")}]])
    sse, _ = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert _avvisi(sse) == []
