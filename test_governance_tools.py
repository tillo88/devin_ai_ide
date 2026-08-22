"""Honest, read-only governance inventory for the desktop cockpit."""

from fastapi.testclient import TestClient


def _client():
    from devin.ui.fast_app import app

    return TestClient(app, client=("127.0.0.1", 5000))


def test_tool_registry_is_deny_by_default_and_does_not_invent_mcp_servers():
    response = _client().get("/api/tools/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "devin_tool_registry_v1"
    assert payload["policy"] == {
        "default": "deny",
        "scope": "registered_surfaces_only",
        "mutations": "explicit_human_action",
        "model_execution": False,
    }
    assert payload["external_mcp"] == {
        "status": "unconfigured",
        "registered_count": 0,
        "servers": [],
        "invocation_enabled": False,
    }


def test_tool_registry_reports_real_guards_budgets_and_disabled_terminal_input():
    payload = _client().get("/api/tools/status").json()
    tools = {item["tool_id"]: item for item in payload["tools"]}
    assert set(tools) == {
        "project_files",
        "change_manifest",
        "run_log",
        "knowledge_ingestion",
        "routing_plan",
        "terminal_input",
    }
    assert tools["project_files"]["access"] == "read_only"
    assert tools["project_files"]["budgets"]["max_files"] == 1500
    assert tools["change_manifest"]["access"] == "review_gated_mutation"
    assert "entry_digest_required_for_decision" in tools["change_manifest"]["guards"]
    assert tools["run_log"]["budgets"] == {
        "max_lines": 1000,
        "max_tail_bytes": 512_000,
    }
    assert tools["knowledge_ingestion"]["budgets"]["max_upload_bytes"] == 20 * 1024 * 1024
    assert tools["routing_plan"]["access"] == "plan_only"
    assert tools["terminal_input"]["status"] == "disabled_placeholder"
    assert tools["terminal_input"]["access"] == "none"


def test_routing_status_exposes_eligibility_without_claiming_runtime_activity():
    payload = _client().get("/api/routing/status").json()
    assert payload["automatic_switch"] is False
    assert payload["roles"]["clippy"]["enabled"] is True
    assert payload["roles"]["devin"]["enabled"] is True
    assert payload["roles"]["hermes"]["future"] is True
    assert payload["roles"]["hermes"]["enabled"] is False
    assert payload["roles"]["teacher"]["future"] is True
    assert {
        role["lifecycle_owner"] for role in payload["roles"].values()
    } == {"ai-rig-model-slot"}
    assert "running" not in payload
    assert "resident_role" not in payload
