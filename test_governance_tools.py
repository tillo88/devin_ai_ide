"""Honest, read-only governance inventory for the desktop cockpit."""

from fastapi.testclient import TestClient


def _client():
    from devin.ui.fast_app import app

    return TestClient(app, client=("127.0.0.1", 5000))


def test_tool_registry_is_deny_by_default_and_does_not_invent_mcp_servers(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))      # nessun token: il server c'e' ma non e' pronto
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
    mcp = payload["external_mcp"]
    assert mcp["status"] == "configured"
    assert mcp["invocation_enabled"] is False
    assert [server["name"] for server in mcp["servers"]] == ["github"]
    assert mcp["servers"][0]["status"].startswith("non pronto: manca il file")


def test_mcp_server_with_its_token_file_is_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "devin").mkdir(parents=True)
    (tmp_path / ".config" / "devin" / "github-token").write_text("finto\n")
    from devin.ai import mcp_client
    monkeypatch.setattr(mcp_client, "prova_connessioni", lambda: {"github": "connesso: 25 strumenti"})
    mcp = _client().get("/api/tools/status").json()["external_mcp"]
    assert mcp["servers"][0]["connection"] == "connesso: 25 strumenti"
    assert mcp["status"] == "enabled" and mcp["invocation_enabled"] is True
    assert mcp["servers"][0]["status"] == "pronto"
    assert "finto" not in str(mcp)          # il segreto non esce mai


def test_tool_registry_reports_real_guards_budgets_and_disabled_terminal_input():
    payload = _client().get("/api/tools/status").json()
    tools = {item["tool_id"]: item for item in payload["tools"]}
    assert set(tools) == {
        "project_files",
        "change_manifest",
        "run_log",
        "knowledge_ingestion",
        "routing_plan",
        "local_workspace_process",
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
    assert tools["local_workspace_process"]["access"] == "approval_gated_execution"
    assert "explicit_human_confirmation" in tools["local_workspace_process"]["guards"]
    assert tools["local_workspace_process"]["budgets"]["max_timeout_seconds"] == 900
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
