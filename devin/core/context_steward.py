"""Context Steward - deterministic operational-memory supervisor (CS0).

See docs/CONTEXT_STEWARD_PLAN.md. This is the always-on, GPU-free, LLM-free
core: it watches context pressure and decides WHEN a checkpoint/compaction is
warranted, with hysteresis (no flapping), cooldown, a per-task compaction cap,
and a loop guard (same task/proposal/error/command fingerprint with no new
evidence -> no-op). It does NOT summarize, write to the project, or promote to
permanent memory - those are separate, later phases and always go through the
orchestrator.

Invariants (docs/CONTEXT_STEWARD_PLAN.md section 2):
- deterministic, no GPU, no LLM in this module;
- never replaces evidence with summary (this module produces neither);
- state is JSON-serializable for crash resume.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# Pressure states ordered by severity (index = severity rank).
STATES = (
    "IDLE",
    "WATCHING",
    "PREPARING",
    "COMPACTING",
    "CHECKPOINT_REQUIRED",
    "CONTROLLED_CONTINUATION",
)
_SEVERITY = {name: rank for rank, name in enumerate(STATES)}


@dataclass
class StewardConfig:
    """Thresholds and guards. Defaults are CONSERVATIVE and TO BE CALIBRATED on
    real runs (same discipline as the tree-sitter pin / llama version): they
    live in config, never hardcoded in call sites.

    Thresholds are pressure ratios in [0, 1] (fraction of the usable context
    window). Bands (from docs/Context Steward.txt):
        < watching                 -> IDLE
        watching .. preparing      -> WATCHING
        preparing .. compacting    -> PREPARING
        compacting .. required     -> COMPACTING
        required .. continuation   -> CHECKPOINT_REQUIRED
        >= continuation            -> CONTROLLED_CONTINUATION
    """

    watching: float = 0.65
    preparing: float = 0.75
    compacting: float = 0.82
    required: float = 0.88
    continuation: float = 0.92

    # Hysteresis: once in a state, pressure must fall this far below the band's
    # lower edge before downgrading. Prevents flapping around a boundary.
    hysteresis: float = 0.05

    # Cooldown between compactions (seconds) and minimum pressure drop required
    # after a compaction before another is allowed. Together they break the
    # "summarize -> add summary -> exceed threshold -> summarize again" loop.
    cooldown_seconds: float = 600.0
    min_pressure_drop: float = 0.08

    # Hard cap on compactions per task.
    max_compactions_per_task: int = 3

    @classmethod
    def from_settings(cls, settings: Optional[Dict[str, Any]]) -> "StewardConfig":
        """Build config from settings.json's `context_steward` block. Unknown
        keys ignored; missing keys fall back to the conservative defaults."""
        block = {}
        if isinstance(settings, dict):
            raw = settings.get("context_steward")
            if isinstance(raw, dict):
                block = raw
        allowed = cls.__dataclass_fields__
        return cls(**{k: block[k] for k in block if k in allowed})

    def ordered_bands(self):
        return (
            (self.watching, "WATCHING"),
            (self.preparing, "PREPARING"),
            (self.compacting, "COMPACTING"),
            (self.required, "CHECKPOINT_REQUIRED"),
            (self.continuation, "CONTROLLED_CONTINUATION"),
        )

    def raw_state(self, pressure: float) -> str:
        state = "IDLE"
        for edge, name in self.ordered_bands():
            if pressure >= edge:
                state = name
            else:
                break
        return state

    def band_lower_edge(self, state: str) -> float:
        """Lower pressure edge of the band that maps to `state`."""
        edges = {name: edge for edge, name in self.ordered_bands()}
        return edges.get(state, 0.0)


def fingerprint(*parts: Any) -> str:
    """Stable fingerprint for loop detection (task/proposal/error/command)."""
    payload = "".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ContextSteward:
    """Deterministic pressure supervisor. One instance per task/session.

    Usage:
        s = ContextSteward(task_id="q36-calibration")
        state = s.observe(pressure=0.83, now=t)
        if s.should_compact(now=t):
            ...  # caller performs the (later-phase) checkpoint, then:
            s.mark_compacted(pressure=0.83, now=t)
    """

    task_id: str = ""
    config: StewardConfig = field(default_factory=StewardConfig)
    state: str = "IDLE"
    last_pressure: float = 0.0
    compactions_done: int = 0
    last_compaction_ts: Optional[float] = None
    pressure_at_last_compaction: Optional[float] = None
    _seen_fingerprints: Dict[str, str] = field(default_factory=dict)

    # ---- pressure state machine (with hysteresis) -------------------------
    def observe(self, pressure: float, *, now: float = 0.0) -> str:
        pressure = max(0.0, min(1.0, float(pressure)))
        self.last_pressure = pressure
        raw = self.config.raw_state(pressure)

        if _SEVERITY[raw] > _SEVERITY[self.state]:
            # Rising pressure escalates immediately.
            self.state = raw
        elif _SEVERITY[raw] < _SEVERITY[self.state]:
            # Falling pressure only de-escalates past the hysteresis margin,
            # and only one step at a time to avoid skipping observable states.
            lower_edge = self.config.band_lower_edge(self.state)
            if pressure <= lower_edge - self.config.hysteresis:
                self.state = STATES[_SEVERITY[self.state] - 1]
        return self.state

    # ---- compaction gating ------------------------------------------------
    def should_compact(self, *, now: float = 0.0) -> bool:
        ok, _ = self.compaction_decision(now=now)
        return ok

    def compaction_decision(self, *, now: float = 0.0):
        """Return (allowed, reason). A compaction is warranted only when the
        state is at least COMPACTING, the per-task cap is not exhausted, the
        cooldown has elapsed, and pressure has dropped enough since the last
        compaction (breaks the re-summarize loop)."""
        if _SEVERITY[self.state] < _SEVERITY["COMPACTING"]:
            return False, "below_compacting_threshold"
        if self.compactions_done >= self.config.max_compactions_per_task:
            return False, "max_compactions_reached"
        if self.last_compaction_ts is not None:
            if now - self.last_compaction_ts < self.config.cooldown_seconds:
                return False, "cooldown_active"
            if self.pressure_at_last_compaction is not None:
                drop = self.pressure_at_last_compaction - self.last_pressure
                if drop < self.config.min_pressure_drop:
                    return False, "insufficient_pressure_drop"
        # CHECKPOINT_REQUIRED / CONTROLLED_CONTINUATION are mandatory signals.
        if _SEVERITY[self.state] >= _SEVERITY["CHECKPOINT_REQUIRED"]:
            return True, "checkpoint_required"
        return True, "compacting_window"

    def mark_compacted(self, *, pressure: Optional[float] = None, now: float = 0.0) -> None:
        self.compactions_done += 1
        self.last_compaction_ts = now
        self.pressure_at_last_compaction = (
            self.last_pressure if pressure is None else max(0.0, min(1.0, float(pressure)))
        )

    def reset_task(self, task_id: str = "") -> None:
        """New task boundary: compaction budget and loop memory reset."""
        self.task_id = task_id or self.task_id
        self.state = "IDLE"
        self.compactions_done = 0
        self.last_compaction_ts = None
        self.pressure_at_last_compaction = None
        self._seen_fingerprints.clear()

    # ---- loop guard -------------------------------------------------------
    def is_repeat(self, kind: str, fp: str) -> bool:
        """True if this (kind, fingerprint) was already seen with no new
        evidence: same task/proposal/error/command already handled."""
        return self._seen_fingerprints.get(kind) == fp

    def register(self, kind: str, fp: str) -> bool:
        """Record a fingerprint. Returns False if it is a repeat (caller should
        block: e.g. two retries with no changed variable, duplicate checkpoint,
        same error already diagnosed)."""
        if self.is_repeat(kind, fp):
            return False
        self._seen_fingerprints[kind] = fp
        return True

    # ---- serialization (crash resume) -------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "context_steward_v1",
            "task_id": self.task_id,
            "state": self.state,
            "last_pressure": self.last_pressure,
            "compactions_done": self.compactions_done,
            "last_compaction_ts": self.last_compaction_ts,
            "pressure_at_last_compaction": self.pressure_at_last_compaction,
            "seen_fingerprints": dict(self._seen_fingerprints),
            "config": {
                "watching": self.config.watching,
                "preparing": self.config.preparing,
                "compacting": self.config.compacting,
                "required": self.config.required,
                "continuation": self.config.continuation,
                "hysteresis": self.config.hysteresis,
                "cooldown_seconds": self.config.cooldown_seconds,
                "min_pressure_drop": self.config.min_pressure_drop,
                "max_compactions_per_task": self.config.max_compactions_per_task,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextSteward":
        cfg_data = data.get("config") or {}
        config = StewardConfig(**{k: cfg_data[k] for k in cfg_data
                                  if k in StewardConfig.__dataclass_fields__})
        steward = cls(task_id=str(data.get("task_id") or ""), config=config)
        steward.state = data.get("state") if data.get("state") in _SEVERITY else "IDLE"
        steward.last_pressure = float(data.get("last_pressure") or 0.0)
        steward.compactions_done = int(data.get("compactions_done") or 0)
        ts = data.get("last_compaction_ts")
        steward.last_compaction_ts = float(ts) if ts is not None else None
        plc = data.get("pressure_at_last_compaction")
        steward.pressure_at_last_compaction = float(plc) if plc is not None else None
        seen = data.get("seen_fingerprints") or {}
        steward._seen_fingerprints = {str(k): str(v) for k, v in seen.items()}
        return steward
