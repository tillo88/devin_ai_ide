# DEVIN AI IDE — continuity brief

**Updated:** 2026-07-15  
**Primary workspace:** `/home/tillo/devin_ai_ide` on WSL distro `Ubuntu`.

DEVIN AI IDE is a local-first coding-agent workspace: FastAPI backend, Codex-like `/app` prototype UI, local/rig model routing, safe memory, project-aware chat, scaffold/maintenance runs, and an early training/eval loop.

**Product direction:** the web UI is a development/prototyping surface. The intended product is a desktop app like Codex/Claude Desktop, using Tauri as the shell and the local backend/model stack behind it.

If you are resuming this project, start here:

1. Read [`docs/CONTINUITY_2026-07-15.md`](docs/CONTINUITY_2026-07-15.md).
2. Then read [`ROADMAP_DEVIN_UI.md`](ROADMAP_DEVIN_UI.md) for UI/Tauri direction.
3. Use [`README_DEVIN_AI_IDE.md`](README_DEVIN_AI_IDE.md) for the broader architecture and historical notes.

## Quick start

```bash
cd ~/devin_ai_ide
source venv/bin/activate
venv/bin/python devin/ui/fast_app.py
```

Open:

- Web workspace: <http://127.0.0.1:5000/app>
- Legacy dashboard: <http://127.0.0.1:5000/>
- Legacy chat: <http://127.0.0.1:5000/chat>

## Verify before changing

```bash
venv/bin/python -m pytest -q --capture=no
```

Expected current baseline: `72 passed, 1 skipped`.

## Current safety rule

Training/eval output is not promoted directly into good memory. Automatic benchmark results are stored as `auto_success`, `auto_failure`, or `runner_error`; a human/Teacher validation step must promote or correct them.

## Latest mini bench report

See [`docs/TRAINING_MINI_BENCH_2026-07-15.md`](docs/TRAINING_MINI_BENCH_2026-07-15.md): first real mini bench run, manual validation, lessons, and next engineering steps.

## Teacher / Colibrì / external review direction

The target rig roles remain DEVIN, TEACHER, and HERMES. Colibrì/GLM-5.2 is planned as an optional offline deep-review component for batch benchmark artifacts, not a always-on role. Optional OpenAI/Claude review adapters may be added later for final checks, with explicit approval and redaction/privacy controls. See [`ROADMAP_DEVIN_UI.md`](ROADMAP_DEVIN_UI.md#fase-6-teacher--colibrì-batch-review-pipeline).




## Dataset and benchmark roadmap

See [Training datasets and benchmarks](docs/TRAINING_DATASETS_AND_BENCHMARKS.md) for the staged plan: custom DEVIN packs, HumanEval/MBPP, BigCodeBench/APPS, SWE-bench, Terminal-Bench, huge corpora, Teacher/Colibrì review, and anti-contamination rules.
## Repository cleanup policy

The active root is kept intentionally small: current README/roadmap, package/requirements, launcher/utilities, core source, tests, scripts, Tauri shell, docs, and runtime folders. Historical planning files and generated diagnostics are archived under `archive/` instead of being deleted. Local scratch secrets live under `archive/private_local/`, which is ignored by git.

Policy: archive first, delete only after a separate explicit review.


## Desktop validation

For the current desktop-first test path, follow `docs/DESKTOP_VALIDATION_CHECKPOINTS.md`. It covers the Windows-native Tauri launcher, WSL headless backend, local model cleanup, Diagnostics tabs, linked external project folders, training review, crawl/knowledge, and sandbox validation.


## Current desktop launcher

Preferred launcher after the Windows host has been prepared:

```text
C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop.cmd
```

The repo-side `scripts/DEVIN Desktop.cmd` is only a delegating helper. The desktop app runs from a native Windows host in `%LOCALAPPDATA%\DEVIN\desktop-host`, while the FastAPI backend remains headless in WSL `Ubuntu`.

The main Workspace is intentionally light: project switching uses lite project overview, and Runs/Training/Memory/Knowledge/Sandbox/Settings live in Diagnostics tabs. External folders such as ForgeStudio must be linked with the Workspace `Link` button before crawl/sandbox can access them.
