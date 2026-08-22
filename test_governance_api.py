from fastapi.testclient import TestClient

from devin.ui import fast_app


def test_governance_status_routes_and_desktop_panel(monkeypatch, tmp_path):
    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path / "workspace")
    client = TestClient(fast_app.app)

    knowledge = client.get("/api/knowledge-exchange/status")
    assert knowledge.status_code == 200
    assert knowledge.json()["raw_store_shared"] is False

    council = client.get("/api/council/status")
    assert council.status_code == 200
    assert len(council.json()["axes"]) == 5
    assert council.json()["automatic_promotion"] is False

    routing = client.get("/api/routing/status")
    assert routing.status_code == 200
    assert routing.json()["automatic_switch"] is False
    assert routing.json()["roles"]["hermes"]["enabled"] is False

    page = client.get("/app")
    assert page.status_code == 200
    assert "Governance agente" in page.text
    assert "routing-preview-button" in page.text


def test_routing_plan_endpoint_never_switches_model():
    client = TestClient(fast_app.app)
    response = client.post(
        "/api/routing/plan", json={"capability": "coding", "resident_role": "clippy"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["target_role"] == "devin"
    assert result["activation_required"] is True
    assert result["automatic_switch"] is False
