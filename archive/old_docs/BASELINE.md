# DEVIN AI IDE Baseline

Baseline created as the first intentional git snapshot for the local DEVIN workspace.

## Product direction

DEVIN is the coding/development member of the local rig family. The target experience is a clean desktop app in the spirit of Codex/Claude Desktop: calm layout, visible project context, streaming chat, explicit tool/run state, and no fake completion.

The planned desktop shell is Tauri. The web UI remains the fast iteration surface for now; future Tauri work should wrap or replace it without losing the current project, memory, and eval contracts.

## Reasoning direction

DEVIN should become more like a careful senior coding agent than a snippet chatbot:

- understand project/chat context before acting;
- distinguish discussion from operational work;
- write real files when asked to build;
- run tests/quality gates before reporting success;
- save failures as failures, not as facts;
- promote shared memory only after human/eval confirmation.

The goal is not blind fine-tuning first. The current baseline favors eval-driven learning plus structured memory: successes, failures, hypotheses, corrections, user preferences, and project context are stored with status/evidence/promotion metadata.

## Memory policy

Runtime memories are local JSONL state and are intentionally ignored by git. Reproducible seed memories live in `scripts/seed_core_memory.py`.

Recall-safe statuses:

- `verified_success`
- `verified_failure`
- `human_confirmed`

Non-promoted statuses such as `pending_review`, `hypothesis`, `quarantine`, `syntax_only`, `inconclusive`, `revoked`, and `superseded` must not contaminate normal recall.

## Multi-agent rig direction

The wider rig has DEVIN, TEACHER, and HERMES as separate roles/models. Each role should keep private/local memory plus a promoted shared memory on the fourth disk. The shared memory should be curated by a librarian/teacher flow and should prefer verified successes, verified failures, human-confirmed preferences, and eval evidence.

## Baseline exclusions

The git baseline intentionally excludes:

- model weights (`*.gguf`, `*.safetensors`);
- virtualenvs and caches;
- local `.env` files and plaintext API key scratch files;
- generated logs, diagnostics, dumps, and workspaces;
- live local memory state (`devin/memory/local_memories.jsonl`).

## Validation command

Use this before major baseline changes:

```bash
venv/bin/python -m pytest -q --capture=no
```
