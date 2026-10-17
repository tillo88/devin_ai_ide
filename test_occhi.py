"""Occhi di DEVIN: pagine pubbliche in chat, la propria app nell'agente."""
import json
import os
import sys

from devin.core import occhi


def test_in_chat_solo_indirizzi_pubblici():
    assert "non pubblico" in occhi.url_pubblico("http://127.0.0.1:5001/api")
    assert "non pubblico" in occhi.url_pubblico("http://192.168.1.100:5000/")
    assert "non pubblico" in occhi.url_pubblico("http://[::1]/")
    assert occhi.url_pubblico("file:///etc/passwd") == "solo indirizzi http(s)"
    assert occhi.url_pubblico("http://93.184.215.14/") is None


def test_senza_browser_lo_dice(tmp_path):
    config = {"tools": {"solver_python": str(tmp_path / "manca" / "python")}}
    pronto, motivo = occhi.disponibile(config)
    assert not pronto and "non installato" in motivo
    assert occhi.guarda("https://example.org", config=config).startswith("ERRORE: browser non disponibile")


def _finto_browser(tmp_path, monkeypatch, uscita):
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\necho 'rumore'\necho '" + json.dumps(uscita) + "'\n")
    python.chmod(0o755)
    monkeypatch.setattr(occhi, "disponibile", lambda config=None: (True, "pronto"))
    monkeypatch.setattr(occhi, "interprete", lambda config=None: python)


def test_risultato_per_il_modello(tmp_path, monkeypatch):
    _finto_browser(tmp_path, monkeypatch, {"titolo": "Turni", "url": "http://127.0.0.1:8765/", "status": 200,
                                          "console": ["TypeError: x is undefined"], "falliti": [],
                                          "testo": "Squadra A  M M M", "link": 2, "bottoni": ["Genera"]})
    testo = occhi.guarda("http://127.0.0.1:8765/", consenti_locale=True, cartella_screenshot=tmp_path)
    assert testo.startswith("pagina: Turni — http://127.0.0.1:8765/ (HTTP 200)")
    assert "errori della console: TypeError: x is undefined" in testo
    assert "bottoni: Genera" in testo and "Squadra A M M M" in testo


def test_avvia_il_server_della_app_e_lo_ferma(tmp_path, monkeypatch):
    _finto_browser(tmp_path, monkeypatch, {"titolo": "ok", "url": "u", "status": 200, "console": [],
                                          "falliti": [], "testo": "", "link": 0, "bottoni": []})
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text("<h1>ciao</h1>")
    porta = 18765
    lanciati = []
    vero = occhi._avvia_server

    def spia(avvia, cartella, url, attesa=20.0):
        processo, problema = vero(avvia, cartella, url, attesa)
        lanciati.append(processo)
        return processo, problema

    monkeypatch.setattr(occhi, "_avvia_server", spia)
    testo = occhi.guarda(f"http://127.0.0.1:{porta}/", consenti_locale=True, cartella=app,
                         avvia=["python", "-m", "http.server", str(porta), "--bind", "127.0.0.1"])
    assert testo.startswith("pagina: ok"), testo
    assert lanciati[0].poll() is not None            # fermato dopo
    assert not occhi._porta_aperta(f"http://127.0.0.1:{porta}/")


def test_server_che_muore_subito_si_dice(tmp_path, monkeypatch):
    _finto_browser(tmp_path, monkeypatch, {})
    testo = occhi.guarda("http://127.0.0.1:18766/", consenti_locale=True, cartella=tmp_path,
                         avvia=["python", "-c", "raise SystemExit('porta occupata')"])
    assert testo.startswith("ERRORE: il server si e' chiuso subito") and "porta occupata" in testo


def test_nell_agente_lo_strumento_c_e_solo_se_il_browser_c_e(tmp_path):
    from devin.agents.tool_agent import AgenteStrumenti
    nomi = lambda a: [t["function"]["name"] for t in a.strumenti]
    assert "guarda_pagina" not in nomi(AgenteStrumenti(None, tmp_path, log=lambda *a: None))
    chiamate = []
    agente = AgenteStrumenti(None, tmp_path, log=lambda *a: None,
                             guarda=lambda url, **kw: chiamate.append((url, kw)) or "pagina: x")
    assert "guarda_pagina" in nomi(agente)
    assert agente._esegui_strumento("guarda_pagina", {"url": "http://127.0.0.1:8000/", "avvia": ["python", "app.py"]}) == "pagina: x"
    assert chiamate[0][1]["avvia"] == ["python", "app.py"] and chiamate[0][1]["cartella"] == tmp_path.resolve()
