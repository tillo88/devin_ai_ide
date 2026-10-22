"""Lezioni dagli errori e richieste di miglioria (22/10).

Una lezione con la prova (errore e poi successo dello stesso strumento) e'
verificata ed entra subito nelle istruzioni; senza prova aspetta Alessandro.
Una richiesta di miglioria cita solo pagine aperte davvero.
"""
from __future__ import annotations

import json

from devin.core import agente_locale, lezioni
from devin.ui.routers import chat as chat_router


def _conv(*risultati):
    """Conversazione in formato API: (strumento, risultato) in ordine."""
    messaggi = [{"role": "system", "content": "s"}]
    for i, (nome, risultato) in enumerate(risultati):
        messaggi.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"t{i}", "type": "function", "function": {"name": nome, "arguments": "{}"}}]})
        messaggi.append({"role": "tool", "tool_call_id": f"t{i}", "content": risultato})
    return messaggi


ERRORE_PEZZO = "ERRORE nel codice del pezzo (exit 1):\nNameError: name 'radians' is not defined"


def test_la_prova_e_un_errore_seguito_da_un_successo_dello_stesso_strumento():
    assert lezioni.trova_prova(_conv(("modella_3d", ERRORE_PEZZO), ("modella_3d", "ok: pezzo.stl 1284 B")))["strumento"] == "modella_3d"
    assert lezioni.trova_prova(_conv(("modella_3d", ERRORE_PEZZO))) is None
    assert lezioni.trova_prova(_conv(("modella_3d", ERRORE_PEZZO), ("cerca_web", "risultati"))) is None, \
        "il successo di un altro strumento non prova niente"
    assert lezioni.trova_prova(_conv(("modella_3d", "ok"), ("modella_3d", ERRORE_PEZZO))) is None, "l'ordine conta"
    assert lezioni.trova_prova(_conv(("ricorda_lezione", "ERRORE: servono causa e regola"),
                                     ("ricorda_lezione", "Lezione salvata"))) is None, \
        "ricordare una lezione non e' la prova di una lezione"
    prova = lezioni.trova_prova(_conv(("esegui", "exit 1 · 80 ms\n1 failed"), ("esegui", "exit 0 · 70 ms\n5 passed")))
    assert prova and prova["errore"].startswith("exit 1") and prova["successo"].startswith("exit 0")


def test_lezione_verificata_entra_nelle_istruzioni_quella_senza_prova_aspetta():
    assert lezioni.blocco_prompt() == ""
    r = lezioni.ricorda({"causa": "radians non e' importato nel preludio",
                         "regola": "usa math.radians o importalo"},
                        _conv(("modella_3d", ERRORE_PEZZO), ("modella_3d", "ok")), "chat")
    assert "verificata" in r
    assert "usa math.radians o importalo" in lezioni.blocco_prompt()
    r = lezioni.ricorda({"causa": "credo che serva X", "regola": "fai sempre X"}, _conv(), "chat")
    assert "DA CONFERMARE" in r
    assert "fai sempre X" not in lezioni.blocco_prompt(), "senza prova non entra"
    assert "gia'" in lezioni.ricorda({"causa": "c", "regola": "Usa math.radians, o importalo!"}, _conv(), "chat")
    da_confermare = next(l for l in lezioni.elenco()["lezioni"] if l["regola"] == "fai sempre X")
    assert lezioni.cambia_stato("lezione", da_confermare["id"], "confermata")["status"] == "ok"
    assert "fai sempre X" in lezioni.blocco_prompt(), "confermata da Alessandro entra"
    lezioni.cambia_stato("lezione", da_confermare["id"], "scartata")
    assert "fai sempre X" not in lezioni.blocco_prompt()
    assert "error" in lezioni.cambia_stato("lezione", da_confermare["id"], "approvata")
    assert "error" in lezioni.cambia_stato("altro", da_confermare["id"], "confermata")


def test_nel_prompt_al_massimo_le_ultime():
    for i in range(lezioni.MAX_IN_PROMPT + 3):
        lezioni.ricorda({"causa": f"c{i}", "regola": f"regola numero {i}"},
                        _conv(("esegui", "exit 1"), ("esegui", "exit 0")), "chat")
    blocco = lezioni.blocco_prompt()
    assert "regola numero 0 " not in blocco and f"regola numero {lezioni.MAX_IN_PROMPT + 2} " in blocco
    assert blocco.count("\n- ") == lezioni.MAX_IN_PROMPT


