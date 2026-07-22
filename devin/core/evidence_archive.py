"""Context Steward CS1 - content-addressed evidence archive (NVMe-ready).

See docs/CONTEXT_STEWARD_PLAN.md section CS1. Large operational artifacts
(logs, diffs, test output, manifests, raw command output) live on disk,
content-addressed by SHA-256. The in-context checkpoint holds only compact
REFERENCES (evidence_id + claim + status + location), never the artifact body.

This enforces the cardinal rule: never replace evidence with a summary. A
summary can drift across compactions; a content-addressed reference cannot -
the exact bytes are always retrievable and verifiable by hash.

Deterministic, GPU/LLM-free. Timestamps go through devin.core.time_service so
they are UTC-canonical with an Europe/Rome display, consistent with run_events.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from devin.core.time_service import timestamp_bundle

INDEX_NAME = "index.jsonl"
EVIDENCE_DIRNAME = "evidence"
_HASH_RE_LEN = 64  # sha256 hex length


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceArchiveError(Exception):
    pass


class EvidenceArchive:
    """Append-only, content-addressed store under a session/task base dir.

    Layout:
        <base>/evidence/<sha256>            # raw artifact bytes (immutable)
        <base>/index.jsonl                  # append-only metadata records
    """

    def __init__(self, base_dir: str | Path):
        self.base = Path(base_dir)
        self.evidence_dir = self.base / EVIDENCE_DIRNAME
        self.index_path = self.base / INDEX_NAME
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    # ---- write ------------------------------------------------------------
    def store(self, content: str | bytes, *, kind: str = "raw",
              meta: Optional[Dict[str, Any]] = None) -> str:
        """Store an artifact and return its evidence_id ("sha256:<hex>").

        Idempotent: identical content stored twice yields the same id and does
        not duplicate the blob. The index still records each logical store call
        so provenance (kind/meta/when) is preserved per occurrence.
        """
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        digest = _sha256_hex(data)
        evidence_id = f"sha256:{digest}"
        blob_path = self.evidence_dir / digest

        if not blob_path.exists():
            tmp = blob_path.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(blob_path)  # atomic, portable (Windows/POSIX)

        stamp = timestamp_bundle()
        record = {
            "evidence_id": evidence_id,
            "sha256": digest,
            "kind": str(kind or "raw"),
            "bytes": len(data),
            "created_at": stamp["timestamp_utc"],
            "created_at_local": stamp["timestamp_local"],
            "meta": meta or {},
            "location": str(blob_path),
        }
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return evidence_id

    # ---- read -------------------------------------------------------------
    def _digest_of(self, evidence_id: str) -> str:
        raw = str(evidence_id or "").strip()
        digest = raw.split(":", 1)[1] if raw.startswith("sha256:") else raw
        if len(digest) != _HASH_RE_LEN or any(c not in "0123456789abcdef" for c in digest):
            raise EvidenceArchiveError(f"invalid evidence_id: {evidence_id!r}")
        return digest

    def get_bytes(self, evidence_id: str) -> bytes:
        """Return the exact artifact bytes, verifying integrity by hash."""
        digest = self._digest_of(evidence_id)
        blob_path = self.evidence_dir / digest
        if not blob_path.exists():
            raise EvidenceArchiveError(f"evidence not found: {evidence_id}")
        data = blob_path.read_bytes()
        if _sha256_hex(data) != digest:
            raise EvidenceArchiveError(f"integrity check failed: {evidence_id}")
        return data

    def get_text(self, evidence_id: str) -> str:
        return self.get_bytes(evidence_id).decode("utf-8")

    def exists(self, evidence_id: str) -> bool:
        try:
            return (self.evidence_dir / self._digest_of(evidence_id)).exists()
        except EvidenceArchiveError:
            return False

    def records(self) -> Iterable[Dict[str, Any]]:
        """Yield index records in append order (provenance log)."""
        if not self.index_path.exists():
            return
        with self.index_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    # ---- reference (what goes INTO the checkpoint) ------------------------
    @staticmethod
    def make_ref(evidence_id: str, *, claim: str, status: str = "unverified",
                 location: Optional[str] = None) -> Dict[str, Any]:
        """Build the compact reference embedded in a checkpoint.

        Deliberately carries the CLAIM and a verifiable pointer, never the
        artifact body. `status` is one of unverified/verified/refuted/
        inconclusive - the Steward never sets 'verified' on its own; that comes
        from the orchestrator/validator (anti-contamination).
        """
        if status not in {"unverified", "verified", "refuted", "inconclusive"}:
            raise EvidenceArchiveError(f"invalid status: {status!r}")
        ref = {
            "claim": str(claim),
            "status": status,
            "evidence_id": str(evidence_id),
        }
        if location:
            ref["location"] = str(location)
        return ref
