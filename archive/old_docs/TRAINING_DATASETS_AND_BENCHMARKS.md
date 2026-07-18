# Training datasets, benchmarks, and eval roadmap

Regola base: eval ≠ training. I benchmark misurano; diventano training solo tramite fallimenti/correzioni validate e rerun.

## Ordine consigliato

### 1. Custom DEVIN packs
- DEVIN Mini Bench esteso
- Official API discipline: Steam, GitHub, OpenAI, Docker, Tauri
- Memory contamination tests
- WSL/Windows command safety
- GUI/app scaffolding tests

### 2. Small public evals
- HumanEval
- MBPP
- MultiPL-E
- IFEval

### 3. Medium practical coding
- APPS
- BigCodeBench
- DS-1000
- CodeXGLUE
- Aider Polyglot

### 4. Agentic / real software engineering
- SWE-bench Lite
- LiveCodeBench
- Terminal-Bench
- Defects4J / BugsInPy / QuixBugs
- SWE-bench Live / Pro quando Docker/runner sono solidi

### 5. Huge corpora
- The Stack / The Stack v2
- StarCoderData / BigCode
- CodeSearchNet
- Kaggle notebooks filtrati

## Pipeline corretta

benchmark case  
→ DEVIN attempt in sandbox  
→ deterministic validators: pytest/lint/allowlist  
→ Teacher / Colibrì / human review  
→ verified_success o verified_failure  
→ correction + memory lesson  
→ rerun  
→ solo dopo promozione in memoria/dataset

## Quality gate da implementare

- test discovery: `tests/`, `test_*.py`, `*_test.py`, `tests.py`
- comando WSL-safe: `venv/bin/python -m pytest -q --capture=no`
- endpoint/domain allowlist
- no plaintext secrets
- keyring quando richiesto
- no fake command output
- separare sempre:
  - runner_error
  - auto_success
  - auto_failure
  - verified_success
  - verified_failure

## Teacher/Colibrì packet

Ogni run deve esportare JSON/JSONL con:

- case_id
- benchmark_id
- prompt
- vincoli
- file/diff summary
- stdout/stderr test
- validator results
- runner status
- domanda per Teacher

Output atteso:

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



Priorità future
Estendere DEVIN Mini Bench a 30-50 casi.
Aggiungere pytest reale nel training runner.
Aggiungere validator per official API.
Aggiungere export teacher_packet.jsonl.
Integrare HumanEval/MBPP.
Integrare BigCodeBench/APPS.
Integrare LiveCodeBench/SWE-bench Lite.
Integrare Colibrì review batch.
Adapter opzionale OpenAI/Claude con redazione e consenso.