"""CS0 tests for the deterministic Context Steward core.

Covers: pressure state machine, hysteresis (no flapping), compaction gating
(cooldown, min pressure drop, per-task cap), loop guard, task reset, and
crash-resume serialization. No GPU, no LLM.
"""
from devin.core.context_steward import (
    ContextSteward,
    StewardConfig,
    fingerprint,
)


def test_pressure_bands_escalate():
    s = ContextSteward(task_id="t")
    assert s.observe(0.10) == "IDLE"
    assert s.observe(0.66) == "WATCHING"
    assert s.observe(0.76) == "PREPARING"
    assert s.observe(0.83) == "COMPACTING"
    assert s.observe(0.89) == "CHECKPOINT_REQUIRED"
    assert s.observe(0.95) == "CONTROLLED_CONTINUATION"


def test_hysteresis_prevents_flapping():
    """Just below a boundary must NOT immediately downgrade: pressure has to
    fall past the hysteresis margin first."""
    cfg = StewardConfig(hysteresis=0.05)
    s = ContextSteward(task_id="t", config=cfg)
    s.observe(0.83)  # COMPACTING (band lower edge 0.82)
    # 0.80 is below the band but within the hysteresis margin (0.82 - 0.05).
    assert s.observe(0.80) == "COMPACTING"
    # Falls past the margin -> steps down one level (PREPARING).
    assert s.observe(0.76) == "PREPARING"


def test_downgrade_is_one_step_at_a_time():
    s = ContextSteward(task_id="t")
    s.observe(0.95)  # CONTROLLED_CONTINUATION
    # A big drop in one observation should not skip observable states.
    assert s.observe(0.10) == "CHECKPOINT_REQUIRED"
    assert s.observe(0.10) == "COMPACTING"
    assert s.observe(0.10) == "PREPARING"
    assert s.observe(0.10) == "WATCHING"
    assert s.observe(0.10) == "IDLE"


def test_compaction_requires_compacting_state():
    s = ContextSteward(task_id="t")
    s.observe(0.70)  # WATCHING
    assert s.should_compact(now=0.0) is False
    s.observe(0.83)  # COMPACTING
    assert s.should_compact(now=0.0) is True


def test_cooldown_and_pressure_drop_break_loop():
    cfg = StewardConfig(cooldown_seconds=600, min_pressure_drop=0.08)
    s = ContextSteward(task_id="t", config=cfg)
    s.observe(0.83, now=0.0)
    assert s.should_compact(now=0.0) is True
    s.mark_compacted(now=0.0)

    # Immediately after: cooldown active.
    s.observe(0.84, now=10.0)
    allowed, reason = s.compaction_decision(now=10.0)
    assert allowed is False and reason == "cooldown_active"

    # Cooldown elapsed but pressure barely moved -> still blocked.
    s.observe(0.84, now=700.0)
    allowed, reason = s.compaction_decision(now=700.0)
    assert allowed is False and reason == "insufficient_pressure_drop"

    # Cooldown elapsed AND pressure dropped enough -> allowed again.
    s.observe(0.70, now=800.0)  # WATCHING now, so also below threshold...
    # bring it back into compacting band but clearly dropped from 0.83
    s.observe(0.83, now=1300.0)
    allowed, reason = s.compaction_decision(now=1300.0)
    # pressure_at_last_compaction was 0.83; last_pressure 0.83 -> drop 0.0.
    # Force a real drop scenario:
    s2 = ContextSteward(task_id="t2", config=cfg)
    s2.observe(0.90, now=0.0)
    s2.mark_compacted(now=0.0)          # compacted at 0.90
    s2.observe(0.81, now=700.0)         # dropped 0.09 >= 0.08, cooldown passed
    s2.observe(0.83, now=700.0)         # back into compacting band, drop 0.07 < 0.08
    allowed2, reason2 = s2.compaction_decision(now=700.0)
    assert allowed2 is False and reason2 == "insufficient_pressure_drop"
    s2.observe(0.80, now=800.0)         # drop 0.10 >= 0.08
    # 0.80 is below compacting(0.82) but within hysteresis of COMPACTING, so
    # state stays COMPACTING; decision should now allow.
    allowed3, reason3 = s2.compaction_decision(now=800.0)
    assert allowed3 is True


def test_max_compactions_per_task():
    cfg = StewardConfig(cooldown_seconds=0, min_pressure_drop=0.0, max_compactions_per_task=2)
    s = ContextSteward(task_id="t", config=cfg)
    s.observe(0.90, now=0.0)
    assert s.should_compact(now=0.0)
    s.mark_compacted(now=0.0)
    s.observe(0.90, now=1.0)
    assert s.should_compact(now=1.0)
    s.mark_compacted(now=1.0)
    s.observe(0.90, now=2.0)
    allowed, reason = s.compaction_decision(now=2.0)
    assert allowed is False and reason == "max_compactions_reached"


def test_checkpoint_required_is_mandatory_reason():
    s = ContextSteward(task_id="t")
    s.observe(0.90, now=0.0)  # CHECKPOINT_REQUIRED
    allowed, reason = s.compaction_decision(now=0.0)
    assert allowed is True and reason == "checkpoint_required"


def test_loop_guard_blocks_repeats():
    s = ContextSteward(task_id="t")
    fp = fingerprint("error", "ModuleNotFoundError: foo")
    assert s.register("error", fp) is True     # first time: recorded
    assert s.is_repeat("error", fp) is True
    assert s.register("error", fp) is False     # repeat: blocked
    # A different fingerprint of the same kind is allowed (new evidence).
    fp2 = fingerprint("error", "ModuleNotFoundError: bar")
    assert s.register("error", fp2) is True


def test_reset_task_clears_budget_and_loop_memory():
    cfg = StewardConfig(cooldown_seconds=0, min_pressure_drop=0.0, max_compactions_per_task=1)
    s = ContextSteward(task_id="t", config=cfg)
    s.observe(0.90, now=0.0)
    s.mark_compacted(now=0.0)
    s.register("cmd", fingerprint("pytest -q"))
    s.reset_task("t-next")
    assert s.task_id == "t-next"
    assert s.compactions_done == 0
    assert s.state == "IDLE"
    assert s.is_repeat("cmd", fingerprint("pytest -q")) is False


def test_config_from_settings_block():
    settings = {"context_steward": {"hysteresis": 0.1, "max_compactions_per_task": 7,
                                     "unknown_key": "ignored"}}
    cfg = StewardConfig.from_settings(settings)
    assert cfg.hysteresis == 0.1
    assert cfg.max_compactions_per_task == 7
    # Missing keys keep conservative defaults.
    assert cfg.compacting == 0.82
    # Absent block -> all defaults, no crash.
    assert StewardConfig.from_settings({}).watching == 0.65
    assert StewardConfig.from_settings(None).watching == 0.65


def test_serialization_round_trip_for_resume():
    cfg = StewardConfig(hysteresis=0.07, max_compactions_per_task=5)
    s = ContextSteward(task_id="resume-me", config=cfg)
    s.observe(0.90, now=42.0)
    s.mark_compacted(now=42.0)
    s.register("proposal", fingerprint("move psu"))

    data = s.to_dict()
    assert data["schema"] == "context_steward_v1"

    restored = ContextSteward.from_dict(data)
    assert restored.task_id == "resume-me"
    assert restored.state == s.state
    assert restored.compactions_done == 1
    assert restored.last_compaction_ts == 42.0
    assert restored.config.hysteresis == 0.07
    assert restored.config.max_compactions_per_task == 5
    assert restored.is_repeat("proposal", fingerprint("move psu")) is True
