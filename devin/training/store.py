
import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SAFE_SUCCESS_STATUSES = {"verified_success", "human_confirmed"}
FAILURE_STATUSES = {"verified_failure", "failed", "needs_correction"}
AUTO_SUCCESS_STATUSES = {"auto_success"}
AUTO_FAILURE_STATUSES = {"auto_failure"}
INFRA_STATUSES = {"runner_error"}
REVIEW_STATUSES = SAFE_SUCCESS_STATUSES | FAILURE_STATUSES | INFRA_STATUSES | {"pending_review"}
# Status ammessi per un ATTEMPT (2026-07-18): un typo tipo "auto_succes"
# prima spariva da review_queue e summary senza alcun errore. Ogni producer
# (runner training, endpoint /api/training/attempts) usa solo questi valori.
ATTEMPT_STATUSES = (SAFE_SUCCESS_STATUSES | FAILURE_STATUSES | AUTO_SUCCESS_STATUSES
                    | AUTO_FAILURE_STATUSES | INFRA_STATUSES | {"pending_review"})
# Review con VERDETTO: tutte tranne pending_review (presa in carico senza
# esito). Solo queste tolgono un attempt dalla review_queue.
VERDICT_REVIEW_STATUSES = REVIEW_STATUSES - {"pending_review"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _write_export_jsonl(target: Path, rows: List[Dict[str, Any]], export_format: str) -> Dict[str, Any]:
    """Write an export atomically and persist authoritative ordering metadata.

    Some shared/virtual filesystems coalesce nanosecond mtimes.  A sidecar with
    a logical creation timestamp keeps ``list_exports`` deterministic even when
    two different exports are produced in the same clock tick.  The digest
    prevents stale sidecars from being trusted after an external file edit.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)

    created_at_ns = time.time_ns()
    metadata = {
        "schema_version": "training_export_meta_v1",
        "filename": target.name,
        "format": export_format,
        "rows": len(rows),
        "size": len(payload.encode("utf-8")),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "created_at": datetime.now().isoformat(timespec="microseconds"),
        "created_at_ns": created_at_ns,
    }
    metadata_path = target.with_suffix(target.suffix + ".meta.json")
    metadata_tmp = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
    metadata_tmp.write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    metadata_tmp.replace(metadata_path)
    return metadata


class TrainingStore:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.cases_file = self.base_dir / "cases.jsonl"
        self.attempts_file = self.base_dir / "attempts.jsonl"
        self.corrections_file = self.base_dir / "corrections.jsonl"
        self.reviews_file = self.base_dir / "reviews.jsonl"
        self.lessons_file = self.base_dir / "lessons.jsonl"
        self.exports_dir = self.base_dir / "datasets"

    def summary(self) -> Dict[str, Any]:
        cases = self.list_cases()
        attempts = self.list_attempts()
        corrections = self.list_corrections()
        reviews = self.list_reviews()
        lessons = self.list_lessons()
        successes = [item for item in attempts if item.get("status") in SAFE_SUCCESS_STATUSES]
        failures = [item for item in attempts if item.get("status") in FAILURE_STATUSES]
        auto_successes = [item for item in attempts if item.get("status") in AUTO_SUCCESS_STATUSES]
        auto_failures = [item for item in attempts if item.get("status") in AUTO_FAILURE_STATUSES]
        infra_errors = [item for item in attempts if item.get("status") in INFRA_STATUSES]
        review_successes = [item for item in reviews if item.get("status") in SAFE_SUCCESS_STATUSES]
        review_failures = [item for item in reviews if item.get("status") in FAILURE_STATUSES]
        review_infra = [item for item in reviews if item.get("status") in INFRA_STATUSES]
        return {
            "path": str(self.base_dir),
            "cases": len(cases),
            "attempts": len(attempts),
            "corrections": len(corrections),
            "reviews": len(reviews),
            "lessons": len(lessons),
            "verified_success": len(successes),
            "verified_failure": len(failures),
            "auto_success": len(auto_successes),
            "auto_failure": len(auto_failures),
            "runner_error": len(infra_errors),
            "review_verified_success": len(review_successes),
            "review_verified_failure": len(review_failures),
            "review_runner_error": len(review_infra),
            "last_attempt_at": attempts[-1].get("created_at") if attempts else None,
            "last_review_at": reviews[-1].get("created_at") if reviews else None,
        }

    def add_case(self, task: str, title: str = "", kind: str = "custom", tags: Optional[List[str]] = None,
                 source: str = "manual", expected_signals: Optional[List[str]] = None,
                 metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        case = {
            "case_id": f"case_{uuid.uuid4().hex[:12]}",
            "title": (title or task[:80] or "Untitled training case").strip(),
            "task": (task or "").strip(),
            "kind": kind or "custom",
            "source": source or "manual",
            "tags": tags or [],
            "expected_signals": expected_signals or [],
            "metadata": metadata or {},
            "created_at": _now(),
            "status": "active",
        }
        if not case["task"]:
            raise ValueError("training case task is required")
        _append_jsonl(self.cases_file, case)
        return case

    def seed_cases(self, cases: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
        """Seed idempotente con SUPERSEDE (2026-07-15): se esiste gia' un caso
        attivo con stesso (source, title) ma task/metadata aggiornati (es. gold
        tests aggiunti), il vecchio viene RITIRATO e si crea la versione nuova.
        Cosi' il reseed aggiorna il bench senza far girare doppioni."""
        active = self.list_cases(limit=10000)
        by_key = {(item.get("source"), item.get("title"), item.get("task")): item for item in active}
        by_title = {(item.get("source"), item.get("title")): item for item in active}
        created = []
        for raw in cases:
            task = raw.get("prompt") or raw.get("task") or ""
            title = raw.get("title", "")
            if (source, title, task) in by_key:
                continue
            old = by_title.get((source, title))
            if old:
                self.retire_case(old.get("case_id"), reason="superseded by reseed")
            created.append(self.add_case(
                task=task,
                title=title,
                kind=raw.get("kind", "benchmark"),
                tags=raw.get("tags", []),
                source=source,
                expected_signals=raw.get("expected_signals", []),
                metadata={k: v for k, v in raw.items() if k not in {"prompt", "task", "title", "kind", "tags", "expected_signals"}},
            ))
        return created

    def retire_case(self, case_id: str, reason: str = "") -> None:
        """Tombstone append-only: il caso resta nello storico (i vecchi attempt
        continuano a risolvere il titolo) ma esce da list_cases() e dai run."""
        if not case_id:
            return
        _append_jsonl(self.cases_file, {
            "op": "retire",
            "case_id": case_id,
            "reason": reason or "",
            "created_at": _now(),
        })

    def list_cases(self, limit: int = 200, include_retired: bool = False) -> List[Dict[str, Any]]:
        records = _read_jsonl(self.cases_file)
        retired = {r.get("case_id") for r in records if r.get("op") == "retire"}
        cases = [r for r in records if r.get("op") != "retire"]
        if not include_retired:
            cases = [r for r in cases if r.get("case_id") not in retired]
        return cases[-limit:]

    def add_attempt(self, case_id: str, prompt: str = "", response: str = "", status: str = "pending_review",
                    tests: Optional[Dict[str, Any]] = None, error_reason: str = "",
                    run_id: str = "", artifacts: Optional[List[str]] = None) -> Dict[str, Any]:
        clean_status = (status or "pending_review").strip() or "pending_review"
        if clean_status not in ATTEMPT_STATUSES:
            raise ValueError(f"unsupported attempt status: {clean_status}")
        attempt = {
            "attempt_id": f"attempt_{uuid.uuid4().hex[:12]}",
            "case_id": case_id or "manual",
            "prompt": prompt or "",
            "response": response or "",
            "status": clean_status,
            "tests": tests or {},
            "error_reason": error_reason or "",
            "run_id": run_id or "",
            "artifacts": artifacts or [],
            "created_at": _now(),
        }
        _append_jsonl(self.attempts_file, attempt)
        return attempt

    def list_attempts(self, case_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        items = _read_jsonl(self.attempts_file)
        if case_id:
            items = [item for item in items if item.get("case_id") == case_id]
        return items[-limit:]

    def add_review(self, attempt_id: str, status: str, rationale: str = "", reviewer: str = "human",
                   confidence: float = 1.0, tags: Optional[List[str]] = None,
                   evidence: Optional[Dict[str, Any]] = None, method_trace: str = "",
                   failure_mode: str = "", next_action: str = "", lesson_candidate: str = "") -> Dict[str, Any]:
        attempts = {item.get("attempt_id") for item in self.list_attempts(limit=10000)}
        clean_status = (status or "").strip()
        if not attempt_id or attempt_id not in attempts:
            raise ValueError("known attempt_id is required")
        if clean_status not in REVIEW_STATUSES:
            raise ValueError(f"unsupported review status: {clean_status}")
        try:
            clean_confidence = max(0.0, min(float(confidence), 1.0))
        except Exception:
            clean_confidence = 1.0
        review = {
            "review_id": f"review_{uuid.uuid4().hex[:12]}",
            "attempt_id": attempt_id,
            "status": clean_status,
            "rationale": (rationale or "").strip(),
            "method_trace": (method_trace or "").strip(),
            "failure_mode": (failure_mode or "").strip(),
            "next_action": (next_action or "").strip(),
            "lesson_candidate": (lesson_candidate or "").strip(),
            "reviewer": reviewer or "human",
            "confidence": clean_confidence,
            "tags": tags or [],
            "evidence": evidence or {},
            "created_at": _now(),
            "promotion": "eligible" if clean_status in SAFE_SUCCESS_STATUSES else "manual_required",
        }
        _append_jsonl(self.reviews_file, review)
        return review

    def list_reviews(self, attempt_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        items = _read_jsonl(self.reviews_file)
        if attempt_id:
            items = [item for item in items if item.get("attempt_id") == attempt_id]
        return items[-limit:]

    def latest_reviews_by_attempt(self, limit: int = 10000) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for review in self.list_reviews(limit=limit):
            attempt_id = review.get("attempt_id")
            if attempt_id:
                latest[attempt_id] = review
        return latest

    def review_queue(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Attempts awaiting Teacher/human validation, newest first.

        In coda: status auto_success/auto_failure/runner_error/pending_review
        la cui ULTIMA review non porta un verdetto (o non esiste). FIX
        (2026-07-18): prima QUALUNQUE review — anche una pending_review di
        semplice presa in carico senza esito — toglieva l'attempt dalla coda
        per sempre. Ora solo le review con verdetto (VERDICT_REVIEW_STATUSES)
        la chiudono; una nuova pending_review dopo un verdetto la RIAPRE.
        Ogni voce porta gia' l'evidenza che serve al reviewer (quality gate +
        validatori semantici del caso), cosi' la decisione non richiede di
        riaprire attempts.jsonl a mano.
        """
        needs_review = (AUTO_SUCCESS_STATUSES | AUTO_FAILURE_STATUSES
                        | INFRA_STATUSES | {"pending_review"})
        reviewed = {attempt_id
                    for attempt_id, review in self.latest_reviews_by_attempt().items()
                    if (review.get("status") or "") in VERDICT_REVIEW_STATUSES}
        cases = {item.get("case_id"): item for item in self.list_cases(limit=10000, include_retired=True)}
        queue: List[Dict[str, Any]] = []
        for attempt in self.list_attempts(limit=10000):
            if attempt.get("status") not in needs_review:
                continue
            if attempt.get("attempt_id") in reviewed:
                continue
            case = cases.get(attempt.get("case_id"), {})
            tests = attempt.get("tests") or {}
            gate = tests.get("quality_gate") or {}
            validators = tests.get("validators") or {}
            signals = validators.get("signals") or {}
            queue.append({
                "attempt_id": attempt.get("attempt_id"),
                "case_id": attempt.get("case_id"),
                "title": case.get("title") or attempt.get("case_id") or attempt.get("attempt_id"),
                "status": attempt.get("status"),
                "created_at": attempt.get("created_at"),
                "run_id": attempt.get("run_id") or "",
                "error_reason": (attempt.get("error_reason") or "")[:400],
                "expected_signals": case.get("expected_signals") or [],
                "gate": {
                    "status": gate.get("status"),
                    "tests_run": gate.get("tests_run"),
                    "test_command": gate.get("test_command"),
                    "errors": [str(e)[:200] for e in (gate.get("errors") or [])][:5],
                },
                "validators": {
                    "overall": validators.get("overall"),
                    "signals": {k: v.get("verdict") for k, v in signals.items()
                                if isinstance(v, dict)},
                },
                "artifacts": attempt.get("artifacts") or [],
            })
        queue.reverse()
        return queue[:limit]

    def add_correction(self, attempt_id: str, correction: str, corrected_solution: str = "",
                       reviewer: str = "human", tags: Optional[List[str]] = None) -> Dict[str, Any]:
        item = {
            "correction_id": f"correction_{uuid.uuid4().hex[:12]}",
            "attempt_id": attempt_id,
            "correction": (correction or "").strip(),
            "corrected_solution": corrected_solution or "",
            "reviewer": reviewer or "human",
            "tags": tags or [],
            "created_at": _now(),
        }
        if not item["attempt_id"] or not item["correction"]:
            raise ValueError("attempt_id and correction are required")
        _append_jsonl(self.corrections_file, item)
        return item

    def list_corrections(self, limit: int = 200) -> List[Dict[str, Any]]:
        return _read_jsonl(self.corrections_file)[-limit:]

    def add_lesson(self, content: str, status: str = "pending_review", tags: Optional[List[str]] = None,
                   evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        lesson = {
            "lesson_id": f"lesson_{uuid.uuid4().hex[:12]}",
            "content": (content or "").strip(),
            "status": status or "pending_review",
            "tags": tags or [],
            "evidence": evidence or {},
            "created_at": _now(),
            "promotion": "eligible" if status in SAFE_SUCCESS_STATUSES else "manual_required",
        }
        if not lesson["content"]:
            raise ValueError("lesson content is required")
        _append_jsonl(self.lessons_file, lesson)
        return lesson

    def list_lessons(self, limit: int = 200) -> List[Dict[str, Any]]:
        return _read_jsonl(self.lessons_file)[-limit:]



    def list_exports(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.exports_dir.exists():
            return []
        items = []
        for path in self.exports_dir.glob("*.jsonl"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            first_line = next((line for line in text.splitlines() if line.strip()), "")
            export_format = "unknown"
            if "teacher_review_v1" in first_line:
                export_format = "teacher_review_v1"
            elif '"messages"' in first_line:
                export_format = "sft_messages_jsonl"

            stat = path.stat()
            logical_order_ns = stat.st_mtime_ns
            created_at = None
            metadata_path = path.with_suffix(path.suffix + ".meta.json")
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if (
                    metadata.get("schema_version") == "training_export_meta_v1"
                    and metadata.get("filename") == path.name
                    and metadata.get("sha256") == digest
                ):
                    logical_order_ns = int(metadata.get("created_at_ns") or logical_order_ns)
                    created_at = metadata.get("created_at") or None
                    export_format = metadata.get("format") or export_format
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                # Legacy exports and corrupt/stale sidecars remain listable;
                # their filesystem timestamp is only a deterministic fallback.
                pass
            items.append({
                "filename": path.name,
                "path": str(path),
                "format": export_format,
                "rows": sum(1 for line in text.splitlines() if line.strip()),
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "created_at": created_at,
                "_logical_order_ns": logical_order_ns,
            })
        items.sort(
            key=lambda item: (item["_logical_order_ns"], item["filename"]),
            reverse=True,
        )
        selected = items[:max(0, limit)]
        for item in selected:
            item.pop("_logical_order_ns", None)
        return selected

    def export_teacher_packet(self, filename: str = "") -> Dict[str, Any]:
        """Export review-ready JSONL for TEACHER/Colibri without promoting memory.

        Each row is intentionally evidence-heavy and asks the reviewer to classify
        the attempt before anything can become recall-safe knowledge.
        """
        cases = {item.get("case_id"): item for item in self.list_cases(limit=10000, include_retired=True)}
        corrections_by_attempt: Dict[str, List[Dict[str, Any]]] = {}
        for correction in self.list_corrections(limit=10000):
            corrections_by_attempt.setdefault(correction.get("attempt_id", ""), []).append(correction)
        reviews_by_attempt: Dict[str, List[Dict[str, Any]]] = {}
        for review in self.list_reviews(limit=10000):
            reviews_by_attempt.setdefault(review.get("attempt_id", ""), []).append(review)

        rows = []
        for attempt in self.list_attempts(limit=10000):
            case = cases.get(attempt.get("case_id"), {})
            status = attempt.get("status") or "pending_review"
            rows.append({
                "packet_version": "teacher_review_v1",
                "review_task": (
                    "Classify this DEVIN attempt as verified_success, verified_failure, "
                    "needs_correction, or runner_error. Identify violated constraints, "
                    "summarize the operational method that led to the outcome, suggest a "
                    "corrected solution when possible, and propose only safe lessons."
                ),
                "promotion_policy": {
                    "auto_promote": False,
                    "recall_safe_only": sorted(SAFE_SUCCESS_STATUSES),
                    "review_only": sorted(FAILURE_STATUSES | AUTO_SUCCESS_STATUSES | AUTO_FAILURE_STATUSES | INFRA_STATUSES),
                },
                "case": {
                    "case_id": attempt.get("case_id"),
                    "benchmark_id": case.get("source"),
                    "title": case.get("title"),
                    "task": case.get("task") or attempt.get("prompt"),
                    "kind": case.get("kind"),
                    "tags": case.get("tags") or [],
                    "expected_signals": case.get("expected_signals") or [],
                    "metadata": case.get("metadata") or {},
                },
                "attempt": {
                    "attempt_id": attempt.get("attempt_id"),
                    "run_id": attempt.get("run_id"),
                    "status": status,
                    "created_at": attempt.get("created_at"),
                    "prompt": attempt.get("prompt"),
                    "response": attempt.get("response"),
                    "tests": attempt.get("tests") or {},
                    "error_reason": attempt.get("error_reason"),
                    "artifacts": attempt.get("artifacts") or [],
                    "requires_teacher_validation": status in (AUTO_SUCCESS_STATUSES | AUTO_FAILURE_STATUSES),
                },
                "known_reviews": reviews_by_attempt.get(attempt.get("attempt_id"), []),
                "known_corrections": corrections_by_attempt.get(attempt.get("attempt_id"), []),
            })

        if not filename:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"devin_teacher_packet_{stamp}.jsonl"
        target = self.exports_dir / Path(filename).name
        metadata = _write_export_jsonl(target, rows, "teacher_review_v1")
        return {
            "path": str(target),
            "rows": len(rows),
            "format": "teacher_review_v1",
            "created_at": metadata["created_at"],
        }

    def export_sft_dataset(self, filename: str = "") -> Dict[str, Any]:
        attempts = {item.get("attempt_id"): item for item in self.list_attempts(limit=10000)}
        cases = {item.get("case_id"): item for item in self.list_cases(limit=10000, include_retired=True)}
        rows = []
        for correction in self.list_corrections(limit=10000):
            attempt = attempts.get(correction.get("attempt_id"), {})
            case = cases.get(attempt.get("case_id"), {})
            solution = correction.get("corrected_solution") or correction.get("correction")
            if not solution:
                continue
            rows.append({
                "messages": [
                    {"role": "system", "content": "You are DEVIN, a local coding agent. Produce verified, testable coding work."},
                    {"role": "user", "content": case.get("task") or attempt.get("prompt") or "Fix the previous attempt."},
                    {"role": "assistant", "content": solution},
                ],
                "metadata": {
                    "case_id": attempt.get("case_id"),
                    "attempt_id": attempt.get("attempt_id"),
                    "correction_id": correction.get("correction_id"),
                    "source": case.get("source"),
                    "tags": sorted(set((case.get("tags") or []) + (correction.get("tags") or []))),
                },
            })
        if not filename:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"devin_training_sft_{stamp}.jsonl"
        target = self.exports_dir / Path(filename).name
        metadata = _write_export_jsonl(target, rows, "sft_messages_jsonl")
        return {
            "path": str(target),
            "rows": len(rows),
            "format": "sft_messages_jsonl",
            "created_at": metadata["created_at"],
        }
