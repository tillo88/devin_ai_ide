"""Source-level contract for the status-first desktop cockpit."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "devin" / "ui" / "templates" / "codex_app.html"
SCRIPT = ROOT / "devin" / "ui" / "static" / "js" / "codex_app.js"
STYLE = ROOT / "devin" / "ui" / "static" / "css" / "codex_app.css"


def test_cockpit_exposes_lifecycle_model_context_and_goal_surfaces():
    html = TEMPLATE.read_text(encoding="utf-8")
    for element_id in (
        "gpu-slot-status",
        "active-model-label",
        "context-meter-label",
        "goal-panel",
        "goal-checklist",
        "goal-step-fill",
        "goal-time-fill",
        "goal-objective-input",
        "goal-criteria-draft",
        "goal-start-button",
        "goal-stop-button",
        "goal-event-feed",
        "goal-stream-status",
    ):
        assert f'id="{element_id}"' in html
    assert "Agent Swarm" in html
    assert "MCP Tools" in html


def test_cockpit_uses_structured_goal_and_health_apis_without_nvml_ui_claims():
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'fetchJson("/api/health")' in script
    assert 'fetchJson("/api/goal")' in script
    assert "renderGoalPanel(goals)" in script
    assert 'postJson("/api/goal/run"' in script
    assert "stopGoal(goalRunId)" in script
    assert "/events/stream?after_seq=" in script
    assert "TERMINAL_GOAL_EVENT_TYPES" in script
    assert "NVML: off · lifecycle safe" in script
    assert "nvidia-smi" not in script


def test_cockpit_css_keeps_status_and_goal_meters_bounded():
    css = STYLE.read_text(encoding="utf-8")
    assert ".topbar-telemetry" in css
    assert ".goal-budget-track" in css
    assert ".goal-summary[hidden]" in css
    assert ".goal-evidence" in css
    assert ".goal-event-feed" in css
    assert "max(0, min(1" not in css  # clamping belongs to JS, not CSS hacks


def test_cockpit_exposes_project_scoped_read_only_editor():
    html = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")
    for element_id in (
        "project-file-tree",
        "project-tree-status",
        "show-chat-view",
        "show-editor-view",
        "editor-workspace",
        "editor-file-path",
        "editor-content",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/project/tree" in script
    assert "/api/project/file" in script
    assert "/api/file/save" not in script
    assert "read-only" in html
    assert '.workstream-panel[data-center-view="editor"]' in css


def test_cockpit_exposes_verified_manifest_diff_without_arbitrary_writer():
    html = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")
    for element_id in (
        "show-diff-view",
        "manifest-diff-workspace",
        "manifest-diff-run",
        "manifest-diff-digest",
        "manifest-file-rail",
        "manifest-diff-rows",
        "manifest-diff-apply",
        "manifest-diff-reject",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/run/changes/" in script
    assert "expected_entry_digest" in script
    assert "change_manifest_v1" in script
    assert "/api/diff/apply" not in script
    assert "diff-input" not in html
    assert '.workstream-panel[data-center-view="diff"]' in css


def test_cockpit_exposes_bounded_central_log_with_structured_faults():
    html = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")
    for element_id in (
        "show-log-view",
        "run-log-workspace",
        "run-log-output",
        "structured-fault-list",
        "hide-known-warnings",
        "open-run-log-workspace",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/terminal/output" in script
    assert "structuredFaults" in script
    assert "filterLogRows" in script
    assert "/api/terminal/input" not in script
    assert '.workstream-panel[data-center-view="log"]' in css
    assert "activity-log-details" not in html


def test_cockpit_exposes_honest_central_governance_workspace():
    html = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")
    for element_id in (
        "show-governance-view",
        "governance-workspace",
        "governance-policy-badge",
        "governance-mcp-status",
        "governance-agent-grid",
        "governance-dispatch-summary",
        "governance-dispatch-list",
        "governance-tool-list",
        "governance-knowledge-counts",
        "governance-council-axes",
    ):
        assert f'id="{element_id}"' in html
    assert 'fetchJson("/api/tools/status")' in script
    assert 'fetchJson("/api/operations/active")' in script
    assert "renderCentralGovernance" in script
    assert "future-disabled" in script
    assert "needs_evidence" in script
    assert "nessun ruolo dedotto" in script
    assert "built-in ≠ MCP" in html
    assert '.workstream-panel[data-center-view="governance"]' in css
    assert "/api/terminal/input" not in script
