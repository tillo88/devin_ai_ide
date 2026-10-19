"""La vista negli strumenti (roadmap 2, 19/10): screenshot e immagini del progetto al modello.

Fissa: solo file ammessi e riconosciuti dai byte; lo screenshot di guarda_pagina e
guarda_immagine arrivano come messaggio dell'utente subito dopo i risultati degli
strumenti; le immagini vecchie diventano testo; senza visione non cambia niente.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile

from devin.core import vista
from test_tool_agent import AIFinta, _agente, chiamata

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def test_carica_solo_immagini_vere_dentro_le_cartelle_ammesse(tmp_path) -> None:
    (tmp_path / "grafico.png").write_bytes(PNG)
    url, motivo = vista.carica("grafico.png", [tmp_path], base=tmp_path)
    assert motivo == "" and url == "data:image/png;base64," + base64.b64encode(PNG).decode()
    (tmp_path / "finta.png").write_bytes(b"<svg/>")
    assert vista.carica("finta.png", [tmp_path], base=tmp_path) == (None, "non e' un PNG, JPEG, GIF o BMP")
    fuori = tmp_path.parent / "fuori.png"
    fuori.write_bytes(PNG)
    assert vista.carica(str(fuori), [tmp_path])[1] == "fuori dalle cartelle ammesse"
    assert vista.carica("../fuori.png", [tmp_path], base=tmp_path)[1] == "fuori dalle cartelle ammesse"
    (tmp_path / "link.png").symlink_to(fuori)
    assert vista.carica("link.png", [tmp_path], base=tmp_path)[1] == "e' un collegamento simbolico"
    assert vista.carica("manca.png", [tmp_path], base=tmp_path)[1] == "file non trovato"
    assert vista.carica("", [tmp_path])[1] == "percorso vuoto"


def test_gli_screenshot_temporanei_di_occhi_si_ammettono_solo_se_chiesti() -> None:
    cartella = Path(tempfile.mkdtemp(prefix=vista.PREFISSO_OCCHI))
    scatto = cartella / "pagina-1.png"
    scatto.write_bytes(PNG)
    assert vista.carica(str(scatto), [])[1] == "fuori dalle cartelle ammesse"
    assert vista.carica(str(scatto), [], screenshot_temporanei=True)[0] is not None
    altra = Path(tempfile.mkdtemp(prefix="altro-"))
    (altra / "x.png").write_bytes(PNG)
    assert vista.carica(str(altra / "x.png"), [], screenshot_temporanei=True)[0] is None


def test_messaggio_e_alleggerimento() -> None:
    assert vista.messaggio([]) is None
    m = vista.messaggio([("data:image/png;base64,AAA", "screenshot di x")])
    assert m["role"] == "user" and m["content"][0]["type"] == "image_url"
    assert vista.SEGNO in m["content"][1]["text"] and "- screenshot di x" in m["content"][1]["text"]
    messaggi = [{"role": "system", "content": "s"}] + [vista.messaggio([(f"data:image/png;base64,{i}", f"n{i}")])
                                                       for i in range(4)]
    assert vista.alleggerisci(messaggi, tieni=2) == 2
    assert isinstance(messaggi[1]["content"], str) and "gia' viste" in messaggi[1]["content"]
    assert isinstance(messaggi[3]["content"], list) and isinstance(messaggi[4]["content"], list)
    assert vista.caratteri(messaggi[4]["content"]) < 2000 + len(messaggi[4]["content"][1]["text"])


def test_screenshot_di_legge_il_risultato_di_occhi() -> None:
    assert vista.screenshot_di("pagina: X\nscreenshot: /tmp/devin-occhi-a/pagina-1.png\ntesto") == \
        "/tmp/devin-occhi-a/pagina-1.png"
    assert vista.screenshot_di("ERRORE: browser non disponibile") is None


class AIVede(AIFinta):
    def chiama_con_strumenti(self, messages, tools, **kwargs):
        self.strumenti = [t["function"]["name"] for t in tools]
        return super().chiama_con_strumenti(messages, tools, **kwargs)


def test_l_agente_che_vede_riceve_lo_screenshot_e_le_immagini(tmp_path) -> None:
    (tmp_path / "logo.png").write_bytes(PNG)

    def guarda(url, **kw):
        cartella = kw["cartella_screenshot"]
        cartella.mkdir(parents=True, exist_ok=True)
        (cartella / "pagina-1.png").write_bytes(PNG)
        return f"pagina: Demo — {url} (HTTP 200)\nscreenshot: {cartella / 'pagina-1.png'}\ntesto visibile: ciao"

    ai = AIVede([
        [chiamata("guarda_pagina", url="http://127.0.0.1:8000/")],
        [chiamata("guarda_immagine", percorso="logo.png"), chiamata("guarda_immagine", percorso="/etc/passwd")],
        "fatto",
    ])
    agente, _ = _agente(tmp_path, ai, guarda=guarda, vede=True)
    esito = agente.esegui("controlla la pagina")
    assert esito.motivo == "risposta" and "guarda_immagine" in ai.strumenti
    assert "VISIONE" in ai.visti[0][0]["content"]
    secondo = ai.visti[1]
    assert secondo[-2]["role"] == "tool" and "prossimo messaggio" in secondo[-2]["content"]
    assert secondo[-1]["role"] == "user" and secondo[-1]["content"][0]["type"] == "image_url"
    assert "screenshot di http://127.0.0.1:8000/" in secondo[-1]["content"][-1]["text"]
    terzo = ai.visti[2]
    risultati = [m["content"] for m in terzo if m["role"] == "tool"]
    assert risultati[-2].startswith("ok") and risultati[-1].startswith("ERRORE: immagine non mostrabile")
    assert sum(1 for m in terzo if isinstance(m.get("content"), list)) == 2


def test_senza_visione_niente_cambia(tmp_path) -> None:
    def guarda(url, **kw):
        return f"pagina: Demo\nscreenshot: {tmp_path / 'x.png'}\n"

    ai = AIVede([[chiamata("guarda_pagina", url="http://127.0.0.1:8000/")], "fatto"])
    agente, _ = _agente(tmp_path, ai, guarda=guarda)
    agente.esegui("guarda")
    assert "guarda_immagine" not in ai.strumenti
    assert not any(isinstance(m.get("content"), list) for turno in ai.visti for m in turno)
    assert "prossimo messaggio" not in json.dumps(ai.visti[-1])
