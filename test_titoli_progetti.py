"""Il nome dei progetti collegati dal PC (22/10): "spyengine", non "spyengine-1e0dd3e5"."""
from __future__ import annotations

from devin.ui.routers.workspace import _titoli


def test_la_cartella_del_pc_si_chiama_come_sul_pc():
    progetti = [
        {"name": "spyengine-1e0dd3e5", "local_workspace": {"mode": "direct", "display_name": "spyengine"}},
        {"name": "General", "local_workspace": None},
    ]
    _titoli(progetti)
    assert [p["title"] for p in progetti] == ["spyengine", "General"]


def test_se_il_nome_e_doppio_si_distingue_quello_del_pc():
    progetti = [
        {"name": "spyengine"},
        {"name": "spyengine-1e0dd3e5", "local_workspace": {"mode": "direct", "display_name": "SpyEngine"}},
    ]
    _titoli(progetti)
    assert [p["title"] for p in progetti] == ["spyengine", "SpyEngine (PC)"]


def test_l_elenco_dei_progetti_porta_il_titolo(tmp_path, monkeypatch):
    import asyncio
    import json

    from devin.ui import fast_app
    from devin.ui.routers import workspace as workspace_router
    progetto = tmp_path / "spyengine-1e0dd3e5"
    (progetto / ".devin").mkdir(parents=True)
    (progetto / ".devin" / "local_workspace.json").write_text(json.dumps({
        "schema": "devin_local_workspace_snapshot_v1", "mode": "direct", "bridge_id": "1e0dd3e5-0000-4000-8000-000000000000",
        "display_name": "spyengine"}), encoding="utf-8")
    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(fast_app, "_LINKED_PROJECT_ROOTS", [])
    risposta = asyncio.run(workspace_router.api_workspace_projects())
    (voce,) = [p for p in risposta["projects"] if p["path"] == str(progetto)]
    assert voce["title"] == "spyengine" and voce["name"] == "spyengine-1e0dd3e5"


def test_l_overview_conta_i_messaggi_della_chat_default(tmp_path, monkeypatch):
    """22/10: per chiedere «togli anche il progetto?» conta anche la chat «Project default»."""
    import asyncio

    from devin.core.chat_persistence import ChatPersistence
    from devin.ui import fast_app
    from devin.ui.routers import projects as projects_router
    progetto = tmp_path / "alpha"
    progetto.mkdir()
    monkeypatch.setattr(fast_app, "_project_space_for", lambda p: __import__(
        "devin.core.project_space", fromlist=["ProjectSpace"]).ProjectSpace(str(progetto)))
    vuoto = asyncio.run(projects_router.api_project_overview(project_path=str(progetto), lite=True))
    assert vuoto["default_chat_messages"] == 0
    ChatPersistence(str(progetto)).append("user", "ciao")
    pieno = asyncio.run(projects_router.api_project_overview(project_path=str(progetto), lite=True))
    assert pieno["default_chat_messages"] == 1
    generale = asyncio.run(projects_router.api_project_overview(project_path="", lite=True))
    assert "default_chat_messages" not in generale
