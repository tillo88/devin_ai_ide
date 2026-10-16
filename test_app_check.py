"""Prova dell'app: DEVIN usa davvero il programma scritto, in una copia."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from devin.core import app_check


CLI = '''import json, sys
from pathlib import Path
DB = Path("dati.json")
def main(argv):
    voci = json.loads(DB.read_text()) if DB.exists() else []
    if argv[:1] == ["aggiungi"] and len(argv) == 3:
        voci.append({"nome": argv[1], "data": argv[2]})
        DB.write_text(json.dumps(voci))
        print(f"aggiunta {argv[1]}")
        return 0
    if argv[:1] == ["lista"]:
        for v in voci:
            print(v["nome"], v["data"])
        return 0
    print("uso: aggiungi NOME DATA | lista", file=sys.stderr)
    return 2
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

SERVER = '''import os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<title>Scadenze</title>ok"
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def _piano(*passi):
    return app_check.leggi_piano(json.dumps({"passi": list(passi)}))


def test_il_piano_ammette_solo_la_forma_stretta():
    with pytest.raises(app_check.PianoNonValido, match="python"):
        _piano({"tipo": "comando", "argv": ["bash", "-c", "rm -rf /"]})
    with pytest.raises(app_check.PianoNonValido, match="tipo"):
        _piano({"tipo": "shell", "argv": ["python", "x.py"]})
    with pytest.raises(app_check.PianoNonValido, match="al massimo"):
        _piano(*[{"tipo": "comando", "argv": ["python", "x.py"]}] * 7)
    with pytest.raises(app_check.PianoNonValido, match="path"):
        _piano({"tipo": "http", "argv": ["python", "s.py"], "path": "http://evil"})
    with pytest.raises(app_check.PianoNonValido, match="JSON"):
        app_check.leggi_piano("non e' json")
    passi = app_check.leggi_piano('```json\n{"passi": [{"tipo": "comando", "argv": ["python", "a.py"], "secondi": 999}]}\n```')
    assert passi[0]["secondi"] == app_check.MAX_SECONDI
    assert passi[0]["non_contiene"] == ["Traceback (most recent call last)"]


def test_una_cli_si_prova_in_sequenza_e_la_sandbox_resta_pulita(tmp_path):
    (tmp_path / "scadenze.py").write_text(CLI, encoding="utf-8")
    passi = _piano(
        {"nome": "aggiungi", "tipo": "comando", "argv": ["python", "scadenze.py", "aggiungi", "Bollo", "2026-12-01"],
         "exit": 0, "contiene": ["aggiunta Bollo"]},
        {"nome": "lista", "tipo": "comando", "argv": ["python", "scadenze.py", "lista"], "contiene": ["Bollo 2026-12-01"]},
        {"nome": "errore di input", "tipo": "comando", "argv": ["python", "scadenze.py", "boh"], "exit": 2, "contiene": ["uso:"]},
    )
    prova = app_check.esegui_piano(tmp_path, passi)
    assert prova["stato"] == "riuscita", prova
    assert not (tmp_path / "dati.json").exists(), "la prova ha sporcato la sandbox"


def test_un_controllo_che_non_torna_e_un_fallimento_col_motivo(tmp_path):
    (tmp_path / "scadenze.py").write_text(CLI, encoding="utf-8")
    prova = app_check.esegui_piano(tmp_path, _piano(
        {"nome": "lista vuota", "tipo": "comando", "argv": ["python", "scadenze.py", "lista"], "contiene": ["Bollo"]}))
    assert prova["stato"] == "fallita"
    assert "manca 'Bollo'" in prova["errori"][0]
    rotto = tmp_path / "rotto.py"
    rotto.write_text("raise SystemExit(undefined_name)\n", encoding="utf-8")
    prova = app_check.esegui_piano(tmp_path, _piano(
        {"nome": "parte", "tipo": "comando", "argv": ["python", "rotto.py"]}))
    assert "Traceback" in prova["errori"][0]


def test_un_server_si_avvia_risponde_e_si_chiude(tmp_path):
    (tmp_path / "app.py").write_text(SERVER, encoding="utf-8")
    prova = app_check.esegui_piano(tmp_path, _piano(
        {"nome": "home", "tipo": "http", "argv": ["python", "app.py", "{port}"], "path": "/", "contiene": ["<title>Scadenze"]}))
    assert prova["stato"] == "riuscita", prova
    prova = app_check.esegui_piano(tmp_path, _piano(
        {"nome": "home", "tipo": "http", "argv": ["python", "app.py", "{port}"], "path": "/", "contiene": ["Pagina che non c'e'"]}))
    assert prova["stato"] == "fallita"


def test_un_server_che_muore_lo_dice(tmp_path):
    (tmp_path / "app.py").write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    prova = app_check.esegui_piano(tmp_path, _piano(
        {"nome": "home", "tipo": "http", "argv": ["python", "app.py"], "secondi": 5}))
    assert "uscito (exit 3)" in prova["errori"][0]


def test_una_pagina_statica_si_serve(tmp_path):
    (tmp_path / "index.html").write_text("<html><title>x</title></html>", encoding="utf-8")
    assert app_check.tipo_progetto(tmp_path)["html"]
    prova = app_check.esegui_piano(tmp_path, app_check.piano_html_statico(tmp_path),
                                   browser=lambda url: [])
    assert prova["stato"] == "riuscita", prova
    prova = app_check.esegui_piano(tmp_path, app_check.piano_html_statico(tmp_path),
                                   browser=lambda url: ["ReferenceError: x is not defined"])
    assert "console del browser" in prova["errori"][0]


def test_il_quality_gate_boccia_se_la_prova_fallisce(tmp_path):
    from devin.core.orchestrator import Orchestrator

    (tmp_path / "scadenze.py").write_text(CLI, encoding="utf-8")
    (tmp_path / "test_scadenze.py").write_text(
        "import scadenze\n\ndef test_main():\n    assert scadenze.main(['lista']) == 0\n", encoding="utf-8")

    class AI:
        def __init__(self, piano):
            self.piano, self.chiamate = piano, 0

        def local(self, messages, **kwargs):
            self.chiamate += 1
            return self.piano

    orch = Orchestrator.__new__(Orchestrator)
    orch.project_path = str(tmp_path)
    orch._prova_attiva, orch._piano_prova, orch._task_corrente = True, None, "promemoria scadenze"
    orch._log = lambda *a, **k: None
    orch.runner = None
    buono = json.dumps({"passi": [{"nome": "aggiungi", "tipo": "comando",
                                   "argv": ["python", "scadenze.py", "aggiungi", "Bollo", "2026-12-01"],
                                   "contiene": ["aggiunta Bollo"]}]})
    orch.ai_client = AI(buono)
    gate = orch._scaffold_quality_gate(["scadenze.py", "test_scadenze.py"], root=tmp_path)
    assert gate["status"] == "verified_success", gate["errors"]
    assert gate["app_check"]["stato"] == "riuscita"

    orch._piano_prova = None
    orch.ai_client = AI(buono.replace("aggiunta Bollo", "aggiunto Bollo"))
    gate = orch._scaffold_quality_gate(["scadenze.py", "test_scadenze.py"], root=tmp_path)
    assert gate["status"] == "verified_failure"
    assert any(e.startswith("prova dell'app:") for e in gate["errors"])
    # il piano si chiede una volta per run: la seconda verifica lo riusa
    orch._scaffold_quality_gate(["scadenze.py", "test_scadenze.py"], root=tmp_path)
    assert orch.ai_client.chiamate == 1


def test_un_piano_illeggibile_si_richiede_una_volta_poi_si_dichiara(tmp_path):
    from devin.core.orchestrator import Orchestrator

    (tmp_path / "scadenze.py").write_text(CLI, encoding="utf-8")
    orch = Orchestrator.__new__(Orchestrator)
    orch._piano_prova, orch._task_corrente = None, "x"
    orch._log = lambda *a, **k: None

    class AI:
        chiamate = 0

        def local(self, messages, **kwargs):
            AI.chiamate += 1
            return "boh"

    orch.ai_client = AI()
    prova = orch._prova_app(tmp_path, ["scadenze.py"])
    assert prova["stato"] == "saltata" and "non valido" in prova["motivo"]
    assert AI.chiamate == 2
