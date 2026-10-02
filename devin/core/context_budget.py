"""Runtime-aware context budgeting for bounded model calls.

The model context is shared by prompt and completion.  This module keeps the
calculation in one place so callers cannot independently invent an evidence
cap and an output cap which do not fit together.

``DEVIN_EFFECTIVE_CONTEXT_TOKENS`` is the authoritative runtime hand-off from
the model-slot/frontdoor.  Older deployments do not expose it yet, therefore
the fallback is deliberately conservative and reports its source to the UI.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_CONTEXT_TOKENS = 8_192
MIN_CONTEXT_TOKENS = 4_096
MAX_CONTEXT_TOKENS = 262_144
MIN_ANALYSIS_OUTPUT_TOKENS = 2_048
MIN_PLAN_OUTPUT_TOKENS = 3_072
FIXED_PROMPT_RESERVE_TOKENS = 1_100
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _bounded_context(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if MIN_CONTEXT_TOKENS <= parsed <= MAX_CONTEXT_TOKENS:
        return parsed
    return None


def effective_context_tokens(config: Mapping[str, Any] | None = None) -> tuple[int, str]:
    """Return the effective context and an observable provenance label.

    The environment value is injected by the runtime owner and wins over
    application settings.  ``runtime_context_tokens`` is an explicit bridge
    for development fixtures.  The legacy local-model values are only a
    conservative fallback; ``rig_ctx_size`` is intentionally ignored because
    it has historically drifted from the model-slot envelope.
    """

    runtime_value = _bounded_context(os.environ.get("DEVIN_EFFECTIVE_CONTEXT_TOKENS"))
    if runtime_value is not None:
        return runtime_value, "runtime_env"

    models = dict((config or {}).get("models", {}) or {})
    configured_runtime = _bounded_context(models.get("runtime_context_tokens"))
    if configured_runtime is not None:
        return configured_runtime, "runtime_config"

    local_models = models.get("local_models", {}) or {}
    configured = []
    for item in local_models.values():
        if isinstance(item, Mapping):
            value = _bounded_context(item.get("ctx_size"))
            if value is not None:
                configured.append(value)
    if configured:
        return min(configured), "conservative_local_config"
    return DEFAULT_CONTEXT_TOKENS, "conservative_default"


def llm_compaction_allowed() -> bool:
    """Return true only when the runtime guarantees a fresh model instance.

    Continuity compaction otherwise stays deterministic.  A config file is not
    accepted as authority for process lifecycle: only the slot/broker owner can
    truthfully inject this runtime hand-off.
    """

    value = os.environ.get("DEVIN_FRESH_INSTANCE_PER_INFERENCE", "")
    return value.strip().lower() in _TRUE_VALUES


@dataclass(frozen=True)
class ContextBudget:
    context_tokens: int
    context_source: str
    intent: str
    safety_tokens: int
    minimum_output_tokens: int
    preferred_output_tokens: int
    evidence_token_budget: int
    evidence_char_budget: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "devin_context_budget_v2",
            "context_tokens": self.context_tokens,
            "context_source": self.context_source,
            "intent": self.intent,
            "safety_tokens": self.safety_tokens,
            "minimum_output_tokens": self.minimum_output_tokens,
            "preferred_output_tokens": self.preferred_output_tokens,
            "evidence_token_budget": self.evidence_token_budget,
            "evidence_char_budget": self.evidence_char_budget,
            "chars_per_token_estimate": 3,
        }


def context_budget(config: Mapping[str, Any] | None, *, wants_plan: bool) -> ContextBudget:
    context_tokens, source = effective_context_tokens(config)
    intent = "plan" if wants_plan else "analysis"
    minimum = MIN_PLAN_OUTPUT_TOKENS if wants_plan else MIN_ANALYSIS_OUTPUT_TOKENS
    preferred_ratio = 0.45 if wants_plan else 0.35
    preferred = max(minimum, int(context_tokens * preferred_ratio))
    safety = max(512, int(math.ceil(context_tokens * 0.08)))
    evidence_tokens = max(
        512,
        context_tokens - preferred - safety - FIXED_PROMPT_RESERVE_TOKENS,
    )
    # The estimator used by chat_continuity is deliberately conservative at
    # three characters/token.  Keep the desktop composer on the same contract.
    evidence_chars = evidence_tokens * 3
    return ContextBudget(
        context_tokens=context_tokens,
        context_source=source,
        intent=intent,
        safety_tokens=safety,
        minimum_output_tokens=minimum,
        preferred_output_tokens=preferred,
        evidence_token_budget=evidence_tokens,
        evidence_char_budget=evidence_chars,
    )


def completion_budget(
    budget: ContextBudget,
    *,
    estimated_prompt_tokens: int,
) -> tuple[int, dict[str, Any]]:
    """Return max completion tokens or raise before contacting the model."""

    available = budget.context_tokens - int(estimated_prompt_tokens) - budget.safety_tokens
    if available < budget.minimum_output_tokens:
        raise ValueError(
            "contesto insufficiente per una risposta completa: "
            f"prompt stimato {estimated_prompt_tokens} token, finestra "
            f"{budget.context_tokens}, minimo output {budget.minimum_output_tokens}"
        )
    output = min(budget.preferred_output_tokens, available)
    receipt = budget.as_dict()
    receipt.update({
        "estimated_prompt_tokens": int(estimated_prompt_tokens),
        "available_output_tokens": int(available),
        "max_output_tokens": int(output),
    })
    return output, receipt
