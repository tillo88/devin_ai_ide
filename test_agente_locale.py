"""Agente locale a passaggi (21/10): DEVIN usa strumenti che l'app esegue sul PC.

Prima: 18 estratti scelti dall'app e una sola risposta senza strumenti, e su
spyengine DEVIN "non poteva analizzare tutto". Qui si fissa il ciclo:
compito -> strumenti al PC -> risultati -> ... -> risposta salvata nella chat.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from devin.core import agente_locale
from devin.core.chat_persistence import ChatPersistence
from devin.ui.routers import chat as chat_router


def _chiamata(i, nome, **argomenti):
    return {"tipo": "chiamate", "testo": "", "chiamate": [{"id": f"c{i}", "type": "function", "function": {
        "name": nome, "arguments": json.dumps(argomenti)}}]}


class AIFinta:
    config: dict = {}

    def __init__(self, copione):
        self.copione = list(copione)
        self.visti = []
        self.strumenti = []

    def stream_eventi(self, messages, mode="reasoning", sforzo="", tools=None, **kw):
        self.visti.append([dict(m) for m in messages])
        self.strumenti.append([t["function"]["name"] for t in (tools or [])])
        turno = self.copione.pop(0)
        if isinstance(turno, str):
            yield {"tipo": "ragionamento", "testo": "penso"}
            yield {"tipo": "risposta", "testo": turno}
            return
        yield from turno


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    from devin.ui import fast_app
    monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: p)
    monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
    monkeypatch.setattr(chat_router, "local_workspace_for_project",
                        lambda p: {"mode": "direct", "bridge_id": "b1", "display_name": "spyengine"})

    def con(ai):
        monkeypatch.setattr(fast_app, "_get_ai_client", lambda: ai)
        return ai
    return tmp_path, con


def _passo(project, **campi):
    async def scenario():
        risposta = await chat_router.api_local_workspace_agent_step(
            chat_router.LocalAgentStepRequest(project_path=str(project), chat_id="c1", **campi))
        if isinstance(risposta, dict):
            return risposta
        pezzi = []
        async for pezzo in risposta.body_iterator:
            pezzi.append(pezzo if isinstance(pezzo, str) else pezzo.decode())
        return "".join(pezzi)
    return asyncio.run(scenario())


def _eventi(sse, nome):
    out = []
    for blocco in sse.split("\n\n"):
        if blocco.startswith(f"event: {nome}\n"):
            out.append(json.loads(blocco.split("data: ", 1)[1]))
    return out


def test_il_ciclo_va_al_pc_e_torna_fino_alla_risposta(ambiente):
    progetto, con = ambiente
    storia = ChatPersistence(str(progetto), chat_id="c1")
    storia.append("user", "ciao"); storia.append("assistant", "ciao!")
    storia.append("user", "analizza spyengine")          # salvato da /api/chat
    ai = con(AIFinta([[_chiamata(0, "elenca")], [_chiamata(1, "leggi", percorso="src/main.py")],
                      "Ho letto main.py: il punto d'ingresso e' run()."]))

    primo = _passo(progetto, task="analizza spyengine")
    sessione = _eventi(primo, "sessione")[0]["session_id"]
    assert _eventi(primo, "strumenti") == [{"session_id": sessione, "chiamate": [
        {"id": "c0", "name": "elenca", "arguments": {}}]}]
    assert any("📂 Elenco il progetto" in e["message"] for e in _eventi(primo, "info"))
    inviati = ai.visti[0]
    assert inviati[0]["role"] == "system" and "spyengine" in inviati[0]["content"]
    assert [m["content"] for m in inviati[1:]] == ["ciao", "ciao!", "analizza spyengine"], "compito non duplicato"
    assert set(ai.strumenti[0]) == agente_locale.NOMI

    secondo = _passo(progetto, session_id=sessione, results=[{"id": "c0", "content": "src/main.py (120 byte)"}])
    assert _eventi(secondo, "strumenti")[0]["chiamate"][0]["arguments"] == {"percorso": "src/main.py"}
    assert ai.visti[1][-1] == {"role": "tool", "tool_call_id": "c0", "content": "src/main.py (120 byte)"}

    terzo = _passo(progetto, session_id=sessione, results=[{"id": "c1", "content": "1: def run(): ..."}])
    assert _eventi(terzo, "done") and not _eventi(terzo, "strumenti")
    assert storia.load()[-1] == {"role": "assistant", "content": "Ho letto main.py: il punto d'ingresso e' run()."}
    assert _eventi(terzo, "salvato")[0]["assistant_index"] == len(storia.load()) - 1
    assert agente_locale.prendi(sessione, str(progetto)) is None, "la sessione si chiude"


def test_chiamate_sbagliate_non_vanno_al_pc(ambiente):
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(0, "rm_rf", percorso="/")],
                      [_chiamata(1, "proponi_modifiche", summary="x", operations=[
                          {"path": "../fuori.py", "operation": "write", "content": "x"}])],
                      [_chiamata(2, "proponi_modifiche", summary="corregge", operations=[
                          {"path": "src/a.py", "operation": "write", "content": "x = 1\n"}])],
                      "fatto"]))
    primo = _passo(progetto, task="sistema")
    # le prime due sono respinte sul rig: il modello riceve l'errore nello stesso passo
    assert "non esiste" in ai.visti[1][-1]["content"]
    assert "piano di modifiche non valido" in ai.visti[2][-1]["content"]
    (strumenti,) = _eventi(primo, "strumenti")
    assert [c["name"] for c in strumenti["chiamate"]] == ["proponi_modifiche"]
    assert strumenti["chiamate"][0]["arguments"]["operations"][0]["path"] == "src/a.py"


def test_un_risultato_mancante_e_un_errore_esplicito(ambiente):
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(0, "leggi", percorso="a.py")], "ok"]))
    sessione = _eventi(_passo(progetto, task="leggi"), "sessione")[0]["session_id"]
    _passo(progetto, session_id=sessione, results=[])
    assert "non ha restituito il risultato" in ai.visti[1][-1]["content"]


def test_il_sorvegliante_ferma_chi_rilegge_sempre_lo_stesso(ambiente):
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(i, "leggi", percorso="a.py")] for i in range(5)] + ["Mi sono bloccato."]))
    sse = _passo(progetto, task="capisci a.py")
    sessione = _eventi(sse, "sessione")[0]["session_id"]
    tutti = sse
    for i in range(5):
        tutti += _passo(progetto, session_id=sessione, results=[{"id": f"c{i}", "content": "1: x = 1"}])
    assert any("Sorvegliante" in e["message"] for e in _eventi(tutti, "info"))
    assert any("Ti stai ripetendo" in str(m.get("content")) for visti in ai.visti for m in visti), \
        "l'avviso arriva al modello, non solo all'app"
    assert any("girava in tondo" in e["message"] for e in _eventi(tutti, "warning"))
    assert ai.strumenti[-1] == [], "dopo lo stop l'ultimo giro e' senza strumenti"
    assert "fermato perche' girava in tondo" in ai.visti[-1][-1]["content"]
    assert _eventi(tutti, "done")


def test_sessione_sconosciuta_e_cartella_non_diretta(ambiente, monkeypatch):
    progetto, con = ambiente
    con(AIFinta([]))
    assert "scaduta o sconosciuta" in _passo(progetto, session_id="nessuna", results=[])["error"]
    assert "task locale vuoto" in _passo(progetto, task="  ")["error"]
    altra = agente_locale.nuova_sessione("/altro/progetto", "c1", "x", [])
    assert "scaduta o sconosciuta" in _passo(progetto, session_id=altra.id, results=[])["error"], \
        "la sessione di un altro progetto non si usa"
    monkeypatch.setattr(chat_router, "local_workspace_for_project", lambda p: {"mode": "snapshot"})
    assert "non e' una cartella Windows diretta" in _passo(progetto, task="x")["error"]


def test_l_etichetta_dice_cosa_succede_sul_pc():
    assert agente_locale.etichetta("leggi", {"percorso": "a.py", "da_riga": 200}) == "📖 Leggo a.py dalla riga 200"
    assert agente_locale.etichetta("esegui", {"programma": "pytest", "argomenti": ["-q"]}) == "▶ Eseguo pytest -q"
    assert agente_locale.etichetta("cerca", {"testo": "normalizza", "cartella": "src"}) == "🔎 Cerco «normalizza» in src"


def test_la_nota_dell_utente_arriva_mentre_il_turno_e_sul_pc(ambiente):
    """21/10: «Nota non consegnata: DEVIN non sta lavorando qui» mentre l'agente locale lavorava."""
    from devin.core import note_utente
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(0, "leggi", percorso="a.py")], "ok, uso content"]))
    sessione = _eventi(_passo(progetto, task="sistema"), "sessione")[0]["session_id"]
    # il turno e' sul PC (nessuno stream aperto): la nota va accettata e tenuta
    chiave = note_utente.chiave_chat(str(progetto), "c1")
    assert note_utente.aggiungi(chiave, "il testo nuovo va in content") == 1
    secondo = _passo(progetto, session_id=sessione, results=[{"id": "c0", "content": "1: x"}])
    assert "il testo nuovo va in content" in ai.visti[1][-1]["content"], "la nota entra nel giro successivo"
    assert ai.visti[1][-1]["role"] == "user" and ai.visti[1][-2]["role"] == "tool"
    assert any("ha letto 1 nota" in e["message"] for e in _eventi(secondo, "info"))
    # a sessione chiusa la nota si rifiuta (e lo si dice), non si perde in silenzio
    with pytest.raises(note_utente.NotaRifiutata):
        note_utente.aggiungi(chiave, "tardi")


