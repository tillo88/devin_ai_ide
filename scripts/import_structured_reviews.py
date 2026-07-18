#!/usr/bin/env python3
"""Importa decisioni di review (e correzioni opzionali) nel TrainingStore.

Pensato per il flusso "reviewer esterno" (Claude/Colibri/Teacher offline):
il reviewer produce un JSONL di decisioni, l'umano lo guarda e lo importa con
questo script. Ogni review passa da TrainingStore.add_review => stessa
validazione della UI (attempt esistente, status ammesso), append-only, nessuna
promozione automatica in memoria.

Uso:
  venv/bin/python scripts/import_structured_reviews.py decisions.jsonl
  venv/bin/python scripts/import_structured_reviews.py decisions.jsonl --store workspace/_training

Formato riga (campi extra ignorati):
  {"attempt_id": "attempt_…", "status": "verified_success|verified_failure|
   needs_correction|runner_error|pending_review", "rationale": "...",
   "method_trace": "...", "failure_mode": "...", "next_action": "...",
   "lesson_candidate": "...", "reviewer": "claude", "confidence": 0.9,
   "tags": ["..."],
   "correction": "(opzionale) cosa andava fatto",
   "corrected_solution": "(opzionale) codice corretto -> risposta SFT"}
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devin.training.store import TrainingStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decisions", help="file JSONL con le decisioni")
    parser.add_argument("--store", default=str(ROOT / "workspace" / "_training"),
                        help="cartella del TrainingStore (default: workspace/_training)")
    parser.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe senza scrivere")
    args = parser.parse_args()

    store = TrainingStore(args.store)
    ok = failed = corrections = 0
    for line_no, line in enumerate(Path(args.decisions).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception as exc:
            print(f"[riga {line_no}] JSON non valido: {exc}", file=sys.stderr)
            failed += 1
            continue
        attempt_id = d.get("attempt_id", "")
        status = d.get("status", "")
        if args.dry_run:
            print(f"[dry-run] {attempt_id} -> {status} ({d.get('rationale', '')[:80]})")
            continue
        try:
            review = store.add_review(
                attempt_id=attempt_id,
                status=status,
                rationale=d.get("rationale", ""),
                reviewer=d.get("reviewer", "external"),
                confidence=float(d.get("confidence", 0.9)),
                tags=d.get("tags") or ["structured_import"],
                evidence=d.get("evidence") or {},
                method_trace=d.get("method_trace", ""),
                failure_mode=d.get("failure_mode", ""),
                next_action=d.get("next_action", ""),
                lesson_candidate=d.get("lesson_candidate", ""),
            )
            ok += 1
            print(f"[ok] review {review['review_id']} -> {attempt_id} = {status}")
        except Exception as exc:
            failed += 1
            print(f"[ERRORE] {attempt_id}: {exc}", file=sys.stderr)
            continue
        correction = (d.get("correction") or "").strip()
        if correction:
            try:
                item = store.add_correction(
                    attempt_id=attempt_id,
                    correction=correction,
                    corrected_solution=d.get("corrected_solution", ""),
                    reviewer=d.get("reviewer", "external"),
                    tags=(d.get("tags") or []) + ["sft_flywheel"],
                )
                corrections += 1
                print(f"[ok] correzione {item['correction_id']} -> {attempt_id}")
            except Exception as exc:
                print(f"[ERRORE correzione] {attempt_id}: {exc}", file=sys.stderr)

    print(f"\nImport finito: {ok} review, {corrections} correzioni, {failed} errori.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
