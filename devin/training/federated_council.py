"""Bounded, evidence-first Federated Council primitives (P6).

The module is deliberately runtime-neutral: it builds blind review packets and
aggregates structured verdicts, but it never starts a model and never promotes
an attempt.  A disagreement can only be resolved by a verified deterministic
experiment result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Protocol


COUNCIL_SCHEMA = "devin_federated_council_v1"
PLAN_SCHEMA = "devin_council_plan_v1"
RESULT_SCHEMA = "devin_council_result_v1"
AXES = (
    "correttezza_concettuale",
    "robustezza",
    "vincoli",
    "sicurezza",
    "qualita",
)
VERDICTS = frozenset({"pass", "fail", "needs_evidence"})
SAFE_PACKET_FIELDS = (
    "attempt_id",
    "prompt",
    "response",
    "diff_summary",
    "tests",
    "constraints",
    "evidence",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class ReviewerSpec:
    reviewer_id: str
    family: str
    axes: tuple[str, ...]
    local: bool = True
    available: bool = True
    max_tokens: int = 2_000
    timeout_seconds: int = 90

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ReviewerSpec":
        axes = tuple(dict.fromkeys(str(item) for item in value.get("axes", ())))
        if not axes or any(axis not in AXES for axis in axes):
            raise ValueError("reviewer axes are missing or invalid")
        reviewer_id = str(value.get("reviewer_id") or "").strip()
        family = str(value.get("family") or "").strip()
        if not reviewer_id or not family:
            raise ValueError("reviewer_id and family are required")
        return cls(
            reviewer_id=reviewer_id[:80],
            family=family[:80],
            axes=axes,
            local=bool(value.get("local", True)),
            available=bool(value.get("available", True)),
            max_tokens=max(128, min(int(value.get("max_tokens", 2_000)), 16_000)),
            timeout_seconds=max(5, min(int(value.get("timeout_seconds", 90)), 900)),
        )


@dataclass(frozen=True)
class ReviewVerdict:
    packet_id: str
    axis: str
    verdict: str
    confidence: float
    reasoning: str
    violations: tuple[str, ...]
    proposed_experiment: str | None
    reviewer_id: str
    family: str
    evidence_ids: tuple[str, ...] = ()
    budget_spent: dict[str, int] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ReviewVerdict":
        axis = str(value.get("axis") or "")
        verdict = str(value.get("verdict") or "")
        reasoning = str(value.get("reasoning") or "").strip()
        if axis not in AXES or verdict not in VERDICTS:
            raise ValueError("invalid council verdict")
        if len(reasoning) < 8:
            raise ValueError("review reasoning is required")
        confidence = float(value.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("review confidence must be between zero and one")
        packet_id = str(value.get("packet_id") or "")
        reviewer_id = str(value.get("reviewer_id") or "").strip()
        family = str(value.get("family") or "").strip()
        if not packet_id.startswith("crp_") or not reviewer_id or not family:
            raise ValueError("review provenance is incomplete")
        return cls(
            packet_id=packet_id,
            axis=axis,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning[:8_000],
            violations=tuple(str(item)[:1_000] for item in value.get("violations", ())),
            proposed_experiment=(str(value.get("proposed_experiment"))[:4_000]
                                 if value.get("proposed_experiment") else None),
            reviewer_id=reviewer_id[:80],
            family=family[:80],
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", ())),
            budget_spent=value.get("budget_spent") if isinstance(value.get("budget_spent"), dict) else None,
        )


class ReviewerAdapter(Protocol):
    spec: ReviewerSpec

    def review(self, packet: dict[str, Any], axis: str) -> ReviewVerdict:
        """Return one structured, independent verdict for one axis."""


class CapacityBudgeter:
    def __init__(self, *, max_reviewers: int = 7, total_tokens: int = 12_000,
                 total_seconds: int = 360):
        self.max_reviewers = max(1, min(int(max_reviewers), 20))
        self.total_tokens = max(512, min(int(total_tokens), 100_000))
        self.total_seconds = max(10, min(int(total_seconds), 3_600))

    def allocate(self, selected: list[tuple[ReviewerSpec, str]]) -> dict[str, dict[str, int]]:
        if len(selected) > self.max_reviewers:
            raise ValueError("council reviewer bound exceeded")
        if not selected:
            return {}
        fair_tokens = max(128, self.total_tokens // len(selected))
        fair_seconds = max(5, self.total_seconds // len(selected))
        return {
            f"{spec.reviewer_id}:{axis}": {
                "max_tokens": min(spec.max_tokens, fair_tokens),
                "timeout_seconds": min(spec.timeout_seconds, fair_seconds),
                "heartbeat_seconds": min(30, max(5, fair_seconds // 3)),
            }
            for spec, axis in selected
        }


def default_reviewer_roster() -> list[ReviewerSpec]:
    """Only reviewers that exist today. Semantic model roles stay disabled."""
    return [
        ReviewerSpec("local-constraints", "devin-deterministic", ("vincoli",)),
        ReviewerSpec("local-security", "devin-deterministic", ("sicurezza",)),
    ]


class CouncilRouter:
    def __init__(self, budgeter: CapacityBudgeter | None = None):
        self.budgeter = budgeter or CapacityBudgeter()

    def plan(self, evidence_packet: dict[str, Any], reviewers: Iterable[ReviewerSpec],
             *, critical: bool = False, external_consent: bool = False) -> dict[str, Any]:
        safe_evidence = {key: evidence_packet[key] for key in SAFE_PACKET_FIELDS if key in evidence_packet}
        if not safe_evidence.get("attempt_id") or not safe_evidence.get("evidence"):
            raise ValueError("attempt_id and evidence are required")
        external_packet = evidence_packet.get("external_packet")
        redaction_manifest = evidence_packet.get("redaction_manifest")

        candidates = sorted(
            (spec for spec in reviewers if spec.available),
            key=lambda item: (not item.local, item.reviewer_id),
        )
        selected: list[tuple[ReviewerSpec, str]] = []
        families_by_axis: dict[str, set[str]] = {axis: set() for axis in AXES}
        # Coverage comes first: reserve at most one slot for every axis before
        # spending the remaining bounded capacity on critical redundancy.
        for axis in AXES:
            for spec in candidates:
                if axis not in spec.axes:
                    continue
                if not spec.local and not external_consent:
                    continue
                selected.append((spec, axis))
                families_by_axis[axis].add(spec.family)
                break
        if critical:
            for axis in AXES:
                if len(selected) >= self.budgeter.max_reviewers:
                    break
                for spec in candidates:
                    if axis not in spec.axes or spec.family in families_by_axis[axis]:
                        continue
                    if not spec.local and not external_consent:
                        continue
                    selected.append((spec, axis))
                    families_by_axis[axis].add(spec.family)
                    break
        budgets = self.budgeter.allocate(selected)
        packets = []
        for spec, axis in selected:
            packet_evidence = safe_evidence
            packet_redaction = None
            if not spec.local:
                if not isinstance(external_packet, dict) or not isinstance(redaction_manifest, dict):
                    raise ValueError("external reviewer requires an explicit redacted packet and manifest")
                if redaction_manifest.get("approved") is not True:
                    raise ValueError("external redaction manifest is not approved")
                packet_evidence = {
                    key: external_packet[key] for key in SAFE_PACKET_FIELDS if key in external_packet
                }
                if not packet_evidence.get("attempt_id") or not packet_evidence.get("evidence"):
                    raise ValueError("redacted external packet is incomplete")
                packet_redaction = redaction_manifest
            body = {
                "schema": COUNCIL_SCHEMA,
                "axis": axis,
                "lens": f"Review only the {axis} axis. Return evidence, not another reviewer's opinion.",
                "evidence": packet_evidence,
                "reviewer": {"reviewer_id": spec.reviewer_id, "family": spec.family, "local": spec.local},
                "budget": budgets[f"{spec.reviewer_id}:{axis}"],
                "blind": True,
                "other_verdicts_included": False,
                "redaction_manifest": packet_redaction,
            }
            packets.append({**body, "packet_id": _content_id("crp_", body)})
        covered = sorted({item["axis"] for item in packets})
        missing = [axis for axis in AXES if axis not in covered]
        result = {
            "schema": PLAN_SCHEMA,
            "critical": bool(critical),
            "packets": packets,
            "covered_axes": covered,
            "missing_axes": missing,
            "coverage_complete": not missing,
            "bounded": True,
            "external_consent": bool(external_consent),
            "promotion_performed": False,
        }
        return {**result, "plan_id": _content_id("crl_", result)}


class CouncilAggregator:
    def aggregate(self, plan: dict[str, Any], verdicts: Iterable[ReviewVerdict]) -> dict[str, Any]:
        packet_by_id = {item["packet_id"]: item for item in plan.get("packets", ())}
        grouped: dict[str, list[ReviewVerdict]] = {axis: [] for axis in AXES}
        seen: set[tuple[str, str]] = set()
        for verdict in verdicts:
            packet = packet_by_id.get(verdict.packet_id)
            if not packet or packet.get("axis") != verdict.axis:
                raise ValueError("verdict does not belong to this council plan")
            if packet.get("reviewer", {}).get("reviewer_id") != verdict.reviewer_id:
                raise ValueError("verdict reviewer does not match its blind packet")
            dedupe = (verdict.axis, verdict.family)
            if dedupe in seen:
                raise ValueError("duplicate reviewer family on one axis")
            seen.add(dedupe)
            grouped[verdict.axis].append(verdict)

        missing = [axis for axis in AXES if not grouped[axis]]
        disagreements = []
        failures = []
        needs_evidence = list(missing)
        for axis, items in grouped.items():
            states = {item.verdict for item in items}
            if len(states) > 1:
                disagreements.append(axis)
            elif states == {"fail"}:
                failures.append(axis)
            elif states == {"needs_evidence"}:
                needs_evidence.append(axis)

        if disagreements:
            outcome = "arbiter_required"
        elif failures:
            outcome = "verified_failure_candidate"
        elif needs_evidence:
            outcome = "needs_evidence"
        else:
            outcome = "verified_success_candidate"
        result = {
            "schema": RESULT_SCHEMA,
            "plan_id": plan.get("plan_id"),
            "outcome": outcome,
            "failures": failures,
            "needs_evidence": sorted(set(needs_evidence)),
            "arbiter_axes": disagreements,
            "rerun_required": outcome in {"verified_failure_candidate", "verified_success_candidate"},
            "promotion_performed": False,
            "verdicts": [asdict(item) for values in grouped.values() for item in values],
        }
        return {**result, "result_id": _content_id("crr_", result)}


def resolve_arbiter_experiment(*, axis: str, experiment: dict[str, Any],
                               experiment_result: dict[str, Any]) -> dict[str, Any]:
    """Resolve a disagreement from a real rerun receipt, never model authority."""
    if axis not in AXES or not str(experiment.get("spec") or "").strip():
        raise ValueError("a bounded arbiter experiment is required")
    if experiment_result.get("status") not in {"verified_pass", "verified_fail"}:
        return {
            "schema": "devin_council_arbiter_result_v1",
            "axis": axis,
            "outcome": "needs_human_review",
            "promotion_performed": False,
        }
    if not str(experiment_result.get("evidence_id") or "").startswith("sha256:"):
        raise ValueError("verified arbiter result requires content-addressed evidence")
    return {
        "schema": "devin_council_arbiter_result_v1",
        "axis": axis,
        "outcome": ("verified_success_candidate" if experiment_result["status"] == "verified_pass"
                    else "verified_failure_candidate"),
        "authority": "deterministic_experiment_result",
        "experiment": experiment,
        "experiment_result": experiment_result,
        "rerun_required": True,
        "promotion_performed": False,
    }
