from pathlib import Path

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
    assert council.json()["colibri_model_agnostic"] is True
    assert {item["family"] for item in council.json()["manual_reviewer_templates"]} == {
        "openai", "anthropic", "google",
    }

    routing = client.get("/api/routing/status")
    assert routing.status_code == 200
    assert routing.json()["automatic_switch"] is False
    assert routing.json()["roles"]["hermes"]["enabled"] is False

    tools = client.get("/api/tools/status")
    assert tools.status_code == 200
    local_process = next(
        item for item in tools.json()["tools"]
        if item["tool_id"] == "local_workspace_process"
    )
    assert local_process["access"] == "approval_gated_execution"
    assert "sanitized_environment_without_secrets" in local_process["guards"]

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


def test_council_manual_bundle_and_colibri_batch_are_operator_gated():
    client = TestClient(fast_app.app)
    safe = {
        "attempt_id": "attempt-fixture",
        "prompt": "Check the bounded change",
        "response": "Implemented with tests",
        "evidence": [{"evidence_id": "sha256:" + "a" * 64}],
    }
    reviewers = [
        {
            "reviewer_id": "manual-codex", "family": "openai", "local": False,
            "axes": ["correttezza_concettuale", "vincoli"],
        },
        {
            "reviewer_id": "manual-claude", "family": "anthropic", "local": False,
            "axes": ["robustezza", "sicurezza"],
        },
        {
            "reviewer_id": "manual-gemini", "family": "google", "local": False,
            "axes": ["qualita"],
        },
    ]
    plan_response = client.post("/api/council/plans", json={
        "reviewers": reviewers,
        "external_consent": True,
        "evidence_packet": {
            **safe,
            "external_packet": safe,
            "redaction_manifest": {"approved": True, "automatic_send": False},
        },
    })
    plan = plan_response.json()
    assert plan_response.status_code == 200
    assert plan["coverage_complete"] is True

    bundle = client.post("/api/council/manual/bundle", json={"plan": plan}).json()
    assert bundle["automatic_send"] is False
    assert bundle["operator_copy_required"] is True
    assert len(bundle["prompts"]) == 5

    batch = client.post("/api/council/colibri/batch", json={
        "runtime": {
            "engine": "colibri",
            "model_id": "Qwen3.8-Flash-Next",
            "family": "qwen",
            "revision": "sha256:" + "c" * 64,
        },
        "results": [{
            "schema": "devin_council_result_v1",
            "result_id": "crr_fixture",
            "plan_id": plan["plan_id"],
            "outcome": "verified_success_candidate",
            "arbiter_axes": [],
            "failures": [],
            "verdicts": [],
        }],
    }).json()
    assert batch["automatic_start"] is False
    assert batch["runtime"]["model_id"] == "Qwen3.8-Flash-Next"
    assert batch["promotion_performed"] is False


def test_council_prepare_uses_training_store_and_requires_redaction_approval(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(fast_app, "WORKSPACE_DIR", tmp_path / "workspace")
    client = TestClient(fast_app.app)
    case = client.post("/api/training/cases", json={
        "task": "Review this local agent answer",
        "expected_signals": ["tests_pass"],
    }).json()["case"]
    attempt = client.post("/api/training/attempts", json={
        "case_id": case["case_id"],
        "prompt": "password=hunter2 inspect C:\\Users\\alice\\repo",
        "response": "Completed with evidence",
        "status": "pending_review",
        "tests": {"quality_gate": {"status": "passed", "tests_run": 4}},
    }).json()["attempt"]

    preview = client.post("/api/council/manual/prepare", json={
        "attempt_id": attempt["attempt_id"],
    }).json()
    assert preview["approval_required"] is True
    assert preview["redaction_manifest"]["approved"] is False
    assert "hunter2" not in str(preview)

    prepared = client.post("/api/council/manual/prepare", json={
        "attempt_id": attempt["attempt_id"],
        "redaction_approved": True,
    }).json()
    assert prepared["preview"]["redaction_manifest"]["approved"] is True
    assert prepared["bundle"]["automatic_send"] is False
    assert len(prepared["bundle"]["prompts"]) == 5
    assert prepared["promotion_performed"] is False


def test_diagnostics_wires_two_gate_manual_council_copy_flow():
    template = Path("devin/ui/templates/codex_diagnostics.html").read_text(encoding="utf-8")
    script = Path("devin/ui/static/js/codex_diagnostics.js").read_text(encoding="utf-8")
    assert 'id="council-manual-preview"' in template
    assert 'id="council-manual-approve"' in template
    assert 'id="council-manual-bundle"' in template
    assert 'data-council-attempt=' in script
    assert '"/api/council/manual/prepare"' in script
    assert "redaction_approved: false" in script
    assert "redaction_approved: true" in script
    assert "navigator.clipboard.writeText(prompt)" in script
