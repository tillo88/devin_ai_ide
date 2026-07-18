#!/usr/bin/env python3
"""Genera decisioni di review BATCH, evidence-based, dagli attempt non reviewati.

Regola ferrea: la decisione non va MAI oltre l'evidenza macchina.
  - runner_error                                  -> runner_error (infra)
  - auto_success CON gate verified_success (test eseguiti, gold intatti,
    validatori non-fail)                          -> verified_success
  - auto_success SENZA quell'evidenza             -> lasciato in coda (needs_human)
  - auto_failure                                  -> verified_failure, con
    failure_mode CLASSIFICATO (serve per le correzioni concettuali)

Output:
  1. <out>.jsonl      decisioni per scripts/import_structured_reviews.py
  2. <out>.report.md  riepilogo: pass rate, failure mode per classe, campioni
                      d'errore -> base per le correzioni concept-level.

Uso:
  venv/bin/python scripts/generate_claude_reviews.py \
      [--store workspace/_training] [--out workspace/_training/imports/claude_batch]
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devin.training.store import TrainingStore  # noqa: E402

REVIEWER = "claude-batch"


def classify_failure(attempt) -> tuple[str, str]:
    """Ritorna (failure_mode, dettaglio breve) dalla sola evidenza registrata."""
    tests = attempt.get("tests") or {}
    gate = tests.get("quality_gate") or {}
    validators = tests.get("validators") or {}
    signals = validators.get("signals") or {}
    text = " ".join([attempt.get("error_reason") or ""] + [str(e) for e in (gate.get("errors") or [])])

    if tests.get("gold_tampered"):
        return "gold_tampered", "gold test sovrascritti dal modello"
    inv = signals.get("no_invented_endpoint") or {}
    if inv.get("verdict") == "fail":
        return "invented_endpoint", inv.get("detail", "")[:200]
    if "nessuna funzione" in text:
        return "contract_violation", "funzione richiesta dal contratto non trovata nei moduli"
    if "SyntaxError" in text:
        return "syntax_error", "codice generato non compila"
    if "timeout" in text.lower():
        return "test_timeout", "esecuzione test oltre il timeout"
    if gate.get("tests_run") and gate.get("errors"):
        if "test_gold" in text:
            return "gold_assertion_failed", "implementazione presente ma comportamento sbagliato sui gold"
        return "own_tests_failed", "falliscono i test scritti dal modello stesso"
    tp = signals.get("tests_pass") or {}
    if tp.get("verdict") == "fail" and not gate.get("tests_run"):
        return "no_tests_produced", "il caso richiede test ma non ne sono stati trovati/eseguiti"
    if "empty file plan" in text or "No models available" in text:
        return "planner_failure", "nessun piano file valido dal planner"
    return "unclassified", text[:200]


LESSONS = {
    "invented_endpoint": "Gli endpoint di API terze vanno presi dalla doc ufficiale, mai composti per analogia.",
    "contract_violation": "Il nome/firma della funzione richiesta dal task e' un CONTRATTO: va rispettato alla lettera, non parafrasato.",
    "gold_assertion_failed": "Implementazione superficialmente plausibile ma semanticamente sbagliata: rileggere i reference test PRIMA di scrivere il codice.",
    "own_tests_failed": "Consegnare con la propria suite rossa non e' mai accettabile: eseguire i test prima di dichiarare finito.",
    "no_tests_produced": "Quando il task chiede test, i test sono parte del deliverable, non un extra.",
    "syntax_error": "Il codice va almeno compilato mentalmente: errori di sintassi = consegna non verificata.",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(ROOT / "workspace" / "_training"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ap.add_argument("--out", default=str(ROOT / "workspace" / "_training" / "imports" / f"claude_batch_{stamp}"))
    args = ap.parse_args()

    store = TrainingStore(args.store)
    cases = {c.get("case_id"): c for c in store.list_cases(limit=100000, include_retired=True)}
    reviewed = set(store.latest_reviews_by_attempt().keys())
    attempts = [a for a in store.list_attempts(limit=100000)
                if a.get("attempt_id") not in reviewed
                and a.get("status") in {"auto_success", "auto_failure", "runner_error", "pending_review"}]

    decisions = []
    needs_human = []
    by_mode = defaultdict(list)
    counts = Counter()

    for attempt in attempts:
        aid = attempt.get("attempt_id")
        case = cases.get(attempt.get("case_id"), {})
        title = case.get("title") or attempt.get("case_id") or aid
        tests = attempt.get("tests") or {}
        gate = tests.get("quality_gate") or {}
        validators = tests.get("validators") or {}
        status = attempt.get("status")

        if status == "runner_error":
            counts["runner_error"] += 1
            decisions.append({
                "attempt_id": aid, "status": "runner_error", "reviewer": REVIEWER,
                "confidence": 1.0,
                "rationale": f"Errore infrastrutturale, non del modello: {(attempt.get('error_reason') or '')[:200]}",
                "method_trace": "error_reason/infra_error dal runner, nessun output modello valutabile",
                "failure_mode": "infrastruttura", "next_action": "", "lesson_candidate": "",
                "tags": ["claude_batch", "infra"],
            })
            continue

        if status == "auto_success":
            solid = (gate.get("status") == "verified_success" and gate.get("tests_run")
                     and not tests.get("gold_tampered")
                     and validators.get("overall") in {"pass", "unknown", None})
            if solid:
                counts["verified_success"] += 1
                gold = ", ".join(tests.get("gold_tests") or []) or "nessun gold (caso senza)"
                decisions.append({
                    "attempt_id": aid, "status": "verified_success", "reviewer": REVIEWER,
                    "confidence": 0.9,
                    "rationale": f"Gate verified_success con evidenza: {gate.get('test_command')} su "
                                 f"{len(gate.get('test_files') or [])} file test (gold: {gold}), gold intatti, "
                                 f"validatori {validators.get('overall', 'n/d')}.",
                    "method_trace": f"test_output: {((gate.get('test_output') or '').strip().splitlines() or [''])[-1][:120]}",
                    "failure_mode": "", "next_action": "",
                    "lesson_candidate": "",
                    "tags": ["claude_batch", "gold_verified"],
                })
            else:
                counts["needs_human"] += 1
                needs_human.append((aid, title, "auto_success senza evidenza forte (gate non verified/gold assenti)"))
            continue

        # auto_failure / pending_review
        mode, detail = classify_failure(attempt)
        counts[f"fail:{mode}"] += 1
        by_mode[mode].append((aid, title, detail))
        decisions.append({
            "attempt_id": aid, "status": "verified_failure", "reviewer": REVIEWER,
            "confidence": 0.9 if mode != "unclassified" else 0.7,
            "rationale": f"[{mode}] {detail}"[:400],
            "method_trace": "classificazione da quality_gate.errors + validatori registrati nell'attempt",
            "failure_mode": mode,
            "next_action": "candidato a correzione concettuale se il failure mode e' ricorrente",
            "lesson_candidate": LESSONS.get(mode, ""),
            "tags": ["claude_batch", f"fm:{mode}"],
        })

    out_jsonl = Path(args.out + ".jsonl")
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")

    # --- report ---
    total = sum(v for k, v in counts.items())
    lines = [f"# Claude batch review — {stamp}", "",
             f"Attempt non reviewati processati: {total}",
             f"- verified_success: {counts['verified_success']}",
             f"- verified_failure: {sum(v for k, v in counts.items() if k.startswith('fail:'))}",
             f"- runner_error: {counts['runner_error']}",
             f"- lasciati alla review umana: {counts['needs_human']}", "",
             "## Failure mode (per le correzioni concettuali)", ""]
    for mode, items in sorted(by_mode.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"### {mode} — {len(items)} casi")
        for aid, title, detail in items[:8]:
            lines.append(f"- `{aid}` {title[:60]} — {detail[:160]}")
        if len(items) > 8:
            lines.append(f"- … altri {len(items) - 8}")
        lines.append("")
    if needs_human:
        lines.append("## Da guardare a mano (nessuna decisione emessa)")
        for aid, title, why in needs_human:
            lines.append(f"- `{aid}` {title[:60]} — {why}")
    report = Path(args.out + ".report.md")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"decisioni: {out_jsonl} ({len(decisions)} righe)")
    print(f"report:    {report}")
    print(f"needs_human: {counts['needs_human']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
