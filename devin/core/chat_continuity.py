"""Bounded, project-local continuity checkpoints for long chats.

Checkpoints are conversation state, not long-term memory.  They compact older
turns before they fall outside the model window and are always paired with a
verbatim recent tail.  Generation is explicit, bounded and validated; callers
may fall back to the deterministic packet when the model is unavailable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional


CHECKPOINT_SCHEMA = "chat_continuity_v1"
COMPACTION_TRIGGERS = frozenset({
    "context_pressure",
    "subtask_boundary",
    "objective_change",
    "large_tool_output",
    "pre_shift",
    "orchestrator_request",
})


def _clean_messages(messages: Iterable[Dict]) -> List[Dict[str, str]]:
    cleaned = []
    for item in messages:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if content.strip():
            cleaned.append({"role": role, "content": content.strip()})
    return cleaned


def estimate_tokens(messages: Iterable[Dict], extra_text: str = "") -> int:
    """Conservative tokenizer-independent estimate suitable for local models."""
    chars = len(extra_text or "")
    count = 0
    for item in _clean_messages(messages):
        chars += len(item["content"])
        count += 1
    return max(1, (chars + 2) // 3 + count * 6)


def history_fingerprint(messages: Iterable[Dict]) -> str:
    payload = json.dumps(_clean_messages(messages), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def should_checkpoint(
    history: Iterable[Dict],
    *,
    context_size: int,
    fixed_context: str = "",
    trigger_ratio: float = 0.72,
    max_history_messages: int = 20,
    recent_messages: int = 8,
    min_messages: int = 12,
) -> bool:
    clean = _clean_messages(history)
    if len(clean) < max(2, min_messages):
        return False
    message_trigger = len(clean) > max(recent_messages, max_history_messages - recent_messages)
    budget = max(256, int(max(512, context_size) * min(0.95, max(0.25, trigger_ratio))))
    return message_trigger or estimate_tokens(clean, fixed_context) >= budget


def checkpoint_needs_refresh(history: Iterable[Dict], checkpoint: Optional[Dict],
                             *, recent_messages: int = 8,
                             refresh_messages: int = 6) -> bool:
    clean = _clean_messages(history)
    cutoff = max(0, len(clean) - max(2, recent_messages))
    if cutoff <= 0:
        return False
    if not checkpoint or checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        return True
    summarized = int(checkpoint.get("summarized_messages") or 0)
    if summarized > cutoff:
        return True
    if summarized and checkpoint.get("source_fingerprint") != history_fingerprint(clean[:summarized]):
        return True
    return cutoff - summarized >= max(1, refresh_messages)


def _bounded_transcript(messages: List[Dict[str, str]], max_chars: int) -> str:
    chunks = []
    remaining = max(1000, max_chars)
    for item in reversed(messages):
        text = f"[{item['role'].upper()}]\n{item['content']}\n"
        if len(text) > remaining:
            text = text[-remaining:]
        chunks.append(text)
        remaining -= len(text)
        if remaining <= 0:
            break
    return "\n".join(reversed(chunks))


def _fallback_summary(messages: List[Dict[str, str]], max_chars: int) -> str:
    transcript = _bounded_transcript(messages, max_chars=max_chars)
    return (
        "## Verified conversation handoff\n"
        "Automatic semantic compression was unavailable. The following is a "
        "bounded verbatim evidence tail; treat it as conversation evidence, not "
        "as verified project state.\n\n" + transcript
    )[:max_chars]


def build_checkpoint(
    history: Iterable[Dict],
    *,
    existing: Optional[Dict] = None,
    summarizer: Optional[Callable[[str], Optional[str]]] = None,
    recent_messages: int = 8,
    source_max_chars: int = 24000,
    summary_max_chars: int = 6000,
    trigger: str = "orchestrator_request",
) -> Optional[Dict]:
    if trigger not in COMPACTION_TRIGGERS:
        raise ValueError(f"unsupported compaction trigger: {trigger}")
    clean = _clean_messages(history)
    cutoff = len(clean) - max(2, recent_messages)
    if cutoff <= 0:
        return None

    older = clean[:cutoff]
    fingerprint = history_fingerprint(older)
    if existing and existing.get("schema") == CHECKPOINT_SCHEMA:
        if existing.get("source_fingerprint") == fingerprint:
            return existing

    prior_summary = ""
    incremental = older
    previous_count = 0
    if existing and existing.get("schema") == CHECKPOINT_SCHEMA:
        previous_count = int(existing.get("summarized_messages") or 0)
        if 0 < previous_count <= len(older):
            expected = existing.get("source_fingerprint")
            if expected == history_fingerprint(older[:previous_count]):
                prior_summary = str(existing.get("summary") or "")[:summary_max_chars]
                incremental = older[previous_count:]

    transcript = _bounded_transcript(incremental, source_max_chars)
    prompt = (
        "Create a compact continuity checkpoint for another coding-agent chat.\n"
        "Preserve only information supported by the transcript. Separate facts, "
        "decisions, completed work with evidence, open work, constraints, failures, "
        "and the exact next action. Never invent test results, files or decisions. "
        "Keep identifiers, paths, commands and unresolved doubts exact. Use concise "
        "Markdown headings.\n\n"
    )
    if prior_summary:
        prompt += "PREVIOUS VERIFIED HANDOFF:\n" + prior_summary + "\n\n"
    prompt += "NEW CONVERSATION EVIDENCE:\n" + transcript

    summary = None
    if summarizer:
        try:
            candidate = summarizer(prompt)
            if isinstance(candidate, str) and len(candidate.strip()) >= 80:
                summary = candidate.strip()[:summary_max_chars]
        except Exception:
            summary = None
    if not summary:
        summary = _fallback_summary(older, summary_max_chars)

    checkpoint_id = "sha256:" + hashlib.sha256(
        json.dumps(
            {"source_fingerprint": fingerprint, "summary": summary, "trigger": trigger},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": CHECKPOINT_SCHEMA,
        "checkpoint_id": checkpoint_id,
        "generated_at": datetime.now().isoformat(),
        "summarized_messages": len(older),
        "source_fingerprint": fingerprint,
        "summary": summary,
        "recent_messages": max(2, recent_messages),
        "generation": "model" if summarizer and not summary.startswith("## Verified conversation handoff") else "deterministic",
        "trigger": trigger,
        "validation": "proposed",
        "promotion": "none",
        "supersedes": (
            str(existing.get("checkpoint_id") or "")
            if isinstance(existing, dict) and existing.get("checkpoint_id")
            else None
        ),
    }


def accept_checkpoint_proposal(
    history: Iterable[Dict],
    checkpoint: Optional[Dict],
    *,
    recent_messages: int = 8,
    summary_max_chars: int = 6000,
) -> Dict:
    """Validate a CS4 proposal before the orchestrator persists it.

    Acceptance never promotes the checkpoint to long-term memory. It only
    certifies that the bounded summary is tied to the exact older transcript.
    """
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("invalid continuity checkpoint schema")
    trigger = checkpoint.get("trigger")
    if trigger not in COMPACTION_TRIGGERS:
        raise ValueError("checkpoint trigger missing or unsupported")
    clean = _clean_messages(history)
    cutoff = len(clean) - max(2, recent_messages)
    summarized = checkpoint.get("summarized_messages")
    if not isinstance(summarized, int) or summarized <= 0 or summarized != cutoff:
        raise ValueError("checkpoint summarized message boundary mismatch")
    expected = history_fingerprint(clean[:summarized])
    if checkpoint.get("source_fingerprint") != expected:
        raise ValueError("checkpoint source fingerprint mismatch")
    summary = checkpoint.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 80:
        raise ValueError("checkpoint summary is empty or too short")
    if len(summary) > max(256, int(summary_max_chars)):
        raise ValueError("checkpoint summary exceeds configured bound")
    if checkpoint.get("promotion") != "none":
        raise ValueError("continuity checkpoint cannot promote to long-term memory")
    accepted = dict(checkpoint)
    accepted["validation"] = "accepted"
    return accepted


def context_from_checkpoint(checkpoint: Optional[Dict]) -> str:
    if not checkpoint or checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        return ""
    summary = str(checkpoint.get("summary") or "").strip()
    if not summary:
        return ""
    return (
        "CONTINUITY CHECKPOINT (conversation state, not long-term memory):\n"
        "Use this handoff together with the recent verbatim turns. Do not promote "
        "its assumptions to verified facts without evidence.\n\n" + summary
    )
