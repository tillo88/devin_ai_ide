"""L'app desktop espone "Costruisci da questa chat" e lo collega all'endpoint giusto.

Il comportamento dell'endpoint (scaffold su progetto vuoto, patch altrimenti) e'
coperto dai test di /api/chat/generate_patch; qui si verifica il cablaggio, che
prima mancava del tutto nell'app desktop.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "devin/ui/templates/codex_app.html").read_text(encoding="utf-8")
JS = (ROOT / "devin/ui/static/js/codex_app.js").read_text(encoding="utf-8")


def test_goal_menu_offers_build_from_chat() -> None:
    goal_pop = HTML[HTML.index('data-pop="goal"'):]
    goal_pop = goal_pop[: goal_pop.index("</div>")]
    assert 'data-plus="build-from-chat"' in goal_pop


def test_build_from_chat_posts_the_selected_conversation() -> None:
    assert 'action === "build-from-chat"' in JS
    body = JS[JS.index("async function buildFromConversation"):]
    body = body[: body.index("\nfunction ")]
    assert '"/api/chat/generate_patch"' in body
    assert "project_path: state.selectedProjectPath" in body
    assert "chat_id: state.selectedChatId" in body
    assert "selectRun(payload.run_id)" in body


def test_build_from_chat_reports_the_outcome_in_the_chat_it_came_from() -> None:
    body = JS[JS.index("async function buildFromConversation"):]
    body = body[: body.index("\nfunction ")]
    assert "state.chatBuildRuns[payload.run_id]" in body
    esito = JS[JS.index("function esitoInChat"):]
    esito = esito[: esito.index("\n}\n")]
    assert "event.data?.summary?.chat_message" in esito
    assert "delete state.chatBuildRuns[event.run_id]" in esito
    finished = JS[JS.index('if (event.type === "run_finished") {'):]
    assert "esitoInChat(event);" in finished[:300]
