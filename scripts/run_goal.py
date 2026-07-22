"""CLI per lanciare una Goal Mode reale sul rig (senza UI).

Costruisce un Goal, lo esegue col ruolo Scaffolder collegato all'orchestrator, e
stampa gli attempt in tempo reale + il risultato finale.

Esempi:

    # scaffold autonomo: costruisci finche' i test passano
    python scripts/run_goal.py --project workspace/demo \
        --objective "crea is_prime.py con is_prime(n) e test_is_prime.py, test verdi" \
        --mode scaffold --accept tests_pass --accept file_exists:is_prime.py

    # da file JSON (schema goal_v1)
    python scripts/run_goal.py --project workspace/demo --goal-file goal.json

Mini-DSL per --accept (ripetibile):
    tests_pass
    file_exists:PATH
    contains:PATH:TESTO
    absence:REGEX
    command:PROG ARG ARG        (exit 0 richiesto)

Nota: questo carica i modelli via Orchestrator. Va eseguito sul rig.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Permette `python scripts/run_goal.py` da root repo.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devin.core.goal_mode import (  # noqa: E402
    APPROVAL_AUTO,
    APPROVAL_MANUAL,
    MODE_MAINTENANCE,
    MODE_SCAFFOLD,
    Goal,
    GoalError,
    parse_acceptance,
)
from devin.core.goal_executors import (  # noqa: E402
    build_orchestrator_scaffold_runner,
    default_apply_fn,
    scaffolder_executor,
)
from devin.core.goal_runner import run_goal  # noqa: E402

_DEFAULT_CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "settings.json")


def build_goal(args: argparse.Namespace) -> Goal:
    if args.goal_file:
        data = json.loads(Path(args.goal_file).read_text(encoding="utf-8"))
        return Goal.from_dict(data)
    goal = Goal(
        objective=args.objective or "",
        acceptance=parse_acceptance(args.accept),
        mode=args.mode,
        approval_policy=args.approval,
        budget_steps=args.max_steps,
        budget_seconds=args.max_seconds,
    )
    return goal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lancia una Goal Mode reale (rig).")
    parser.add_argument("--project", required=True, help="cartella del progetto")
    parser.add_argument("--objective", help="obiettivo in linguaggio naturale")
    parser.add_argument("--accept", action="append", default=[], help="criterio (ripetibile), vedi mini-DSL")
    parser.add_argument("--goal-file", help="JSON schema goal_v1 (alternativa a --objective/--accept)")
    parser.add_argument("--mode", choices=[MODE_SCAFFOLD, MODE_MAINTENANCE], default=MODE_SCAFFOLD)
    parser.add_argument("--approval", choices=[APPROVAL_AUTO, APPROVAL_MANUAL], default=APPROVAL_AUTO)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-seconds", type=int, default=3600)
    parser.add_argument("--config", default=_DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    try:
        goal = build_goal(args)
        goal.validate()
    except (GoalError, ValueError, KeyError) as exc:
        print(f"[goal non valido] {exc}", file=sys.stderr)
        return 2

    project = str(Path(args.project).expanduser())
    Path(project).mkdir(parents=True, exist_ok=True)  # evita errori su cartella nuova
    print(f"[goal] {goal.objective!r}")
    print(f"[goal] mode={goal.mode} approval={goal.approval_policy} "
          f"checkpoint={goal.requires_checkpoint()} criteri={len(goal.acceptance)}")

    run_scaffold_fn = build_orchestrator_scaffold_runner(args.config)
    executor = scaffolder_executor(run_scaffold_fn, apply_fn=default_apply_fn())

    def on_attempt(attempt):
        sat = attempt.evaluation.get("satisfied")
        print(f"  step {attempt.index}: {attempt.status} [{attempt.strategy}] "
              f"satisfied={sat} — {attempt.detail}")

    result = run_goal(goal, project, executor, on_attempt=on_attempt)

    print(f"\n[risultato] {result.status}: {result.reason}")
    print(f"[risultato] step eseguiti: {len(result.attempts)}")
    for r in result.evaluation.get("results", []):
        mark = "OK " if r["passed"] else "-- "
        print(f"  {mark}{r['criterion']['type']} {r['criterion'].get('params', {})}: {r['detail']}")

    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
