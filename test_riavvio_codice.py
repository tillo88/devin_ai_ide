"""Il backend si riavvia da solo col codice nuovo quando DEVIN e' libero (22/10).

Il 21/10 tre correzioni sono rimaste ferme per ore perche' il backend non si
riavviava dopo il deploy. Qui: decide bene (fermo / aspetto / riavvio), aspetta
chi lavora, ignora una sessione abbandonata, esce con 75 solo quando tocca.
"""
from __future__ import annotations

import subprocess
import time

import pytest

from devin.core import agente_locale, note_utente, riavvio_codice
from devin.ui import fast_app

A, B = "a" * 40, "b" * 40


@pytest.fixture(autouse=True)
def pulito(monkeypatch):
    riavvio_codice._richiesto.clear()
    monkeypatch.setattr(fast_app, "active_runs", {})
    monkeypatch.setattr(fast_app, "starting_runs", set())
    yield
    riavvio_codice._richiesto.clear()
    with agente_locale._LOCK:
        for chiave in list(agente_locale._SESSIONI):
            agente_locale._togli(chiave)


def test_decisione():
    assert riavvio_codice.controlla(A, A, "") == "fermo"
    assert riavvio_codice.controlla(A, None, "") == "fermo", "git illeggibile non riavvia"
    assert riavvio_codice.controlla(A, B, "una risposta in corso") == "aspetto"
    assert riavvio_codice.controlla(A, B, "") == "riavvio"


def test_aspetta_chi_lavora_poi_si_riavvia(capsys):
    commit = iter([A, B, B, B, B])
    motivi = iter(["una risposta in corso", "una risposta in corso", ""])
    uscite = []
    riavvio_codice.sorveglia(A, esci=lambda: uscite.append(1), dormi=lambda s: None,
                             commit=lambda: next(commit), perche_occupato=lambda: next(motivi))
    assert uscite == [1]
    assert riavvio_codice.codice_uscita() == riavvio_codice.CODICE_RIAVVIO
    log = capsys.readouterr().out
    assert log.count("aspetto, una risposta in corso") == 1, "l'attesa si scrive una volta, non ogni minuto"
    assert "DEVIN e' libero, mi riavvio" in log


def test_senza_codice_nuovo_esce_con_zero():
    assert riavvio_codice.codice_uscita() == 0


def test_chi_lavora_e_chi_ha_abbandonato():
    assert riavvio_codice.occupato() == ""
    chiave = note_utente.chiave_chat("/p", "c1")
    note_utente.apri(chiave)
    try:
        assert riavvio_codice.occupato() == "una risposta in corso"
    finally:
        note_utente.chiudi(chiave)
    sessione = agente_locale.nuova_sessione("/p", "c2", "x", [])
    assert riavvio_codice.occupato() == "l'agente locale sta lavorando"
    sessione.ultimo_uso = time.time() - riavvio_codice.SESSIONE_INATTIVA - 1
    assert riavvio_codice.occupato() == "", "una sessione abbandonata non blocca il riavvio"
    fast_app.active_runs["r1"] = object()
    assert riavvio_codice.occupato() == "un run in corso"


def test_solo_sotto_systemd(monkeypatch):
    monkeypatch.delenv("DEVIN_RIAVVIO_AUTOMATICO", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert riavvio_codice.avvia() is False
    monkeypatch.setenv("INVOCATION_ID", "x")
    monkeypatch.setenv("DEVIN_RIAVVIO_AUTOMATICO", "0")
    assert riavvio_codice.avvia() is False


def test_commit_letto_da_git(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    commit = riavvio_codice.commit_corrente(tmp_path)
    assert commit and len(commit) == 40
    assert riavvio_codice.commit_corrente(tmp_path / "manca") is None


def test_col_codice_nuovo_si_rilancia_nello_stesso_processo():
    """22/10: uscendo con 75 la sessione DEVIN vedeva il backend fermo e spegneva anche il modello."""
    import sys
    uscite, rilanci = [], []
    riavvio_codice.chiudi_o_rilancia(esci=uscite.append, rilancia=lambda exe, argv: rilanci.append((exe, argv)))
    assert uscite == [0] and rilanci == [], "senza codice nuovo si esce e basta"
    riavvio_codice._richiesto.set()
    uscite.clear()
    riavvio_codice.chiudi_o_rilancia(esci=uscite.append, rilancia=lambda exe, argv: rilanci.append((exe, argv)))
    assert rilanci == [(sys.executable, [sys.executable, *sys.argv])]


def test_se_il_rilancio_fallisce_ci_pensa_systemd():
    riavvio_codice._richiesto.set()
    uscite = []

    def rotto(exe, argv):
        raise OSError("exec negato")
    riavvio_codice.chiudi_o_rilancia(esci=uscite.append, rilancia=rotto)
    assert uscite == [riavvio_codice.CODICE_RIAVVIO]
