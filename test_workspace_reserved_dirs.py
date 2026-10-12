"""Le cartelle interne del workspace non compaiono fra i progetti (docs_cache, 12/10)."""
from __future__ import annotations

import asyncio


def test_internal_workspace_dirs_are_not_projects(tmp_path, monkeypatch) -> None:
    from devin.ui import fast_app
    from devin.ui.routers import workspace

    for name in ("registro-spese", "docs_cache", "sandbox", "sandboxes", "_tmp", ".git"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path)
    # Altri test registrano progetti collegati in questa struttura globale.
    linked = fast_app._LINKED_PROJECT_ROOTS
    monkeypatch.setattr(fast_app, "_LINKED_PROJECT_ROOTS", type(linked)())
    result = asyncio.run(workspace.api_workspace_projects())
    projects = result["projects"] if isinstance(result, dict) else result
    assert [p["name"] for p in projects] == ["registro-spese"]