def test_il_piano_rifiutato_dice_quali_campi_sono_arrivati(ambiente):
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(0, "proponi_modifiche", summary="x", operations=[
        {"path": "a.py", "operation": "replace", "before": "x = 1", "new": "x = 2",
         "expected_sha256": "a" * 64}])], "capito"]))
    _passo(progetto, task="sistema")
    errore = ai.visti[1][-1]["content"]
    assert "replace senza contenuto finale" in errore
    assert "before (5 caratteri)" in errore and "new (5 caratteri)" in errore, errore
    assert "content" in errore.split("i nomi sono", 1)[1]


def test_una_chiamata_tagliata_dal_limite_di_token_non_va_al_pc(ambiente):
    from devin.ai.client import AVVISO_TRONCATO
    progetto, con = ambiente
    tagliata = _chiamata(0, "proponi_modifiche", summary="x", operations=[
        {"path": "a.py", "operation": "write", "content": "x = 1\n"}])
    ai = con(AIFinta([[tagliata, {"tipo": "avviso", "testo": f"\n[{AVVISO_TRONCATO}]"}], "riprovo piu' corto"]))
    sse = _passo(progetto, task="sistema")
    assert not _eventi(sse, "strumenti"), "una chiamata a meta' non si esegue"
    assert "tagliata dal limite di token" in ai.visti[1][-1]["content"]


