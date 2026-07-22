# DEVIN AI IDE — Indice della documentazione

Mappa di tutti i doc del progetto. Punto d'ingresso: **[../README_DEVIN_AI_IDE.md](../README_DEVIN_AI_IDE.md)**.
Fonte di verità operativa giorno-per-giorno: i file `CONTINUITY_*` (log datati).

## Panoramica e stato
- **[../README_DEVIN_AI_IDE.md](../README_DEVIN_AI_IDE.md)** — cos'è, hardware, struttura, avvio, stato.
- **[CONTINUITY_2026-07-18.md](CONTINUITY_2026-07-18.md)** — reliability hardening: resume esplicito, no-progress guard, cache JSON, filtri memoria, evidence tier (più recente).
- **[CONTINUITY_2026-07-15.md](CONTINUITY_2026-07-15.md)** — log operativo datato (storico, non modificare a ritroso).
- **[../AGENTS.md](../AGENTS.md)** — istruzioni per gli agenti che lavorano nel repo.

## Architettura e design
- **[CODEX_LIKE_MENTAL_MODEL.md](CODEX_LIKE_MENTAL_MODEL.md)** — modello mentale stile Codex/Claude: come ragiona l'app.
- **[CONTEXT_STEWARD_PLAN.md](CONTEXT_STEWARD_PLAN.md)** — audit + piano stratificato del Context Steward (memoria operativa di sessione, P4/P5). CS0 (nucleo deterministico) implementato in `devin/core/context_steward.py`.
- **[PROJECT_SANDBOX.md](PROJECT_SANDBOX.md)** — sandbox trasparente dei progetti (copia→diff→applica).
- **[API_TAURI_SPEC.md](API_TAURI_SPEC.md)** — spec dell'API tra shell Tauri e backend FastAPI.

## Training e valutazione
- **[TRAINING.md](TRAINING.md)** — pipeline anti-contaminazione, quality gate, dataset/benchmark, teacher/Colibrì packet. **Doc canonico** (fonde i vecchi TRAINING_DATASETS_AND_BENCHMARKS + TRAINING_MINI_BENCH_2026-07-15).

## Packaging / distribuzione
- **[PACKAGING-ROADMAP.md](PACKAGING-ROADMAP.md)** — verso l'eseguibile Windows: profilo RIG vs LOCALE, sidecar PyInstaller, wizard onboarding, installer .msi/.exe.

## Storico / archiviati
- `TRAINING_MINI_BENCH_2026-07-15.md` — superato da [TRAINING.md](TRAINING.md) (le lezioni sono state incorporate; il gate descritto è ormai implementato). Da spostare in `archive/old_docs/`.
- `archive/old_docs/` — doc di fasi precedenti (BASELINE, HARDENING_STATUS, AUDIT-TODO, README_DEVIN_FASE2, APPLY_PATCHES).
