"""Regression per /api/workspace/projects/remove (2026-07-21).

Rimozione progetto dalla sidebar: interno -> cestino workspace/_trash
(mai delete permanente); collegato -> unlink senza toccare i file;
path esterni/traversal -> rifiutati.
"""
from fastapi.testclient import TestClient


def test_remove_internal_project_moves_to_trash(tmp_path, monkeypatch):
    from devin.ui import fast_app

    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path)
    project = tmp_path / "demo"
    project.mkdir()
    (project / "file.txt").write_text("contenuto", encoding="utf-8")

    client = TestClient(fast_app.app)
    body = client.post("/api/workspace/projects/remove",
                       json={"path": str(project)}).json()

    assert body["status"] == "trashed"
    assert not project.exists()
    trashed = list((tmp_path / "_trash").iterdir())
    assert len(trashed) == 1
    assert trashed[0].name.startswith("demo-")
    assert (trashed[0] / "file.txt").read_text(encoding="utf-8") == "contenuto"


def test_remove_rejects_external_and_reserved_paths(tmp_path, monkeypatch):
    from devin.ui import fast_app

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    reserved = workspace / "_trash"
    reserved.mkdir()

    client = TestClient(fast_app.app)
    assert "error" in client.post("/api/workspace/projects/remove",
                                  json={"path": str(outside)}).json()
    assert outside.exists()
    assert "error" in client.post("/api/workspace/projects/remove",
                                  json={"path": str(reserved)}).json()
    assert reserved.exists()
    assert "error" in client.post("/api/workspace/projects/remove",
                                  json={"path": ""}).json()


def test_remove_linked_project_unlinks_without_touching_files(tmp_path, monkeypatch):
    from devin.ui import fast_app

    external = tmp_path / "linked_proj"
    external.mkdir()
    resolved = external.resolve()
    monkeypatch.setattr(fast_app, "_LINKED_PROJECT_ROOTS", [resolved])
    fast_app._ALLOWED_ROOTS.add(resolved)
    try:
        client = TestClient(fast_app.app)
        body = client.post("/api/workspace/projects/remove",
                           json={"path": str(external)}).json()

        assert body["status"] == "unlinked"
        assert external.exists()
        assert resolved not in fast_app._LINKED_PROJECT_ROOTS
        assert resolved not in fast_app._ALLOWED_ROOTS
    finally:
        fast_app._ALLOWED_ROOTS.discard(resolved)
