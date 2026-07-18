"""Facade memoria: locale curata + Understory/AutoMem manuali.

Gli esiti automatici di DEVIN vanno prima in JSONL locale. La memoria condivisa
resta disponibile per salvataggi espliciti dell'utente e promozioni future.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from devin.ai.automem_client import AutoMemClient, project_tags
from devin.ai.understory_client import UnderstoryClient
from devin.memory.taxonomy import MEMORY_SCHEMA_VERSION, validate_memory_tags


_EXCLUDED_RECALL_STATUSES = {
    "pending", "pending_review", "inconclusive", "revoked", "superseded",
    "quarantine", "syntax_only",
}
_RECALLABLE_LOCAL_STATUSES = {"verified_success", "verified_failure", "human_confirmed"}
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{3,}")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")
# FUZZY RECALL (2026-07-18): il match esatto sui token manca varianti
# morfologiche e testi misti IT/EN ("patching del sandbox" vs "the sandbox
# patch failed"). Fallback ADDITIVO a trigrammi carattere (dice coefficient),
# solo per record con zero overlap esatto: nessuna dipendenza, offline,
# deterministico. NON traduce — allarga il recall dentro vocabolario simile.
# Soglia calibrata su coppie reali: rilevanti 0.20-0.37, irrilevanti <=0.05.
_DEFAULT_FUZZY_THRESHOLD = 0.15
_FUZZY_WEIGHT = 3.0  # un fuzzy match forte vale ~1-2 token esatti


def _char_ngrams(text, n=3):
    normalized = _WS_RE.sub(" ", _NON_ALNUM_RE.sub(" ", str(text).lower())).strip()
    if len(normalized) < n:
        return {normalized} if normalized else set()
    padded = f" {normalized} "
    return {padded[i:i + n] for i in range(len(padded) - n + 1)}


def _dice_similarity(a, b):
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))
# HARDENING (2026-07-18): il marker puo' NON essere sulla prima riga — il
# recall semantico di Understory prefissa gli hit con "[path] ", spostando il
# marker in una riga successiva. Il vecchio filtro guardava solo la prima riga
# e lasciava passare memorie quarantine/pending (bypass della quarantena).
_STRUCTURED_MARKER_RE = re.compile(r"\[structured_memory\b([^\]]*)\]", re.IGNORECASE)
_STATUS_ATTR_RE = re.compile(r"\bstatus=([a-z0-9_]+)")


def _remote_memory_status(text):
    """Estrae lo status dal marker [STRUCTURED_MEMORY ...] ovunque nel testo.

    Ritorna None se non c'e' marker o il marker non dichiara uno status:
    le memorie remote non marcate (appunti manuali dell'utente) restano
    ammissibili come prima — il filtro esclude solo stati esplicitamente
    non richiamabili.
    """
    marker = _STRUCTURED_MARKER_RE.search(str(text))
    if not marker:
        return None
    status = _STATUS_ATTR_RE.search(marker.group(1).lower())
    return status.group(1) if status else None


def _tag_value(tags, prefix, default):
    return next(
        (tag.split(":", 1)[1] for tag in (tags or [])
         if isinstance(tag, str) and tag.startswith(prefix + ":")),
        default,
    )


def _fallback_envelope(content, tags):
    status = _tag_value(tags, "status", "pending_review")
    # POLARITY COHERENCE (2026-07-18): the default polarity derives from the
    # STATUS, not from "non-failure == positive". Review-only/quarantined
    # memories are neutral provenance; only recall-safe verified outcomes are
    # positive. An explicit polarity tag still wins (caller intent).
    default_polarity = (
        "negative" if status == "verified_failure"
        else "positive" if status in _RECALLABLE_LOCAL_STATUSES
        else "neutral"
    )
    polarity = _tag_value(tags, "polarity", default_polarity)
    evidence = _tag_value(tags, "evidence", "unspecified")
    return (
        f"[STRUCTURED_MEMORY status={status} polarity={polarity} evidence={evidence}]\n"
        f"{content.strip()}"
    )


def _safe_memory_text(record):
    tags = record.get("tags") or []
    return _fallback_envelope(record.get("content", ""), tags)


class LocalMemoryStore:
    """Small append-only local memory with explicit quality states."""

    def __init__(self, config: dict):
        cfg = (config or {}).get("local_memory", {})
        default_path = Path(__file__).resolve().parents[1] / "memory" / "local_memories.jsonl"
        self.enabled = bool(cfg.get("enabled", True))
        self.path = Path(cfg.get("path") or default_path).expanduser()
        self.fuzzy_recall = bool(cfg.get("fuzzy_recall", True))
        self.fuzzy_threshold = float(cfg.get("fuzzy_threshold", _DEFAULT_FUZZY_THRESHOLD))

    def store(self, content, tags=None, importance=0.5):
        if not self.enabled:
            return "disabled"
        tags = list(tags or [])
        # ANTI-CONTAMINATION fail-safe (2026-07-18): records whose tags violate
        # the taxonomy are NORMALIZED to review-only, never rejected — raising
        # or dropping would lose evidence silently (callers catch broad
        # exceptions). The record is preserved with the violation list attached
        # (taxonomy_violations) and can never be recall-safe or promoted.
        violations = validate_memory_tags(tags)
        if violations:
            tags = [
                tag for tag in tags
                if not (isinstance(tag, str) and tag.split(":", 1)[0] in
                        {"status", "kind", "visibility", "promotion", "polarity"})
            ]
            tags += [
                "status:pending_review", "kind:raw_observation",
                "visibility:local", "promotion:manual_required",
                "polarity:neutral",
            ]
        status = _tag_value(tags, "status", "pending_review")
        visibility = _tag_value(tags, "visibility", "local")
        promotion = _tag_value(tags, "promotion", "manual_required")
        record = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": MEMORY_SCHEMA_VERSION,
            "content": str(content or "").strip(),
            "tags": tags,
            "kind": _tag_value(tags, "kind", "lesson"),
            "status": status,
            "polarity": _tag_value(tags, "polarity", "neutral"),
            "evidence": _tag_value(tags, "evidence", "unspecified"),
            "visibility": visibility,
            "promotion": promotion,
            "agent_scope": _tag_value(tags, "agent_scope", "devin"),
            "share_scope": _tag_value(tags, "share_scope", "agent_local"),
            "failure_type": _tag_value(tags, "failure_type", ""),
            "memory_key": _tag_value(tags, "memory_key", ""),
            "taxonomy_violations": violations,
            "importance": float(importance),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return "local_stored"

    def _iter_records(self):
        if not self.enabled or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record

    def recall(self, query, tags=None, limit=3):
        query_tokens = set(_TOKEN_RE.findall((query or "").lower()))
        query_ngrams = _char_ngrams(query or "") if (self.fuzzy_recall and query_tokens) else set()
        requested_tags = set(tags or [])
        scored = []
        for record in self._iter_records() or []:
            status = record.get("status") or _tag_value(record.get("tags"), "status", "")
            if status not in _RECALLABLE_LOCAL_STATUSES:
                continue
            record_tags = set(record.get("tags") or [])
            if requested_tags and not (requested_tags & record_tags):
                continue
            text = record.get("content", "")
            text_tokens = set(_TOKEN_RE.findall(text.lower()))
            token_score = len(query_tokens & text_tokens)
            tag_score = len(requested_tags & record_tags)
            fuzzy_sim = 0.0
            if query_tokens and token_score == 0 and tag_score == 0:
                # Nessun overlap esatto: ultima spiaggia fuzzy sui trigrammi.
                # Il filtro qualita' (status) e' gia' applicato sopra — il fuzzy
                # allarga il RECALL, mai la sicurezza.
                if query_ngrams:
                    fuzzy_sim = _dice_similarity(query_ngrams, _char_ngrams(text))
                    if fuzzy_sim < self.fuzzy_threshold:
                        continue
                else:
                    continue
            if not query_tokens and tag_score == 0:
                continue
            score = (token_score + (2 * tag_score) + (_FUZZY_WEIGHT * fuzzy_sim)
                     + float(record.get("importance") or 0))
            scored.append((score, record.get("created_at", ""), record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [_safe_memory_text(record) for _, _, record in scored[:limit]]

    def count(self):
        return sum(1 for _ in self._iter_records() or [])

    def status(self):
        return {"enabled": self.enabled, "path": str(self.path), "records": self.count()}


class HybridMemoryClient:
    def __init__(self, config: dict):
        self.local = LocalMemoryStore(config)
        self.understory = UnderstoryClient(config)
        self.automem = AutoMemClient(config)
        self.enabled = self.local.enabled or self.understory.enabled or self.automem.enabled

    def recall(self, query, tags=None, limit=3):
        memories = self.local.recall(query, tags=tags, limit=limit)
        remaining = max(0, limit - len(memories))
        if remaining <= 0:
            return memories
        remote = self.understory.recall(query, tags=tags, limit=remaining)
        if not remote:
            remote = self.automem.recall(query, tags=tags, limit=remaining)
        for memory in remote:
            text = str(memory)
            if _remote_memory_status(text) in _EXCLUDED_RECALL_STATUSES:
                continue
            memories.append(text)
            if len(memories) >= limit:
                break
        return memories

    def store_local(self, content, tags=None, importance=0.5):
        return self.local.store(content, tags=tags, importance=importance)

    def store(self, content, tags=None, importance=0.5, queue_if_offline=True):
        """Explicit/manual shared-memory write path."""
        result = self.understory.store(
            content, tags=tags, importance=importance, queue_if_offline=queue_if_offline
        )
        if result == "stored":
            return result
        return self.automem.store(
            _fallback_envelope(content, tags),
            tags=tags,
            importance=importance,
            queue_if_offline=queue_if_offline,
        )

    def flush_outbox(self):
        return self.automem.flush_outbox()

    def outbox_size(self):
        return self.automem.outbox_size()

    def status(self):
        understory = self.understory.status()
        automem = self.automem.status()
        return {
            "enabled": self.enabled,
            "reachable": understory.get("reachable") or automem.get("reachable"),
            "backend": "understory" if understory.get("reachable") else "automem",
            "local": self.local.status(),
            "understory": understory,
            "automem": automem,
            "outbox": automem.get("outbox", 0),
        }


__all__ = ["HybridMemoryClient", "LocalMemoryStore", "project_tags"]
