# P6 — Stato dei 10 debiti di training/eval (verifica 2026-07-23)

Verdetto: **tutti e 10 i debiti P6 elencati nella roadmap (CONTINUITY 07-20 /
NUOVA IDEA) sono GIA' implementati E coperti da test che passano.** Sono stati
chiusi nel giro di hardening del **2026-07-18** (+ sidecar ordering export);
la roadmap li elencava ancora come aperti perche' il doc non era stato
aggiornato. Nessun codice nuovo necessario. Suite completa: 538 passed, 5 skipped.

| # | Debito | Dove e' gestito | Test che lo copre | Stato |
|---|--------|-----------------|-------------------|:-----:|
| 1 | Gold test aggirabili via `conftest.py`/`collect_ignore` (**security-critical**) | `routers/training.py::_verify_gold_tests_executed` (raccolta da directory; exit 5 = bypass) + guardia byte `gold_tampered` | `test_training_runner_declass.py::test_runner_gold_conftest_collect_ignore_auto_failure`, `..._autouse_skip_...`, `..._gold_not_executed_...` | ✅ |
| 2 | Detector mock troppo permissivo sulla parola `mock` | `training/validators.py` `MOCK_IMPORT_RE`/`MOCK_USAGE_RE` (solo import/uso reale in `.py`) | `test_training_quality_gate.py::test_validator_mock_word_in_comment_does_not_pass`, `..._in_readme_...`, `..._real_unittest_mock_usage_passes` | ✅ |
| 3 | Status tentativi da validare rigidamente | `training/store.py` `ATTEMPT_STATUSES`/`REVIEW_STATUSES`; `add_attempt`/`add_review` sollevano su status ignoto | `test_training_quality_gate.py::test_add_attempt_rejects_unknown_status`, `..._accepts_all_canonical_statuses`; `test_..._invalid_status_error_convention` | ✅ |
| 4 | `runner_error` non deve contare come tentativo (skip resume) | `routers/training.py` skip_attempted: `attempted_statuses` esclude `INFRA_STATUSES` | `test_training_runner_declass.py::test_skip_attempted_retries_runner_error_cases` | ✅ |
| 5 | Crash del validator non deve diventare `auto_success` (**security-critical**) | `routers/training.py` (fix A): crash -> `validation=unknown` + `validator_crash` -> declassa `auto_success->auto_failure` (fail-closed) | `test_training_runner_declass.py::test_runner_validator_crash_fails_closed` | ✅ |
| 6 | Review `pending_review` non deve rimuovere l'attempt dalla coda | `store.py::review_queue` usa `VERDICT_REVIEW_STATUSES` (pending_review escluso; riapre dopo verdetto) | `test_training_quality_gate.py::test_review_queue_keeps_attempt_with_pending_review_review`, `..._verdict_review_clears_attempt` | ✅ |
| 7 | Ordinamento export deterministico | `store.py::_write_export_jsonl` (sidecar `.meta.json` con `created_at_ns`+sha256) + `list_exports` ordina per `(logical_order_ns, filename)` | `test_training_endpoints_exports.py::test_list_exports_uses_logical_order_when_filesystem_mtimes_tie` | ✅ |
| 8 | Evidenza che i gold test siano stati davvero raccolti/eseguiti | `_verify_gold_tests_executed` (raccolta reale, non solo byte intatti) | `test_runner_gold_not_executed_auto_failure`, `..._real_verification_clean_auto_success` | ✅ |
| 9 | Quality gate indipendente dal codice del modello | gold test NOSTRI iniettati (`training/benchmarks.py`, `adapters.py`) + guardia `gold_tampered` + validator solo declassano, mai promuovono | `test_runner_gold_tampered_auto_failure`; validators docstring "STRICTER only" | ✅ |
| 10 | Dataset SFT solo da correzioni/verifiche approvate, con provenance | `store.py::export_sft_dataset` costruisce righe SOLO da `corrections` (fix umani) con `metadata` (case_id, attempt_id, correction_id, source, tags) | `test_training_endpoints_exports.py::test_export_sft_dataset_messages_shape` | ✅ |

## Note
- I punti **1** e **5** (i security-critical segnati dall'owner) sono chiusi e
  hanno test fail-closed dedicati.
- Debito 10: l'SFT esce solo dalle correzioni umane (che *sono* l'approvazione) e
  porta provenance. Enhancement opzionale (non un buco): gate ulteriore che
  richieda anche una review `verified_*` sull'attempt della correzione.
- La memoria anti-contaminazione ha copertura a parte: `test_memory_write_path.py`
  (normalizzazione status a review-only, quarantine non promuovibile, provenance).

## Cosa e' invece ANCORA da fare in P6 (feature, non debiti)
Il **Federated Evidence Council** (review cieca multi-modello, GLM-Colibri
adjudicator, adapter esterni OpenAI/Claude con redazione/consenso) e' *feature*
non ancora costruita — richiede il rig e decisioni owner. I 10 debiti qui sopra
erano la parte di correttezza/sicurezza della pipeline esistente: quelli sono
chiusi. Il Council resta il prossimo blocco P6 vero, quando ci arriviamo.
