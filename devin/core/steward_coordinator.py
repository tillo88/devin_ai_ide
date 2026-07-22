"""Context Steward coordinator - ties CS0 (pressure), CS1 (evidence archive)
and CS2 (retrieval) into one per-session object and derives the observable
snapshot for the panel (CS3).

The snapshot is DERIVED from the deterministic core state (CS3 DoD: the panel
has no state of its own). This module stays GPU/LLM-free: semantic enrichment
of findings/risks is CS4 and always goes through the orchestrator. The
coordinator never promotes to AutoMem/Understory on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from devin.core.chat_continuity import estimate_tokens
from devin.core.context_steward import ContextSteward, StewardConfig, fingerprint
from devin.core.evidence_archive import EvidenceArchive
from devin.core.evidence_retriever import EvidenceRetriever

SNAPSHOT_SCHEMA = "context_steward_snapshot_v1"


class StewardCoordinator:
    def __init__(self, *, task_id: str = "", settings: Optional[Dict[str, Any]] = None,
                 archive_dir: Optional[str | Path] = None):
        self.task_id = task_id
        self.steward = ContextSteward(task_id=task_id,
                                      config=StewardConfig.from_settings(settings))
        self.archive: Optional[EvidenceArchive] = (
            EvidenceArchive(archive_dir) if archive_dir else None
        )
        self.retriever: Optional[EvidenceRetriever] = (
            EvidenceRetriever(self.archive) if self.archive else None
        )
        # Refs and caller-supplied notes surfaced in the snapshot (not the core).
        self._evidence_refs: List[Dict[str, Any]] = []
        self._risks: List[str] = []
        self._actions: List[str] = []
        self._last_pressure: float = 0.0

    # ---- pressure ---------------------------------------------------------
    def observe_history(self, history, *, context_size: int, fixed_context: str = "",
                        now: float = 0.0) -> str:
        """Compute pressure from a conservative token estimate and advance the
        core state machine. Returns the current state."""
        tokens = estimate_tokens(history, fixed_context)
        pressure = min(1.0, tokens / max(1, context_size))
        self._last_pressure = pressure
        return self.steward.observe(pressure, now=now)

    def observe_pressure(self, pressure: float, *, now: float = 0.0) -> str:
        self._last_pressure = max(0.0, min(1.0, float(pressure)))
        return self.steward.observe(self._last_pressure, now=now)

    def should_compact(self, *, now: float = 0.0) -> bool:
        return self.steward.should_compact(now=now)

    def mark_compacted(self, *, now: float = 0.0) -> None:
        self.steward.mark_compacted(now=now)
        self._actions.append("compacted at context boundary")

    # ---- evidence (CS1) ---------------------------------------------------
    def archive_evidence(self, content: str, *, kind: str, claim: str,
                         status: str = "unverified",
                         meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Store an artifact and keep a compact reference for the snapshot.
        Returns the evidence_id, or None if no archive is configured."""
        if not self.archive:
            return None
        full_meta = dict(meta or {})
        full_meta.setdefault("claim", claim)
        evidence_id = self.archive.store(content, kind=kind, meta=full_meta)
        self._evidence_refs.append(EvidenceArchive.make_ref(
            evidence_id, claim=claim, status=status))
        return evidence_id

    # ---- caller/orchestrator notes ---------------------------------------
    def note_risk(self, text: str) -> None:
        """Open risk to surface (e.g. 'test 262K in corso, non riassumere come
        PASS'). Deterministic core does not invent these."""
        if text:
            self._risks.append(str(text))

    def note_action(self, text: str) -> None:
        if text:
            self._actions.append(str(text))

    # ---- loop guard passthrough ------------------------------------------
    def register(self, kind: str, *parts: Any) -> bool:
        return self.steward.register(kind, fingerprint(*parts))

    def is_repeat(self, kind: str, *parts: Any) -> bool:
        return self.steward.is_repeat(kind, fingerprint(*parts))

    def reset_task(self, task_id: str = "") -> None:
        self.steward.reset_task(task_id)
        self.task_id = self.steward.task_id
        self._evidence_refs.clear()
        self._risks.clear()
        self._actions.clear()
        self._last_pressure = 0.0

    # ---- observable snapshot (CS3, derived) ------------------------------
    def snapshot(self, *, now: float = 0.0) -> Dict[str, Any]:
        allowed, reason = self.steward.compaction_decision(now=now)
        pct = round(self._last_pressure * 100, 1)
        findings = [f"pressione contesto {pct}%",
                    f"stato {self.steward.state}"]
        if self._evidence_refs:
            findings.append(f"{len(self._evidence_refs)} evidenze archiviate")
        if self.steward.compactions_done:
            findings.append(f"{self.steward.compactions_done} compattazioni eseguite")
        return {
            "schema": SNAPSHOT_SCHEMA,
            "task_id": self.task_id,
            "state": self.steward.state,
            "pressure": self._last_pressure,
            "pressure_pct": pct,
            "compaction": {"allowed": allowed, "reason": reason,
                           "done": self.steward.compactions_done,
                           "max": self.steward.config.max_compactions_per_task},
            "findings": findings,
            "evidence_preserved": list(self._evidence_refs),
            "actions": list(self._actions),
            "risks": list(self._risks),
        }
