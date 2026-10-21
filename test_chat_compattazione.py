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
        self.copione = []

    def _get_endpoints(self, mode):
        return "http://rig", "devin-model"

    def local(self, messages, mode="reasoning", timeout=None, sforzo="", max_tokens=None):
        self.chiamate_local.append({"messages": messages, "sforzo": sforzo, "max_tokens": max_tokens})
        return self.riassunto

    def stream_eventi(self, messages, mode="reasoning", sforzo="", tools=None, **kwargs):
        self.inviati.append(messages)
        self.strumenti = tools
        if self.copione:
            yield from self.copione.pop(0)
            return
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


def test_con_il_web_su_auto_devin_cerca_da_solo_e_la_ricerca_si_vede(tmp_path, monkeypatch):
    ai = FintoAI("non usato")
    ai.copione = [
        [{"tipo": "ragionamento", "testo": "non so il turno 1/21, cerco"},
         {"tipo": "chiamate", "testo": "", "chiamate": [{"id": "c1", "type": "function", "function": {
             "name": "cerca_web", "arguments": json.dumps({"domanda": "turno 1/21 quattro squadre"})}}]}],
        [{"tipo": "risposta", "testo": "Il ciclo 1/21 funziona cosi'."}],
    ]
    cercate = []

    async def finta_ricerca(domanda, ai_client):
        cercate.append(domanda)
        return "Ciclo continuo 4 squadre: 7 mattine, 7 pomeriggi, 7 notti..."

    monkeypatch.setattr(chat_router, "_scaffold_web_reference", finta_ricerca)
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert cercate == ["turno 1/21 quattro squadre"]
    assert "Cerco sul web: turno 1/21 quattro squadre" in sse
    # il secondo giro riceve la chiamata e il risultato, poi risponde
    secondo = ai.inviati[1]
    assert secondo[-2]["role"] == "assistant" and secondo[-2]["tool_calls"][0]["id"] == "c1"
    assert secondo[-1] == {"role": "tool", "tool_call_id": "c1",
                           "content": "Ciclo continuo 4 squadre: 7 mattine, 7 pomeriggi, 7 notti..."}
    assert persist.load()[-1]["content"] == "Il ciclo 1/21 funziona cosi'."
    # la nota di sistema non dice piu' "NON hai accesso a internet"
    assert "cerca_web" in ai.inviati[0][0]["content"]
    assert "NON hai accesso a internet" not in ai.inviati[0][0]["content"]


def test_lo_strumento_web_e_offerto_solo_in_auto(tmp_path, monkeypatch):
    ai = FintoAI("non usato")
    _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert ai.strumenti and ai.strumenti[0]["function"]["name"] == "cerca_web"


def test_i_messaggi_nuovi_ricevono_subito_l_indice_per_cancellarli(tmp_path, monkeypatch):
    ai = FintoAI("non usato")
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(4, 10))
    storia = persist.load()
    meta = _evento(sse, "meta")
    salvato = _evento(sse, "salvato")
    assert storia[meta["user_index"]] == {"role": "user", "content": "e adesso?"}
    assert storia[salvato["assistant_index"]] == {"role": "assistant", "content": "fatto"}


def test_l_app_mette_la_x_ai_messaggi_appena_scritti():
    from pathlib import Path
    js = (Path(__file__).resolve().parent / "devin/ui/static/js/codex_app.js").read_text(encoding="utf-8")
    # dal 19/10 il messaggio dell'utente sta sul nodo della risposta (una per chat),
    # non piu' in uno stato globale
    assert "aggiungiCancella(assistantNode._utente?.parentElement, payload.user_index)" in js
    assert "aggiungiCancella(assistantNode.parentElement, payload.assistant_index)" in js
    assert 'const userNode = appendChatMessage("user", message)' in js
    assert "assistantNode._utente = userNode" in js


def test_varianti_della_ricerca_web():
    from devin.ui.routers.chat import _varianti_ricerca
    v = _varianti_ricerca('"20 turni" OR "21 turni" 4 squadre tabella schema M P N riposo esempio settimana')
    assert v[0].startswith('"20 turni"')
    assert v[1] == "20 turni 21 turni 4 squadre tabella schema M P N riposo esempio settimana"
    assert v[2] == "20 turni 21 turni 4 squadre tabella schema"
    assert _varianti_ricerca("eclissi 2027") == ["eclissi 2027"]


def test_il_prefisso_stabile_non_cambia_se_chiedi_di_cercare(tmp_path, monkeypatch, capsys):
    """21/10: la frase "l'utente ti chiede di cercare" stava nel system, prima della
    descrizione degli strumenti: c'era solo a volte, e il rig rileggeva anche gli
    strumenti. Ora sta nel messaggio dell'utente; ogni lettura finisce nel journal."""
    def system_per(messaggio):
        ai = FintoAI("non usato")
        ai.copione = [[{"tipo": "uso", "testo": "", "prompt_tokens": 12000, "cache_tokens": 10500},
                       {"tipo": "risposta", "testo": "ok"}]]
        persist = ChatPersistence(str(tmp_path / messaggio[:5]), chat_id="c1")
        monkeypatch.setattr(fast_app, "_get_ai_client", lambda: ai)
        monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
        monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: p)
        monkeypatch.setattr(fast_app, "_build_project_context", lambda *a, **k: ([], {}))
        monkeypatch.setattr(fast_app, "_get_automem", lambda: None)

        async def scenario():
            risposta = await chat_router.api_chat(chat_router.ChatRequest(
                message=messaggio, project_path=str(tmp_path / messaggio[:5]), chat_id="c1", mode="reasoning"))
            async for _ in risposta.body_iterator:
                pass
        asyncio.run(scenario())
        inviati = ai.inviati[0]
        return inviati[0]["content"], inviati[-1]["content"], persist.load()

    sistema_normale, _, _ = system_per("come funziona il ciclo 1/21?")
    sistema_cerca, ultimo, storia = system_per("cerca sul web come funziona il ciclo 1/21")
    assert sistema_cerca == sistema_normale, "il system (prima degli strumenti) resta identico"
    assert ultimo.startswith("[NOTA DEL SISTEMA: in questo messaggio l'utente ti chiede di cercare sul web")
    assert storia[0]["content"] == "cerca sul web come funziona il ciclo 1/21", "la nota non entra nello storico"
    assert "[Chat] lettura chat: 12000 token, 10500 gia' in cache (88%)" in capsys.readouterr().out
