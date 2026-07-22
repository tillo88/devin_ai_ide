"""CS3 e2e: /api/steward/status returns a snapshot derived from the core.

The panel endpoint must be read-only and reflect the deterministic core over
the current chat history (no own state).
"""
from fastapi.testclient import TestClient

from devin.ui import fast_app


def test_steward_status_endpoint_returns_snapshot():
    client = TestClient(fast_app.app)
    # No project/chat -> empty history -> IDLE, well-formed snapshot.
    resp = client.get("/api/steward/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "context_steward_snapshot_v1"
    assert body["state"] == "IDLE"
    assert body["pressure"] < 0.01  # empty history -> ~0 (min 1 token estimate)
    assert body["history_messages"] == 0
    assert body["context_size"] >= 512
    assert isinstance(body["findings"], list)
    assert isinstance(body["evidence_preserved"], list)
    assert body["compaction"]["done"] == 0
