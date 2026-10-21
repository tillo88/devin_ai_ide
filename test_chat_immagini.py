"""Immagini in chat (roadmap 2, 19/10): arrivano al modello solo se il server le vede.

Il modello del rig ha la torre visiva; il server la usa solo col proiettore
(``--mmproj``), e lo dice ``/props`` in ``modalities.vision``. Questi test fissano:
le immagini si riconoscono dai byte, finiscono nell'ultimo messaggio dell'utente in
formato OpenAI, nello storico resta una riga di testo, e un server che non vede
produce un errore chiaro invece di una risposta inventata.
"""
import asyncio
import base64
import io

import pytest
from starlette.datastructures import UploadFile

from devin.ui.routers import chat
from test_client_ragionamento import client  # noqa: F401 - fixture del client finto

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def test_le_immagini_si_riconoscono_dai_byte_non_dal_nome() -> None:
    assert chat._image_mime(PNG) == "image/png" and chat._image_mime(JPEG) == "image/jpeg"
    assert chat._image_mime(b"GIF89a....") == "image/gif" and chat._image_mime(b"BM....") == "image/bmp"
    assert chat._image_mime(WEBP) is None and chat._image_mime(b"testo") is None
    url = chat._image_data_url(PNG)
    assert url.startswith("data:image/png;base64,") and base64.b64decode(url.split(",", 1)[1]) == PNG
    nota = chat._image_note("C:\\foto\\pezzo.png", PNG)
    assert nota.startswith("[Immagine allegata: pezzo.png | image/png | size=40 bytes | sha256=")


def test_le_immagini_vanno_nell_ultimo_messaggio_dell_utente() -> None:
    messaggi = [{"role": "system", "content": "Sei DEVIN."}, {"role": "user", "content": "prima"},
                {"role": "assistant", "content": "ok"}, {"role": "user", "content": "che cos'e'?"}]
    out = chat._with_images(messaggi, ["data:image/png;base64,AAA"])
    assert out[:3] == messaggi[:3] and messaggi[3]["content"] == "che cos'e'?"   # l'originale non cambia
    assert out[3]["content"] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                                 {"type": "text", "text": "che cos'e'?"}]
    assert chat._with_images(messaggi, []) is messaggi
    with pytest.raises(ValueError):
        chat._with_images([{"role": "system", "content": "x"}], ["data:image/png;base64,AAA"])


def test_il_client_chiede_al_server_se_vede(client) -> None:  # noqa: F811
    stato = client._stato
    stato["props"] = {"modalities": {"vision": True, "audio": False}}
    assert client.vede_immagini("reasoning") == (True, "proiettore caricato")
    stato["props"] = {"modalities": {"vision": False}}
    vede, motivo = client.vede_immagini("reasoning")
    assert vede is False and "--mmproj" in motivo
    stato["props"] = {}
    assert client.vede_immagini("reasoning")[0] is False
    stato["props"] = None
    vede, motivo = client.vede_immagini("reasoning")
    assert vede is False and "/props" in motivo
    assert stato["props_chieste"] == 4, "nessuna cache: il server puo' cambiare"


def _upload(nome: str, dati: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(dati), filename=nome)


def test_gli_allegati_immagine_diventano_immagini_e_una_riga_nello_storico(monkeypatch) -> None:
    visti = []

    async def finta_api_chat(req):
        visti.append(req)
        return {"ok": True}

    monkeypatch.setattr(chat, "api_chat", finta_api_chat)
    out = asyncio.run(chat.api_chat_document(
        message="cosa vedi?", document=None,
        files=[_upload("pezzo.png", PNG), _upload("note.txt", b"misura 20 mm")],
        mode="auto", project_path="", use_web_search=False, chat_id="", reasoning_effort="",
        local_context=""))
    assert out == {"ok": True}
    (req,) = visti
    assert req.images == [chat._image_data_url(PNG)]
    assert "[Immagine allegata: pezzo.png | image/png" in req.message and "misura 20 mm" in req.message
    assert req.message.endswith("cosa vedi?") and "base64" not in req.message
    solo = asyncio.run(chat.api_chat_document(
        message="", document=None, files=[_upload("foto.jpg", JPEG)], mode="auto", project_path="",
        use_web_search=False, chat_id="", reasoning_effort="", local_context=""))
    assert solo == {"ok": True} and visti[-1].message.endswith("Descrivi l'immagine.")
    rifiutata = asyncio.run(chat.api_chat_document(
        message="", document=None, files=[_upload("foto.webp", WEBP)], mode="auto", project_path="",
        use_web_search=False, chat_id="", reasoning_effort="", local_context=""))
    assert "usa PNG o JPEG" in rifiutata["error"]


