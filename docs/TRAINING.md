# DEVIN — Training, quality gate e benchmark

Doc canonico sul training. Fonde i vecchi `TRAINING_DATASETS_AND_BENCHMARKS.md`
e `TRAINING_MINI_BENCH_2026-07-15.md` (snapshot datato, ora storico).

**Regola base: eval ≠ training.** I benchmark misurano; diventano training solo
tramite fallimenti/correzioni validate e rerun. Niente promozione automatica di
materiale non verificato.

---

## Pipeline anti-contaminazione

```
benchmark case
→ DEVIN attempt in sandbox
→ validator deterministici: pytest reale / lint / tree-sitter / bandit / allowlist
→ Teacher / Colibrì / human review
→ verified_success | verified_failure
→ correction + memory lesson
→ rerun
→ solo dopo: promozione in memoria/dataset
```

### Status ladder (solo gli ultimi 3 sono recall-safe)
1. `runner_error` — problema d'infrastruttura, non qualità del modello.
2. `auto_success` — scaffold/codice generato e check meccanici passati (NON "soluzione corretta").
3. `auto_failure` — generazione fallita meccanicamente.
4. `verified_success` — test harness/Teacher/umano conferma il risultato.
5. `verified_failure` — conferma il fallimento e registra il perché.

Recall-safe: `verified_success`, `verified_failure`, `human_confirmed`.

---

## Quality gate (implementato)

Il gate multi-livello è attivo (declassa `auto_success→auto_failure`, non promuove mai):
- **pytest reale** prima di assegnare `auto_success` se esistono test. Discovery:
  `tests/`, `test_*.py`, `*_test.py`, `tests.py`, config pytest in `pyproject.toml`.
  Comando WSL-safe: `venv/bin/python -m pytest -q --capture=no` (con `PYTHONDONTWRITEBYTECODE=1`).
- **Gold test** canonici nostri iniettati nel sandbox (il modello non si corregge i compiti da solo; guardia `gold_tampered`).
- **tree-sitter** syntax critic multi-linguaggio (`devin/engine/syntax_critic.py`, fail-open sugli altri linguaggi).
- **bandit** security offline (`devin/engine/security_critic.py`, warning non bloccanti, salta i `test_gold_*`).
- **validator semantici per caso** (`devin/training/validators.py`): endpoint/domain allowlist, no secret in chiaro, no output comando finto, tests_pass.

### Esempio allowlist (caso "Official API only" — Steam)
- host obbligatorio: `api.steampowered.com`;
- endpoint ammessi: `ISteamUser/GetPlayerSummaries`, `IPlayerService/GetOwnedGames`, `ISteamUserStats/GetUserStatsForGame`;
- API key come query param; niente host/endpoint inventati; niente tracker terzi salvo caso esplicito.

---

## Teacher / Colibrì packet

Ogni run esporta JSON/JSONL con: `case_id`, `benchmark_id`, `prompt`, vincoli,
file/diff summary, stdout/stderr test, risultati validator, runner status, domanda per il Teacher.

Output atteso dal review:
```json
{
  "verdict": "verified_success|verified_failure|needs_human_review",
  "confidence": 0.0,
  "failure_type": "invented_endpoint|tests_fail|incomplete|unsafe|none",
  "evidence": ["..."],
  "correction": "...",
  "memory_lesson": "...",
  "promote_to_memory": false,
  "rerun_required": true
}
```

Review a più livelli: UI-TARS/gate locale → Teacher (rig) → **Colibrì** come
arbitro finale batch (GLM-5.2 744B, giudice indipendente e più forte del coder;
vedi nota capacità RAM in [PACKAGING-ROADMAP.md](PACKAGING-ROADMAP.md)).

---

## Dataset e benchmark — ordine consigliato

1. **Custom DEVIN packs** — Mini Bench esteso; Official API discipline (Steam, GitHub, OpenAI, Docker, Tauri); memory contamination tests; WSL/Windows command safety; GUI/app scaffolding.
2. **Small public evals** — HumanEval, MBPP, MultiPL-E, IFEval.
3. **Medium practical** — APPS, BigCodeBench, DS-1000, CodeXGLUE, Aider Polyglot.
4. **Agentic / real SWE** — SWE-bench Lite, LiveCodeBench, Terminal-Bench, Defects4J/BugsInPy/QuixBugs, SWE-bench Live/Pro (quando Docker/runner sono solidi).
5. **Huge corpora** — The Stack v2, StarCoderData/BigCode, CodeSearchNet, Kaggle notebooks filtrati.

**Baseline MBPP** col gate severo: **~53% auto_success** (52/98) — numero da battere post-LoRA.
Failure mode dominanti: `own_tests_failed`, `gold_assertion_failed`, `invented_endpoint`.

**Vincolo noto:** i batch MBPP lunghi muoiono a metà in locale = OOM killer WSL
(serializzazione coder↔planner) + ReadTimeout del coder locale. Cura = **rig in
ruolo devin** (no swap locale). Intanto: batch da 10 + resume (`skip_attempted`).

---

## Lezioni dal primo Mini Bench (2026-07-15, storico)

Il primo `ok 3` era ingannevole: tre `auto_success` meccanici, ma alla validazione
manuale **1 verified_success + 2 verified_failure**. Fix off-by-one lasciava un test
volutamente rosso come test normale (serve `xfail`/baseline separata); il Steam checker
usava un host inventato (`steamcommunity.ste.com`) invece di `api.steampowered.com`.
→ Da qui sono nati: pytest reale nel gate, gli allowlist validator, e la review queue.
Tutto ciò è **ora implementato**; questa sezione resta come memoria del perché.

---

## Priorità future
- Estendere DEVIN Mini Bench a 30–50 casi.
- Integrare HumanEval/MBPP → BigCodeBench/APPS → LiveCodeBench/SWE-bench Lite.
- Export `teacher_packet.jsonl` completo + review batch Colibrì.
- Adapter opzionale OpenAI/Claude con redazione e consenso.
