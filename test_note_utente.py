"""Note dell'utente mentre DEVIN lavora: in chat interrompono e fanno ripartire, nel run entrano al turno dopo."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from devin.core import note_utente
from test_chat_compattazione import FintoAI, _chiama, _evento, _storia

ROOT = Path(__file__).resolve().parent


def test_la_coda_accetta_note_solo_mentre_qualcuno_le_legge():
    chiave = note_utente.chiave_chat("/p", "c1")
    with pytest.raises(note_utente.NotaRifiutata, match="non sta lavorando"):
        note_utente.aggiungi(chiave, "aspetta")
    note_utente.apri(chiave)
    try:
        assert note_utente.aggiungi(chiave, "  usa il turno 1/21  ") == 1
        with pytest.raises(note_utente.NotaRifiutata, match="vuota"):
            note_utente.aggiungi(chiave, "   ")
        assert note_utente.prendi(chiave) == ["usa il turno 1/21"]
        assert note_utente.prendi(chiave) == []
        for i in range(note_utente.MAX_IN_CODA):
            note_utente.aggiungi(chiave, f"n{i}")
        with pytest.raises(note_utente.NotaRifiutata, match="troppe"):
            note_utente.aggiungi(chiave, "ancora")
    finally:
        note_utente.chiudi(chiave)
    assert not note_utente.attiva(chiave)


def test_una_nota_in_chat_interrompe_e_devin_riparte_tenendone_conto(tmp_path, monkeypatch):
    chiuso = {"si": False}
    ai = FintoAI("non usato")

    def primo_giro():
        try:
            yield {"tipo": "ragionamento", "testo": "il ciclo 1/21 forse e' 5,25 giorni..."}
            # l'utente scrive mentre DEVIN ragiona
            note_utente.aggiungi(note_utente.chiave_chat(str(tmp_path), "c1"),
                                 "nel 1/21 ogni squadra fa 7 giorni per turno")
            yield {"tipo": "risposta", "testo": "Allora, "}
            yield {"tipo": "risposta", "testo": "questo non deve arrivare"}
        finally:
            chiuso["si"] = True

    ai.copione = [primo_giro(), iter([{"tipo": "risposta", "testo": "con 7 giorni per turno il ciclo e' 21."}])]
    sse, persist = _chiama(tmp_path, monkeypatch, ai, _storia(2, 10))
    assert chiuso["si"], "lo stream interrotto deve essere chiuso (il server smette di generare)"
    assert "questo non deve arrivare" not in sse
    assert _evento(sse, "nota") == {"note": ["nel 1/21 ogni squadra fa 7 giorni per turno"]}
    secondo = ai.inviati[1]
    assert secondo[-2] == {"role": "assistant", "content": "Allora, "}
    assert secondo[-1]["role"] == "user" and "NOTA DELL'UTENTE" in secondo[-1]["content"]
    assert "7 giorni per turno" in secondo[-1]["content"]
    storia = persist.load()
    assert storia[-2] == {"role": "user", "content": "📝 nel 1/21 ogni squadra fa 7 giorni per turno"}
    assert storia[-1]["content"] == "Allora, con 7 giorni per turno il ciclo e' 21."
    assert not note_utente.attiva(note_utente.chiave_chat(str(tmp_path), "c1"))


def test_la_nota_a_chat_ferma_e_rifiutata_con_il_motivo(monkeypatch):
    import devin.ui.fast_app as fast_app
    from devin.ui.routers import chat as chat_router
    monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: p)
    esito = asyncio.run(chat_router.api_chat_nota(chat_router.NotaRequest(testo="ehi", project_path="/x", chat_id="z")))
    assert "non sta lavorando" in esito["error"]
    esito = asyncio.run(chat_router.api_run_nota("run_1", chat_router.NotaRequest(testo="ehi")))
    assert "non sta lavorando" in esito["error"]


def test_l_agente_legge_le_note_al_turno_dopo(tmp_path):
    from devin.agents.tool_agent import AgenteStrumenti
    from test_tool_agent import AIFinta, chiamata

    coda = [["usa pathlib invece di os.path"]]
    ai = AIFinta([[chiamata("lista_file")], [chiamata("fine", riassunto="ok")]])
    log = []
    agente = AgenteStrumenti(ai, tmp_path, log=lambda m, l="info": log.append(m),
                             note=lambda: coda.pop(0) if coda else [])
    agente.esegui("compito")
    primo = ai.visti[0]
    assert primo[-1]["role"] == "user" and "usa pathlib" in primo[-1]["content"]
    assert any("📝 Nota dell'utente: usa pathlib" in riga for riga in log)


def test_l_app_lascia_scrivere_mentre_devin_lavora():
    js = (ROOT / "devin/ui/static/js/codex_app.js").read_text(encoding="utf-8")
    busy = js[js.index("function setChatBusy"):]
    busy = busy[: busy.index("\n}\n")]
    assert '$("chat-input").disabled = false;' in busy and '"Nota"' in busy
    assert '"/api/chat/nota"' in js and "/api/run/${encodeURIComponent(runId)}/nota" in js
    assert 'event.type === "nota"' in js