class _NonVede:
    config = {}

    def vede_immagini(self, mode):
        return False, "il server non ha il proiettore della visione (--mmproj)"


def test_un_server_che_non_vede_da_un_errore_chiaro(monkeypatch) -> None:
    from devin.ui import fast_app
    monkeypatch.setattr(fast_app, "_get_ai_client", lambda: _NonVede())
    monkeypatch.setattr(fast_app, "_get_launcher", lambda: None)
    req = chat.ChatRequest(message="che pezzo e'?", images=[chat._image_data_url(PNG)])
    out = asyncio.run(chat.api_chat(req))
    assert "non ha la visione accesa" in out["error"] and "--mmproj" in out["error"]


def test_immagini_non_valide_o_troppe_si_rifiutano_prima_del_modello(monkeypatch) -> None:
    from devin.ui import fast_app
    monkeypatch.setattr(fast_app, "_get_ai_client", lambda: pytest.fail("il modello non va chiamato"))
    troppe = chat.ChatRequest(message="x", images=[chat._image_data_url(PNG)] * (chat.MAX_CHAT_IMAGES + 1))
    assert "troppe immagini" in asyncio.run(chat.api_chat(troppe))["error"]
    finta = chat.ChatRequest(message="x", images=["https://example.com/a.png"])
    assert "solo data URL" in asyncio.run(chat.api_chat(finta))["error"]
    vecchia = chat.ChatRequest(message="x", image_base64=base64.b64encode(WEBP).decode())
    assert "formati ammessi" in asyncio.run(chat.api_chat(vecchia))["error"]


# --- 21/10: le immagini restano nella chat -------------------------------------
NOTA_JS = r"^\[Immagine allegata: (.+?) \| (image/[a-z]+) \| size=(\d+) bytes \| sha256=([0-9a-f]{16})\.\.\..*\]$"


def test_la_nota_e_quella_che_l_app_riconosce() -> None:
    """L'app ridisegna la miniatura dalla riga nello storico: formato e regex devono combaciare."""
    import hashlib
    import re
    from pathlib import Path
    js = Path(__file__).resolve().parent.joinpath("devin/ui/static/js/codex_app.js").read_text(encoding="utf-8")
    assert NOTA_JS.replace("/", "\\/").replace("^", "/^", 1) in js, "regex dell'app cambiata: aggiorna anche qui"
    trovata = re.match(NOTA_JS, chat._image_note("C:\\foto\\pezzo.png", PNG))
    assert trovata and trovata.group(1) == "pezzo.png" and trovata.group(2) == "image/png"
    assert trovata.group(4) == hashlib.sha256(PNG).hexdigest()[:16]


def test_l_immagine_allegata_si_salva_e_si_rivede(tmp_path, monkeypatch) -> None:
    from devin.ui import fast_app
    monkeypatch.setattr(fast_app, "_validated_project_path", lambda p, allow_general=False: str(tmp_path / "progetto"))
    monkeypatch.setattr(fast_app, "GENERAL_CHAT_PROJECT_KEY", str(tmp_path / "generale"))

    async def finta_api_chat(req):
        return {"ok": True}

    monkeypatch.setattr(chat, "api_chat", finta_api_chat)
    for progetto, chat_id in (("/w/progetto", "chat_1"), ("", "")):
        asyncio.run(chat.api_chat_document(
            message="cosa vedi?", document=None, files=[_upload("foto.jpg", JPEG)], mode="auto",
            project_path=progetto, use_web_search=False, chat_id=chat_id, reasoning_effort="", local_context=""))
        ident = __import__("hashlib").sha256(JPEG).hexdigest()[:16]
        risposta = asyncio.run(chat.api_chat_allegato(id=ident, project_path=progetto, chat_id=chat_id))
        assert risposta.status_code == 200 and risposta.body == JPEG and risposta.media_type == "image/jpeg"
    assert (tmp_path / "progetto" / ".devin" / "chats" / "allegati").is_dir(), "accanto allo storico della chat"
    assert (tmp_path / "generale" / ".devin_chat" / "allegati").is_dir()
    assert asyncio.run(chat.api_chat_allegato(id="../../etc/passwd")).status_code == 400
    assert asyncio.run(chat.api_chat_allegato(id="0" * 16)).status_code == 404
