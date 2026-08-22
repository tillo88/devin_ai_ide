"""Reviewed cross-role knowledge exchange (P5).

This store is intentionally separate from every role's AutoMem/Understory raw
store. Proposals start quarantined, artifacts are content-addressed, reviews
are append-only, and only a reviewed artifact can be returned to an audience.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ARTIFACT_SCHEMA = "knowledge_exchange_artifact_v1"
REVIEW_SCHEMA = "knowledge_exchange_review_v1"
STATUS_SCHEMA = "knowledge_exchange_status_v1"
ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
VERIFIED_EVIDENCE = frozenset({"verified", "verified_success", "human_confirmed"})
DECISIONS = frozenset({"approve", "reject", "revoke"})
MAX_CONTENT_CHARS = 64_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _role(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if not ROLE_RE.fullmatch(text):
        raise ValueError(f"invalid {field}")
    return text


def _evidence_refs(items: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("evidence reference must be an object")
        evidence_id = str(item.get("evidence_id") or "").strip()
        claim = str(item.get("claim") or "").strip()
        status = str(item.get("status") or "unverified").strip().lower()
        if not evidence_id.startswith("sha256:") or len(evidence_id) != 71:
            raise ValueError("evidence_id must be a sha256 reference")
        if not claim:
            raise ValueError("evidence claim is required")
        refs.append({"evidence_id": evidence_id, "claim": claim[:500], "status": status})
    return refs


class KnowledgeExchangeStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.artifact_dir = self.root / "artifacts"
        self.review_log = self.root / "reviews.jsonl"
        self._lock = threading.RLock()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def _artifact_path(self, artifact_id: str) -> Path:
        if not re.fullmatch(r"kx_[0-9a-f]{64}", str(artifact_id or "")):
            raise ValueError("invalid artifact_id")
        path = self.artifact_dir / f"{artifact_id}.json"
        if path.parent.resolve() != self.artifact_dir.resolve():
            raise ValueError("artifact path escapes exchange root")
        return path

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("exchange log cannot be a symlink")
        line = _canonical(record) + b"\n"
        with path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def propose(
        self,
        *,
        content: str,
        source_role: str,
        source_project_id: str,
        audience: Iterable[str],
        evidence: Iterable[dict[str, Any]],
        expires_at: str | None = None,
        kind: str = "lesson",
    ) -> dict[str, Any]:
        clean_content = str(content or "").strip()
        if not clean_content or len(clean_content) > MAX_CONTENT_CHARS:
            raise ValueError("knowledge content is empty or exceeds the bound")
        source = _role(source_role, "source_role")
        audiences = sorted({_role(item, "audience") for item in audience})
        if not audiences:
            raise ValueError("at least one audience is required")
        refs = _evidence_refs(evidence)
        if not refs:
            raise ValueError("knowledge proposal requires evidence references")
        immutable = {
            "schema": ARTIFACT_SCHEMA,
            "source_role": source,
            "source_project_id": str(source_project_id or "general")[:128],
            "audience": audiences,
            "kind": str(kind or "lesson")[:64],
            "content": clean_content,
            "content_sha256": hashlib.sha256(clean_content.encode("utf-8")).hexdigest(),
            "evidence": refs,
            "expires_at": str(expires_at or "") or None,
        }
        artifact_id = "kx_" + hashlib.sha256(_canonical(immutable)).hexdigest()
        artifact = {**immutable, "artifact_id": artifact_id, "created_at": _utc_now()}
        path = self._artifact_path(artifact_id)
        with self._lock:
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if {k: existing.get(k) for k in immutable} != immutable:
                    raise ValueError("artifact id collision")
                return self.describe(existing)
            if path.is_symlink():
                raise ValueError("artifact path cannot be a symlink")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(_canonical(artifact))
            os.replace(tmp, path)
        return self.describe(artifact)

    def _load(self, artifact_id: str) -> dict[str, Any]:
        path = self._artifact_path(artifact_id)
        if not path.is_file() or path.is_symlink():
            raise ValueError("knowledge artifact not found")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != ARTIFACT_SCHEMA or value.get("artifact_id") != artifact_id:
            raise ValueError("knowledge artifact is malformed")
        return value

    def _reviews(self) -> list[dict[str, Any]]:
        if not self.review_log.exists():
            return []
        if self.review_log.is_symlink():
            raise ValueError("exchange review log cannot be a symlink")
        records = []
        for line in self.review_log.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("schema") == REVIEW_SCHEMA:
                records.append(item)
        return records

    def review(
        self,
        *,
        artifact_id: str,
        decision: str,
        reviewer_role: str,
        reason: str,
        evidence: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        artifact = self._load(artifact_id)
        clean_decision = str(decision or "").strip().lower()
        if clean_decision not in DECISIONS:
            raise ValueError("unsupported review decision")
        clean_reason = str(reason or "").strip()
        if len(clean_reason) < 8:
            raise ValueError("review reason is too short")
        reviewer = _role(reviewer_role, "reviewer_role")
        prior_status = self.effective_status(artifact_id)
        if clean_decision == "approve" and prior_status in {"rejected", "revoked"}:
            raise ValueError("a rejected or revoked artifact cannot be promoted; propose a new artifact")
        review_refs = _evidence_refs(evidence)
        if clean_decision == "approve":
            if reviewer == artifact["source_role"]:
                raise ValueError("approval requires an independent reviewer role")
            if not review_refs:
                raise ValueError("approval requires independent review evidence")
            if not any(ref["status"] in VERIFIED_EVIDENCE for ref in artifact["evidence"]):
                raise ValueError("artifact evidence is not recall-safe")
            if not any(ref["status"] in VERIFIED_EVIDENCE for ref in review_refs):
                raise ValueError("review evidence is not verified")
        payload = {
            "artifact_id": artifact_id,
            "decision": clean_decision,
            "reviewer_role": reviewer,
            "reason": clean_reason[:2000],
            "evidence": review_refs,
            "reviewed_at": _utc_now(),
        }
        record = {
            "schema": REVIEW_SCHEMA,
            "review_id": "kxr_" + hashlib.sha256(_canonical(payload)).hexdigest(),
            **payload,
        }
        with self._lock:
            if not any(item.get("review_id") == record["review_id"] for item in self._reviews()):
                self._append(self.review_log, record)
        return record

    def effective_status(self, artifact_id: str) -> str:
        decisions = [
            item["decision"] for item in self._reviews()
            if item.get("artifact_id") == artifact_id
        ]
        if not decisions:
            return "quarantine"
        return {"approve": "promoted", "reject": "rejected", "revoke": "revoked"}[decisions[-1]]

    def describe(self, artifact: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
        result = {k: v for k, v in artifact.items() if k != "content"}
        result["status"] = self.effective_status(artifact["artifact_id"])
        if include_content:
            result["content"] = artifact["content"]
        return result

    def list_artifacts(self, *, include_content: bool = False) -> list[dict[str, Any]]:
        return [
            self.describe(self._load(path.stem), include_content=include_content)
            for path in sorted(self.artifact_dir.glob("kx_*.json"))
            if path.is_file() and not path.is_symlink()
        ]

    def list_promoted(self, audience: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
        target = _role(audience, "audience")
        current = now or datetime.now(timezone.utc)
        promoted = []
        for item in self.list_artifacts(include_content=True):
            if item["status"] != "promoted" or target not in item["audience"]:
                continue
            expires_at = item.get("expires_at")
            if expires_at:
                try:
                    expiry = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if expiry <= current:
                    continue
            promoted.append(item)
        return promoted

    def status(self) -> dict[str, Any]:
        artifacts = self.list_artifacts()
        counts = {name: 0 for name in ("quarantine", "promoted", "rejected", "revoked")}
        for item in artifacts:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {
            "schema": STATUS_SCHEMA,
            "storage": "separate_reviewed_exchange",
            "raw_store_shared": False,
            "counts": counts,
            "artifacts": artifacts,
        }
