#!/usr/bin/env python3
"""Correzioni SFT per i fallimenti MBPP: concetto + soluzione UFFICIALE.

Per ogni CASO mbpp con almeno un attempt verified_failure (dopo l'import del
batch review) crea UNA correzione: testo concept-first (dipende dal failure
mode) + `corrected_solution` presa dal campo `code` del dataset MBPP in cache
(la reference ufficiale, non un'invenzione). Cosi' l'export SFT insegna sia il
principio sia il comportamento corretto.

Append-only su corrections.jsonl via store.add_correction. Idempotente: salta
i casi che hanno gia' una correzione con tag mbpp_reference.

Uso:
  venv/bin/python scripts/generate_mbpp_corrections.py [--dry-run]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devin.training.store import TrainingStore  # noqa: E402
from devin.training.adapters import mbpp_cache_path  # noqa: E402

CONCEPTS = {
    "own_tests_failed": (
        "Concetto: non si consegna MAI con la propria suite rossa. I test vanno "
        "eseguiti prima di dichiarare finito; se uno fallisce si itera o si "
        "dichiara esplicitamente il fallimento."
    ),
    "gold_assertion_failed": (
        "Concetto: i reference test definiscono la semantica ESATTA richiesta "
        "(casi limite inclusi). Vanno letti e simulati mentalmente PRIMA di "
        "implementare, non dopo."
    ),
    "no_tests_produced": (
        "Concetto: quando il task chiede test, i test sono parte del "
        "deliverable. Consegna senza test = task non completato."
    ),
    "contract_violation": (
        "Concetto: nome e firma della funzione richiesta sono un contratto da "
        "rispettare alla lettera."
    ),
}
DEFAULT_CONCEPT = "Concetto: verificare il comportamento contro i reference test prima di consegnare."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(ROOT / "workspace" / "_training"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    store = TrainingStore(args.store)
    cache = mbpp_cache_path(args.store)
    if not cache.is_file():
        print("errore: cache MBPP non trovata (nessun import fatto?)", file=sys.stderr)
        return 1
    reference = {}
    for line in cache.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("task_id") is not None:
            reference[row["task_id"]] = row

    cases = {c.get("case_id"): c for c in store.list_cases(limit=100000, include_retired=True)}
    reviews = store.latest_reviews_by_attempt()
    corrected_cases = set()
    for corr in store.list_corrections(limit=100000):
        if "mbpp_reference" in (corr.get("tags") or []):
            att = corr.get("attempt_id")
            for a in store.list_attempts(limit=100000):
                if a.get("attempt_id") == att:
                    corrected_cases.add(a.get("case_id"))

    made = skipped = 0
    seen_cases = set()
    for attempt in store.list_attempts(limit=100000):
        aid = attempt.get("attempt_id")
        case_id = attempt.get("case_id")
        case = cases.get(case_id) or {}
        task_id = (case.get("metadata") or {}).get("mbpp_task_id")
        if task_id is None:
            continue  # non-MBPP
        review = reviews.get(aid) or {}
        if review.get("status") != "verified_failure":
            continue
        if case_id in corrected_cases or case_id in seen_cases:
            skipped += 1
            continue
        ref = reference.get(task_id)
        if not ref or not (ref.get("code") or "").strip():
            continue
        mode = review.get("failure_mode", "")
        concept = CONCEPTS.get(mode, DEFAULT_CONCEPT)
        correction = (
            f"{concept}\n\nFailure mode osservato: {mode or 'n/d'} — "
            f"{(review.get('rationale') or '')[:200]}\n"
            "La soluzione di riferimento ufficiale MBPP e' allegata: confrontala "
            "con il tentativo per capire DOVE la semantica divergeva."
        )
        solution = (
            f"# Soluzione di riferimento MBPP task {task_id} (ufficiale)\n"
            f"# {concept}\n"
            + (ref.get("test_setup_code") or "").strip()
            + ("\n" if (ref.get("test_setup_code") or "").strip() else "")
            + ref["code"].strip() + "\n"
        )
        seen_cases.add(case_id)
        if args.dry_run:
            print(f"[dry ] {case.get('title', '')[:60]} <- correzione ({mode})")
            made += 1
            continue
        store.add_correction(
            attempt_id=aid,
            correction=correction,
            corrected_solution=solution,
            reviewer="claude-batch",
            tags=["claude_batch", "mbpp_reference", f"fm:{mode or 'nd'}"],
        )
        made += 1
        print(f"[ok  ] {case.get('title', '')[:60]} ({mode})")

    print(f"\ncorrezioni {'previste' if args.dry_run else 'scritte'}={made} saltate(gia' presenti)={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
