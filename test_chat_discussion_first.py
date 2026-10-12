"""Una richiesta di dialogo vince sullo smistamento automatico verso le run.

Il 12/10, su un progetto vuoto, il messaggio "Vorrei costruire da zero ...
Prima di scrivere codice, fammi le domande che ti servono e proponimi come lo
struttureresti" ha avviato uno scaffold: "da zero" bastava.
"""
from __future__ import annotations

import asyncio

from devin.ui.routers import chat as chat_router

MESSAGGIO_12_10 = (
    "Ciao DEVIN. Vorrei costruire da zero un piccolo registro delle spese personali in "
    "Python: da riga di comando aggiungo una spesa (data, importo, categoria, nota), le "
    "elenco filtrando per mese o categoria e vedo il totale per categoria. Dati salvati in "
    "un file locale. Prima di scrivere codice, fammi le domande che ti servono e proponimi "
    "come lo struttureresti."
)


def test_message_of_12_october_does_not_start_a_scaffold(tmp_path) -> None:
    empty = tmp_path / "registro-spese"
    empty.mkdir()
    assert chat_router._wants_discussion_first(MESSAGGIO_12_10)
    assert not chat_router._is_scaffold_request(MESSAGGIO_12_10, str(empty))


def test_plain_build_request_still_scaffolds(tmp_path) -> None:
    empty = tmp_path / "vuoto"
    empty.mkdir()
    for message in (
        "Crea un'applicazione Steam Profile Checker MVP con tests.py e file reali",
        "Costruisci da zero un registro spese in Python con test",
    ):
        assert not chat_router._wants_discussion_first(message), message
        assert chat_router._is_scaffold_request(message, str(empty)), message


def test_discussion_phrases() -> None:
    for message in (
        "Prima di iniziare parliamone",
        "Non scrivere codice, ragioniamo insieme sull'architettura",
        "Hai domande prima di partire?",
        "Mettiamo giù le idee per un'app di note",
        "Before writing anything, ask me what you need",
    ):
        assert chat_router._wants_discussion_first(message), message


def test_existing_project_discussion_does_not_start_maintenance(tmp_path, monkeypatch) -> None:
    from devin.ui import fast_app

    project = tmp_path / "calculator"
    project.mkdir()
    (project / "calculator.py").write_text("print('old')\n", encoding="utf-8")

    async def forbidden_run(_req):
        raise AssertionError("a discussion request must not start a run")

    async def forbidden_scaffold(_req):
        raise AssertionError("a discussion request must not start a scaffold")

    monkeypatch.setattr(fast_app, "_validated_project_path", lambda path, allow_general=False: path)
    monkeypatch.setattr(chat_router, "api_run", forbidden_run)
    monkeypatch.setattr(chat_router, "api_chat_scaffold", forbidden_scaffold)

    class Stop(Exception):
        pass

    def stop_at_chat(*_args, **_kwargs):
        raise Stop()

    # Dopo lo smistamento la chat normale sceglie la modalita': ci fermiamo li'.
    monkeypatch.setattr(chat_router, "_detect_mode", stop_at_chat)
    message = "Rendi la UI/UX più moderna e modifica la GUI, ma prima di toccare il codice parliamone"
    try:
        asyncio.run(chat_router.api_chat(chat_router.ChatRequest(
            message=message, project_path=str(project),
        )))
    except Stop:
        reached_chat = True
    else:
        reached_chat = False
    assert reached_chat, "the discussion request never reached the normal chat path"
