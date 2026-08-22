"""Versioned capability routing decisions for the single-GPU rig (P8).

This module is a planner, not a lifecycle controller.  It does not call
systemd, the rig broker, or a model endpoint.  A role change is always returned
as an explicit activation requirement for the existing ai-rig orchestrator.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROFILE_SCHEMA = "devin_routing_profiles_v1"
DECISION_SCHEMA = "devin_capability_route_v1"
CANARY_SCHEMA = "devin_routing_canary_status_v1"
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "routing_profiles.v1.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class RoutingProfile:
    profile_id: str
    fingerprint: str
    value: dict[str, Any]

    @property
    def roles(self) -> dict[str, dict[str, Any]]:
        return self.value["roles"]


def load_routing_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> RoutingProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != PROFILE_SCHEMA:
        raise ValueError("unsupported routing profile schema")
    profile_id = str(payload.get("active_profile") or "")
    profile = payload.get("profiles", {}).get(profile_id)
    if not profile_id or not isinstance(profile, dict):
        raise ValueError("active routing profile is missing")
    if profile.get("automatic_switch") is not False:
        raise ValueError("automatic model switching must remain disabled")
    roles = profile.get("roles")
    if not isinstance(roles, dict) or not {"clippy", "devin"}.issubset(roles):
        raise ValueError("routing profile lacks the active rig roles")
    for role, config in roles.items():
        if config.get("lifecycle_owner") != "ai-rig-model-slot":
            raise ValueError(f"role {role} bypasses the rig lifecycle owner")
        if not isinstance(config.get("capabilities"), list):
            raise ValueError(f"role {role} has no capability list")
    fingerprint = "sha256:" + hashlib.sha256(_canonical({"profile_id": profile_id, "profile": profile})).hexdigest()
    return RoutingProfile(profile_id, fingerprint, profile)


def plan_capability_route(capability: str, *, resident_role: str | None = None,
                          profile: RoutingProfile | None = None) -> dict[str, Any]:
    profile = profile or load_routing_profile()
    capability = str(capability or "").strip().lower()
    preferred = profile.value.get("preferred_roles", {}).get(capability, [])
    dedicated = profile.value.get("dedicated_capabilities", {}).get(capability)
    roles = profile.roles
    if not preferred:
        return _decision(profile, capability, resident_role, None, "unsupported_capability")

    if resident_role and resident_role not in roles:
        return _decision(profile, capability, resident_role, None, "unknown_resident_role")

    target = None
    reason = "preferred_role"
    if dedicated:
        target = dedicated if roles.get(dedicated, {}).get("enabled") else None
        reason = "dedicated_capability" if target else "dedicated_role_disabled"
    elif resident_role:
        resident = roles[resident_role]
        if (resident.get("enabled") and profile.value.get("reuse_compatible_resident")
                and capability in resident.get("capabilities", ())):
            target = resident_role
            reason = "compatible_resident_reuse"
    if target is None and reason != "dedicated_role_disabled":
        target = next((role for role in preferred if roles.get(role, {}).get("enabled")), None)
        if target is None:
            reason = "all_preferred_roles_disabled"

    return _decision(profile, capability, resident_role, target, reason)


def _decision(profile: RoutingProfile, capability: str, resident_role: str | None,
              target_role: str | None, reason: str) -> dict[str, Any]:
    activation_required = bool(target_role and target_role != resident_role)
    status = "ready" if target_role and not activation_required else (
        "activation_required" if activation_required else "unavailable"
    )
    decision = {
        "schema": DECISION_SCHEMA,
        "profile_id": profile.profile_id,
        "profile_fingerprint": profile.fingerprint,
        "capability": capability,
        "resident_role": resident_role,
        "target_role": target_role,
        "status": status,
        "reason": reason,
        "activation_required": activation_required,
        "automatic_switch": False,
        "lifecycle_owner": "ai-rig-model-slot",
    }
    decision["decision_id"] = "route_" + hashlib.sha256(_canonical(decision)).hexdigest()
    return decision


def assess_canary(receipts: Iterable[dict[str, Any]], *, role: str,
                  capability: str, profile: RoutingProfile | None = None) -> dict[str, Any]:
    profile = profile or load_routing_profile()
    role_config = profile.roles.get(role)
    if not role_config or not role_config.get("enabled"):
        raise ValueError("canary role is unknown or disabled")
    if capability not in role_config.get("capabilities", ()):
        raise ValueError("canary capability is not supported by the role")
    canary = profile.value.get("canary", {})
    minimum = max(1, int(canary.get("minimum_verified_receipts", 2)))
    accepted = []
    rejected = []
    seen = set()
    for receipt in receipts:
        receipt_id = str(receipt.get("receipt_id") or "")
        valid = (
            re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_id) is not None
            and receipt_id not in seen
            and receipt.get("status") == "verified_pass"
            and receipt.get("role") == role
            and receipt.get("capability") == capability
            and receipt.get("profile_fingerprint") == profile.fingerprint
        )
        (accepted if valid else rejected).append(receipt_id or "missing")
        if valid:
            seen.add(receipt_id)
    result = {
        "schema": CANARY_SCHEMA,
        "profile_id": profile.profile_id,
        "profile_fingerprint": profile.fingerprint,
        "role": role,
        "capability": capability,
        "accepted_receipts": accepted,
        "rejected_receipts": rejected,
        "minimum_verified_receipts": minimum,
        "canary_passed": len(accepted) >= minimum,
        "automatic_switch": False,
        "automatic_promotion": False,
    }
    return result
