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