def test_richiesta_di_miglioria_solo_con_fonti_aperte():
    campi = {"titolo": "OpenSCAD", "cosa": "CAD a script", "perche": "i pezzi parametrici",
             "come": "apt install openscad"}
    aperta = _conv(("cerca_web", "Fonte: https://openscad.org/downloads.html\n..."))
    r = lezioni.richiedi({**campi, "fonti": ["https://openscad.org/inventata"]}, aperta, "chat")
    assert r.startswith("ERRORE") and "inventata" in r
    assert lezioni.elenco()["richieste"] == []
    r = lezioni.richiedi({**campi, "fonti": ["https://openscad.org/downloads.html/"]}, aperta, "chat")
    assert r.startswith("Richiesta inviata")
    assert "gia' in attesa" in lezioni.richiedi({**campi, "fonti": []}, aperta, "chat")
    (richiesta,) = lezioni.elenco()["richieste"]
    assert richiesta["stato"] == "in_attesa" and richiesta["fonti"] == ["https://openscad.org/downloads.html"]
    assert lezioni.richiedi({"titolo": "x"}, aperta, "chat").startswith("ERRORE: mancano")
    assert lezioni.cambia_stato("richiesta", richiesta["id"], "approvata")["voce"]["stato"] == "approvata"


def test_in_chat_la_lezione_si_esegue_sul_rig_con_la_prova_della_conversazione(tmp_path, monkeypatch):
    from test_chat_sorvegliante import _chiamata, _chiama, _messaggi, _prepara, _storia
    ai, cercate, offerti = _prepara(monkeypatch, [
        [_chiamata(0, "cerca_web", domanda="prima")],
        [_chiamata(1, "ricorda_lezione", causa="la ricerca era troppo vaga", regola="cerca con termini precisi")],
        [{"tipo": "risposta", "testo": "fatto"}],
    ])
    sse, _persist = _chiama(tmp_path, monkeypatch, ai, _storia(2, 4))
    assert "ricorda_lezione" in offerti[0] and "richiedi_miglioria" in offerti[0]
    assert any(m.startswith("🧠") for m in _messaggi(sse, "info"))
    assert "DA CONFERMARE" in ai.istantanee[2][-1]["content"], "una ricerca riuscita non e' una prova"
    (lezione,) = lezioni.elenco()["lezioni"]
    lezioni.cambia_stato("lezione", lezione["id"], "confermata")
    ai2, _c, _o = _prepara(monkeypatch, [[{"tipo": "risposta", "testo": "ok"}]])
    _chiama(tmp_path, monkeypatch, ai2, _storia(2, 4))
    assert "cerca con termini precisi" in ai2.istantanee[0][0]["content"], "la lezione arriva nelle istruzioni"


def test_agente_locale_lezione_sul_rig_non_sul_pc(tmp_path, monkeypatch):
    from test_agente_locale import AIFinta, _chiamata, _eventi, _passo, ambiente as _ambiente  # noqa: F401
    from devin.ui import fast_app
    monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: p)
    monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
    monkeypatch.setattr(chat_router, "local_workspace_for_project",
                        lambda p: {"mode": "direct", "bridge_id": "b1", "display_name": "spyengine"})
    ai = AIFinta([[_chiamata(0, "esegui", programma="pytest")], [_chiamata(1, "esegui", programma="pytest")],
                  [{"tipo": "chiamate", "testo": "", "chiamate": (
                      _chiamata(2, "ricorda_lezione", causa="mancava la fixture", regola="usa tmp_path")["chiamate"]
                      + _chiamata(3, "leggi", percorso="a.py")["chiamate"])}],
                  "finito"])
    monkeypatch.setattr(fast_app, "_get_ai_client", lambda: ai)
    progetto = tmp_path / "p"
    s = _eventi(_passo(progetto, task="sistema i test"), "sessione")[0]["session_id"]
    _passo(progetto, session_id=s, results=[{"id": "c0", "content": "exit 1 · 5 ms\n1 failed"}])
    terzo = _passo(progetto, session_id=s, results=[{"id": "c1", "content": "exit 0 · 5 ms\n1 passed"}])
    (strumenti,) = _eventi(terzo, "strumenti")
    assert [c["name"] for c in strumenti["chiamate"]] == ["leggi"], "la lezione non va al PC"
    _passo(progetto, session_id=s, results=[{"id": "c3", "content": "1: x"}])
    risultati = {m.get("tool_call_id"): m["content"] for m in ai.visti[3] if m.get("role") == "tool"}
    assert "verificata" in risultati["c2"] and risultati["c3"] == "1: x"
    nuova = agente_locale.nuova_sessione(str(progetto), "c9", "altro", [])
    assert "usa tmp_path" in nuova.messaggi[0]["content"]
    agente_locale.chiudi(nuova.id)