def test_proponi_modifiche_piatta_arriva_al_pc_come_piano(ambiente):
    """21/10: con content annidato in operations il testo multi-riga non arrivava mai."""
    progetto, con = ambiente
    schema = next(s for s in agente_locale.STRUMENTI if s["function"]["name"] == "proponi_modifiche")
    proprieta = schema["function"]["parameters"]["properties"]
    assert "operations" not in proprieta and proprieta["content"]["type"] == "string", \
        "il modello vede before/content come parametri di testo di primo livello"
    con(AIFinta([[_chiamata(0, "proponi_modifiche", summary="prezzo prudente", path="utils/selectors.py",
                            operation="replace", before="def parse_euro_price(t):\n    ...\n",
                            content="from utils.text import parse_euro_price\n",
                            expected_sha256="b" * 64)], "fatto"]))
    (strumenti,) = _eventi(_passo(progetto, task="correggi"), "strumenti")
    (chiamata,) = strumenti["chiamate"]
    assert chiamata["arguments"]["summary"] == "prezzo prudente"
    assert chiamata["arguments"]["operations"] == [{
        "path": "utils/selectors.py", "operation": "replace", "content": "from utils.text import parse_euro_price\n",
        "expected_sha256": "b" * 64, "before": "def parse_euro_price(t):\n    ...\n"}]


