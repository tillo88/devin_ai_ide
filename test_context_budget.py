from pathlib import Path

import pytest

from devin.core.context_budget import (
    completion_budget,
    context_budget,
    effective_context_tokens,
    llm_compaction_allowed,
)


def _config(ctx=8192):
    return {
        "models": {
            "rig_ctx_size": 12288,
            "local_models": {
                "coder": {"ctx_size": ctx},
                "reasoning": {"ctx_size": ctx},
            },
        }
    }


def test_runtime_context_wins_and_legacy_rig_hint_is_not_authoritative(monkeypatch):
    monkeypatch.setenv("DEVIN_EFFECTIVE_CONTEXT_TOKENS", "32768")
    assert effective_context_tokens(_config()) == (32768, "runtime_env")

    monkeypatch.delenv("DEVIN_EFFECTIVE_CONTEXT_TOKENS")
    assert effective_context_tokens(_config()) == (8192, "conservative_local_config")


def test_plan_budget_reserves_more_output_and_scales_with_context(monkeypatch):
    monkeypatch.setenv("DEVIN_EFFECTIVE_CONTEXT_TOKENS", "8192")
    analysis = context_budget(_config(), wants_plan=False)
    plan = context_budget(_config(), wants_plan=True)
    assert analysis.preferred_output_tokens >= 2048
    assert plan.preferred_output_tokens >= 3072
    assert plan.preferred_output_tokens > analysis.preferred_output_tokens
    assert plan.evidence_char_budget < analysis.evidence_char_budget

    monkeypatch.setenv("DEVIN_EFFECTIVE_CONTEXT_TOKENS", "32768")
    larger = context_budget(_config(), wants_plan=True)
    assert larger.preferred_output_tokens > plan.preferred_output_tokens
    assert larger.evidence_char_budget > plan.evidence_char_budget


def test_completion_budget_fails_before_inference_when_output_does_not_fit(monkeypatch):
    monkeypatch.setenv("DEVIN_EFFECTIVE_CONTEXT_TOKENS", "8192")
    budget = context_budget(_config(), wants_plan=True)
    with pytest.raises(ValueError, match="contesto insufficiente"):
        completion_budget(budget, estimated_prompt_tokens=5000)

    output, receipt = completion_budget(budget, estimated_prompt_tokens=3500)
    assert output >= 3072
    assert receipt["max_output_tokens"] == output
    assert receipt["context_source"] == "runtime_env"


def test_llm_compaction_requires_runtime_fresh_instance_contract(monkeypatch):
    monkeypatch.delenv("DEVIN_FRESH_INSTANCE_PER_INFERENCE", raising=False)
    assert llm_compaction_allowed() is False

    monkeypatch.setenv("DEVIN_FRESH_INSTANCE_PER_INFERENCE", "true")
    assert llm_compaction_allowed() is True

    monkeypatch.setenv("DEVIN_FRESH_INSTANCE_PER_INFERENCE", "unknown")
    assert llm_compaction_allowed() is False


def test_chat_wires_llm_compaction_only_behind_fresh_instance_contract():
    source = Path("devin/ui/routers/chat.py").read_text(encoding="utf-8")
    assert "summarizer = _summarize_continuity if llm_compaction_allowed() else None" in source
    assert "context_size, _context_source = effective_context_tokens(ai.config)" in source
