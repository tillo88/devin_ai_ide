"""Ogni richiesta al modello finisce su disco prima di partire (per ripeterla identica dopo un arresto)."""
import json
import os

from devin.ai.client import AIClient


def test_registra_e_tiene_solo_le_ultime(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVIN_RICHIESTE_DIR", str(tmp_path / "r"))
    client = AIClient.__new__(AIClient)
    for i in range(AIClient.RICHIESTE_TENUTE + 5):
        client._registra_richiesta("http://rig/v1/chat/completions", {"messages": [{"role": "user", "content": str(i)}]})
    file = sorted((tmp_path / "r").glob("*.json"))
    assert len(file) == AIClient.RICHIESTE_TENUTE
    ultimo = json.loads(file[-1].read_text())
    assert ultimo["url"].endswith("/v1/chat/completions") and ultimo["corpo"]["messages"][0]["content"] == "34"
    assert oct(os.stat(file[-1]).st_mode & 0o777) == "0o600"


def test_un_errore_di_scrittura_non_ferma_la_richiesta(tmp_path, monkeypatch, capsys):
    bloccato = tmp_path / "file"
    bloccato.write_text("non una cartella")
    monkeypatch.setenv("DEVIN_RICHIESTE_DIR", str(bloccato / "sotto"))
    AIClient.__new__(AIClient)._registra_richiesta("u", {"a": 1})
    assert "richiesta non registrata" in capsys.readouterr().out
