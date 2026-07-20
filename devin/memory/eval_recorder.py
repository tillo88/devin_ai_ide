from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from devin.memory.taxonomy import build_memory_tags, tag_value

_CODE_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+.-]+)?\s+.*?```", re.DOTALL)
_OPERATIONAL_CREATE_TERMS = (
    "crea", "creare", "realizza", "implementa", "costruisci", "build", "create",
    "scaffold", "genera",
)
_OPERATIONAL_CHANGE_TERMS = (
    "modifica", "modificare", "migliora", "migliorare", "rendi", "refactor",
    "correggi", "correggere", "fix", "aggiorna", "aggiornare",
)
_OPERATIONAL_DELIVERABLE_TERMS = (
    "app", "applicazione", "mvp", "progetto", "file reali", "tests.py", "test",
    "gui", "ui", "ux", "frontend", "interfaccia", "codice", "api key", "commit",
    "non mostrare snippet", "non mostrare soltanto snippet",
)
_EXPLANATION_TERMS = (
    "spiega", "come funziona", "che ne pensi", "piano", "roadmap", "analizza", "consigliami",
)


def _contains_term(text: str, term: str) -> bool:
    """Match intent terms as words/phrases, not as fragments of other words."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def is_operational_build_request(message: str) -> bool:
    text = (message or "").lower()
    if not text:
        return False
    action_score = sum(
        1 for term in _OPERATIONAL_CREATE_TERMS + _OPERATIONAL_CHANGE_TERMS
        if _contains_term(text, term)
    )
    deliverable_score = sum(
        1 for term in _OPERATIONAL_DELIVERABLE_TERMS if _contains_term(text, term)
    )
    explanation_score = sum(1 for term in _EXPLANATION_TERMS if _contains_term(text, term))
    return action_score >= 1 and deliverable_score >= 2 and explanation_score == 0


def detect_chat_only_output(user_message: str, assistant_response: str) -> dict[str, Any] | None:
    if not is_operational_build_request(user_message):
        return None
    response = assistant_response or ""
    code_fences = _CODE_FENCE_RE.findall(response)
    if len(code_fences) < 1:
        return None
    lower = response.lower()
    claims_operational_done = any(
        phrase in lower for phrase in (
            "file creati", "ho creato i file", "test eseguiti", "commit", "patch applicata"
        )
    )
    if claims_operational_done:
        return None
    return {
        "status": "verified_failure",
        "failure_type": "chat_only_output",
        "reason": "operational build request was answered with Markdown code fences instead of real file writes/tests",
        "code_fences": len(code_fences),
        "retry_rule": "route to scaffold/run_from_conversation, write files, run tests, and only then report completion",
    }


def _memory_key(project_path: str, eval_name: str, task: str, failure_type: str) -> str:
    digest = hashlib.sha256(
        f"{Path(project_path).name}|{eval_name}|{failure_type}|{task[:500]}".encode("utf-8")
    ).hexdigest()[:16]
    return f"eval_{eval_name}_{failure_type}_{digest}"


def _existing_memory_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = record.get("memory_key") or tag_value(record.get("tags"), "memory_key", "")
            if key:
                keys.add(key)
    return keys


def record_eval_result(
    memory_client,
    *,
    project_path: str,
    task: str,
    eval_name: str,
    status: str,
    reason: str,
    failure_type: str = "",
    evidence: str = "eval",
    retry_rule: str = "",
    importance: float = 0.85,
    extra_tags: list[str] | None = None,
) -> str:
    local = getattr(memory_client, "local", None)
    key = _memory_key(project_path, eval_name, task, failure_type or status)
    if local is not None and key in _existing_memory_keys(Path(local.path)):
        return "duplicate"

    # ANTI-CONTAMINATION (2026-07-18): this recorder may only MINT
    # verified_success/verified_failure evidence — the statuses it can
    # legitimately produce. Any other caller-supplied status (human_confirmed,
    # hypothesis, unknown strings) is normalized to review-only pending_review:
    # the eval write path can never mint instantly recall-safe memory.
    # Polarity derives ONLY from the final normalized status.
    if status in {"verified_success", "verified_failure"}:
        final_status = status
        reported = ""
    else:
        final_status = "pending_review"
        reported = f" (caller-reported status '{status}' normalized to review-only)"
    polarity = (
        "negative" if final_status == "verified_failure"
        else "positive" if final_status == "verified_success"
        else "neutral"
    )
    kind = (
        "eval_result" if final_status == "verified_success"
        else "failure_lesson" if final_status == "verified_failure"
        else "raw_observation"
    )
    content = (
        f"Eval result for project '{Path(project_path).name}'.\n"
        f"Eval: {eval_name}. Status: {final_status}{reported}.\n"
        f"Failure type: {failure_type or 'none'}.\n"
        f"Reason: {reason}.\n"
        f"Task summary: {task[:1200]}\n"
        f"Retry rule: {retry_rule or 'preserve evidence and rerun the relevant quality gate before promoting memory'}"
    )
    tags = build_memory_tags(
        project=Path(project_path).name,
        kind=kind,
        status=final_status,
        polarity=polarity,
        evidence=evidence,
        failure_type=failure_type or None,
        memory_key=key,
    ) + ["source:devin", f"eval:{eval_name}"] + list(extra_tags or [])
    return memory_client.store_local(content, tags=tags, importance=importance)
