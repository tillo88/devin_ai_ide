"""Tests for StewardCoordinator: ties CS0+CS1+CS2 and derives the panel
snapshot from the deterministic core (CS3 DoD: panel has no own state).
"""
from devin.core.steward_coordinator import StewardCoordinator, SNAPSHOT_SCHEMA


def _history(n_pairs: int, size: int = 400):
    msgs = []
    for i in range(n_pairs):
        msgs.append({"role": "user", "content": "x" * size})
        msgs.append({"role": "assistant", "content": "y" * size})
    return msgs


def test_pressure_from_history_advances_state():
    c = StewardCoordinator(task_id="t")
    # Small history, large window -> low pressure -> IDLE.
    assert c.observe_history(_history(1), context_size=100_000) == "IDLE"
    # Large history, small window -> high pressure -> escalates.
    state = c.observe_history(_history(40), context_size=2000)
    assert state in {"COMPACTING", "CHECKPOINT_REQUIRED", "CONTROLLED_CONTINUATION"}


def test_snapshot_is_derived_from_core():
    c = StewardCoordinator(task_id="q36")
    c.observe_pressure(0.9, now=0.0)
    snap = c.snapshot(now=0.0)
    assert snap["schema"] == SNAPSHOT_SCHEMA
    assert snap["task_id"] == "q36"
    assert snap["state"] == "CHECKPOINT_REQUIRED"
    assert snap["pressure_pct"] == 90.0
    assert snap["compaction"]["allowed"] is True
    assert any("pressione contesto 90.0%" in f for f in snap["findings"])


def test_archive_evidence_surfaces_ref_without_body(tmp_path):
    c = StewardCoordinator(task_id="t", archive_dir=tmp_path / "sess")
    body = "SECRET raw benchmark output " * 40
    eid = c.archive_evidence(body, kind="benchmark",
                             claim="192K stable", status="unverified",
                             meta={"run_id": "R1"})
    assert eid and eid.startswith("sha256:")
    snap = c.snapshot()
    refs = snap["evidence_preserved"]
    assert len(refs) == 1
    assert refs[0]["claim"] == "192K stable"
    assert body not in str(refs)  # never the body in the snapshot


def test_risks_and_actions_are_caller_supplied():
    c = StewardCoordinator(task_id="t")
    c.note_risk("test 262K in corso: non riassumere come PASS")
    c.observe_pressure(0.85, now=0.0)
    c.mark_compacted(now=0.0)
    snap = c.snapshot(now=0.0)
    assert "test 262K in corso: non riassumere come PASS" in snap["risks"]
    assert any("compacted" in a for a in snap["actions"])


def test_loop_guard_passthrough():
    c = StewardCoordinator(task_id="t")
    assert c.register("cmd", "pytest", "-q") is True
    assert c.is_repeat("cmd", "pytest", "-q") is True
    assert c.register("cmd", "pytest", "-q") is False


def test_reset_clears_snapshot_and_budget(tmp_path):
    c = StewardCoordinator(task_id="t", archive_dir=tmp_path / "s")
    c.archive_evidence("x", kind="log", claim="c")
    c.note_risk("r")
    c.observe_pressure(0.9, now=0.0)
    c.mark_compacted(now=0.0)
    c.reset_task("t2")
    snap = c.snapshot(now=0.0)
    assert c.task_id == "t2"
    assert snap["evidence_preserved"] == []
    assert snap["risks"] == []
    assert snap["compaction"]["done"] == 0
