"""Context Steward CS2 - hybrid evidence retrieval.

See docs/CONTEXT_STEWARD_PLAN.md section CS2. Three ways to recover evidence,
in order of reliability for technical data:

  1. exact lookup   - by evidence_id / sha256 / run_id / file path
  2. structured     - filter index records by metadata (status, model, test...)
  3. keyword/semantic-lite - token-overlap score over kind + metadata text

The retriever returns SMALL bounded fragments, never whole artifacts: the whole
point is to spend O(hundreds) of tokens per turn, not O(10^4). A true embedding
path (VectorStore) can extend `search` later; the deterministic core here works
offline with no GPU/LLM, consistent with the project's offline-first stance.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from devin.core.evidence_archive import EvidenceArchive

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _meta_text(record: Dict[str, Any]) -> str:
    """Flatten kind + metadata values into one searchable string."""
    parts = [str(record.get("kind") or "")]
    meta = record.get("meta") or {}
    for key, value in meta.items():
        parts.append(str(key))
        parts.append(str(value))
    return " ".join(parts)


class EvidenceRetriever:
    def __init__(self, archive: EvidenceArchive):
        self.archive = archive

    def _all_records(self) -> List[Dict[str, Any]]:
        return list(self.archive.records())

    # ---- 1. exact lookup --------------------------------------------------
    def by_id(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        """Most recent index record for an evidence_id (or bare sha256)."""
        target = str(evidence_id or "").strip()
        bare = target.split(":", 1)[1] if target.startswith("sha256:") else target
        found = None
        for rec in self._all_records():
            if rec.get("evidence_id") == target or rec.get("sha256") == bare:
                found = rec  # keep last (most recent occurrence)
        return found

    def by_run(self, run_id: str) -> List[Dict[str, Any]]:
        """All records whose meta.run_id matches (exact technical lookup)."""
        return [r for r in self._all_records()
                if str((r.get("meta") or {}).get("run_id") or "") == str(run_id)]

    # ---- 2. structured query ---------------------------------------------
    def by_meta(self, **filters: Any) -> List[Dict[str, Any]]:
        """Records matching ALL given filters. `kind` matches the top-level
        field; everything else matches inside meta. Values compared as strings.
        """
        results = []
        for rec in self._all_records():
            meta = rec.get("meta") or {}
            ok = True
            for key, wanted in filters.items():
                actual = rec.get("kind") if key == "kind" else meta.get(key)
                if str(actual) != str(wanted):
                    ok = False
                    break
            if ok:
                results.append(rec)
        return results

    # ---- 3. keyword / semantic-lite --------------------------------------
    def search(self, query: str, *, top_k: int = 3) -> List[Dict[str, Any]]:
        """Rank records by token overlap between the query and their
        kind+metadata text. Deterministic, offline. Ties broken by recency
        (later index position wins)."""
        q = set(_tokens(query))
        if not q:
            return []
        scored = []
        for position, rec in enumerate(self._all_records()):
            doc = set(_tokens(_meta_text(rec)))
            overlap = len(q & doc)
            if overlap:
                scored.append((overlap, position, rec))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [rec for _, _, rec in scored[:max(1, top_k)]]

    # ---- bounded fragment extraction -------------------------------------
    def fragment(self, evidence_id: str, *, max_chars: int = 1500,
                 query: Optional[str] = None) -> str:
        """Return a BOUNDED slice of an artifact, never the whole thing.

        With a query, return the matching lines (grep-like) around the hits;
        otherwise the head. Output is hard-capped at `max_chars`.
        """
        text = self.archive.get_text(evidence_id)
        if query:
            q = set(_tokens(query))
            matched: List[str] = []
            used = 0
            for line in text.splitlines():
                if q & set(_tokens(line)):
                    piece = line.strip()
                    if used + len(piece) + 1 > max_chars:
                        break
                    matched.append(piece)
                    used += len(piece) + 1
            if matched:
                return "\n".join(matched)[:max_chars]
        return text[:max_chars]

    def refs_for_context(self, records: Iterable[Dict[str, Any]], *,
                         status: str = "unverified") -> List[Dict[str, Any]]:
        """Turn index records into compact checkpoint references (no bodies)."""
        refs = []
        for rec in records:
            refs.append(EvidenceArchive.make_ref(
                rec.get("evidence_id", ""),
                claim=str((rec.get("meta") or {}).get("claim")
                          or rec.get("kind") or "evidence"),
                status=status,
                location=rec.get("location"),
            ))
        return refs