def test_file_nuovo_con_sha_vuoto_e_etichetta(ambiente):
    progetto, con = ambiente
    con(AIFinta([[_chiamata(0, "proponi_modifiche", summary="test", path="tests/test_prezzi.py",
                            operation="write", content="def test_x():\n    assert True\n",
                            expected_sha256="")], "ok"]))
    sse = _passo(progetto, task="aggiungi test")
    (chiamata,) = _eventi(sse, "strumenti")[0]["chiamate"]
    assert chiamata["arguments"]["operations"][0]["expected_sha256"] is None
    assert any("Propongo 1 modifica" in e["message"] for e in _eventi(sse, "info"))
    assert agente_locale.etichetta("proponi_modifiche", {"path": "a.py", "operation": "write", "summary": "x"}) \
        == "📝 Propongo: write a.py — x"


PNG_1X1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/eL9GtQAAAABJRU5ErkJggg==")


def test_le_foto_delle_finestre_arrivano_al_modello_se_vede(ambiente):
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(0, "guarda_programma", programma="python", argomenti=["app.py"])], "si apre"]))
    ai.vede_immagini = lambda mode="reasoning": (True, "proiettore")
    s = _eventi(_passo(progetto, task="prova l'app"), "sessione")[0]["session_id"]
    _passo(progetto, session_id=s, results=[{"id": "c0", "content": "1 finestra/e fotografate", "immagini": [
        {"url": "data:image/png;base64," + PNG_1X1, "titolo": "SpyEngine"},
        {"url": "data:image/jpeg;base64,/9j/", "titolo": "non png"},
        {"url": "data:image/png;base64,bm9uIHBuZw==", "titolo": "finto"}]}])
    ultimo = ai.visti[1][-1]
    assert ultimo["role"] == "user" and isinstance(ultimo["content"], list)
    immagini = [p for p in ultimo["content"] if p.get("type") == "image_url"]
    assert len(immagini) == 1, "solo PNG veri"
    assert "SpyEngine" in ultimo["content"][-1]["text"]
    assert "le vedi nel prossimo messaggio" in ai.visti[1][-2]["content"]


def test_senza_visione_le_foto_non_partono_e_lo_si_dice(ambiente):
    progetto, con = ambiente
    ai = con(AIFinta([[_chiamata(0, "guarda_programma", programma="python")], "ok"]))
    ai.vede_immagini = lambda mode="reasoning": (False, "niente proiettore")
    s = _eventi(_passo(progetto, task="prova"), "sessione")[0]["session_id"]
    _passo(progetto, session_id=s, results=[{"id": "c0", "content": "1 finestra/e fotografate",
                                             "immagini": [{"url": "data:image/png;base64," + PNG_1X1}]}])
    assert ai.visti[1][-1]["role"] == "tool"
    assert "non vede le immagini" in ai.visti[1][-1]["content"]


def test_la_storia_si_sposta_a_scatti_cosi_il_server_la_riprende_dalla_cache():
    """22/10: con «gli ultimi 10» ogni domanda nuova cambiava l'inizio della storia
    e il server la rileggeva tutta; ancorata, resta uguale per piu' domande di fila."""
    inizi = [agente_locale.inizio_storia(n) for n in range(0, 40)]
    assert inizi[:agente_locale.MAX_STORIA + 1] == [0] * (agente_locale.MAX_STORIA + 1)
    for n, inizio in enumerate(inizi):
        assert n - inizio >= min(n, agente_locale.MAX_STORIA), "sempre almeno MAX_STORIA messaggi"
        assert n - inizio < agente_locale.MAX_STORIA + agente_locale.PASSO_STORIA
    cambi = sum(1 for a, b in zip(inizi, inizi[1:]) if a != b)
    assert cambi <= 40 // agente_locale.PASSO_STORIA, "l'inizio cambia a scatti, non a ogni messaggio"

    def prefisso(n):
        storia = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]
        sessione = agente_locale.nuova_sessione("/p", "c", "compito", storia)
        agente_locale.chiudi(sessione.id)
        return [m["content"] for m in sessione.messaggi[1:-1]]
    # due domande di fila: la storia della seconda comincia come quella della prima
    primo, secondo = prefisso(12), prefisso(14)
    assert secondo[:len(primo)] == primo
