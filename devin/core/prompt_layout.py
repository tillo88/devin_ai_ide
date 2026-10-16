"""Stable prompt layout for Context Steward CS5.

The stable prefix is deliberately separated from per-turn retrieval. Callers
can therefore observe when the reusable prefix changed. Retrieved snippets
travel inside the LAST user message (layout v2): before, they were a system
message ahead of the history, so a different snippet on every turn changed
the prompt before the whole conversation and the server's prompt cache had to
re-read all of it. Now system prompt + history are byte-identical between
turns and only the new message is read. This module does not cache KV state or
control a model runtime.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence


PROMPT_LAYOUT_SCHEMA = "devin_prompt_layout_v2"
RETRIEVAL_HEADER = (
    "EPHEMERAL RETRIEVAL FOR THIS TURN (not long-term memory; "
    "discard next turn unless retrieved again):"
)


def _text_parts(parts: Iterable[object]) -> list[str]:
    return [str(item).strip() for item in parts if str(item or "").strip()]


def stable_prefix_fingerprint(
    stable_parts: Iterable[object], checkpoint: Mapping[str, Any] | None = None
) -> str:
    payload = {
        "stable_parts": _text_parts(stable_parts),
        "checkpoint_id": str((checkpoint or {}).get("checkpoint_id") or ""),
        "checkpoint_fingerprint": str(
            (checkpoint or {}).get("source_fingerprint") or ""
        ),
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compose_prompt_layout(
    *,
    stable_parts: Iterable[object],
    retrieval_parts: Iterable[object] = (),
    recent_history: Sequence[Mapping[str, Any]] = (),
    user_content: str,
    checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return messages plus an observable, deterministic layout receipt.

    Retrieval is never included in ``stable_prefix_fingerprint``. Only clean
    user/assistant history is admitted to the verbatim tail.
    """
    stable = _text_parts(stable_parts)
    retrieval = _text_parts(retrieval_parts)
    messages: list[dict[str, str]] = []
    if stable:
        messages.append({"role": "system", "content": "\n\n".join(stable)})
    for item in recent_history:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    final = str(user_content)
    if retrieval:
        final = (RETRIEVAL_HEADER + "\n\n" + "\n\n".join(retrieval)
                 + "\n\n---\nMESSAGGIO DELL'UTENTE:\n" + final)
    messages.append({"role": "user", "content": final})
    return {
        "schema": PROMPT_LAYOUT_SCHEMA,
        "stable_prefix_fingerprint": stable_prefix_fingerprint(stable, checkpoint),
        "stable_sections": len(stable),
        "retrieval_sections": len(retrieval),
        "recent_messages": sum(
            1 for item in messages if item.get("role") in {"user", "assistant"}
        )
        - 1,
        "retrieval_ephemeral": True,
        "messages": messages,
    }
