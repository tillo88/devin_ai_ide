# DEVIN AI IDE — continuity brief

**Updated:** 2026-09-29

**Primary development workspace:** `F:\devin_ai_ide` on Windows.

**Production runtime:** `/opt/devin-ai-ide-frontend` on the rig.
Start from [`CURRENT.md`](CURRENT.md), then see `AGENTS.md` §1.

DEVIN AI IDE is a local-first coding-agent workspace: FastAPI backend, Codex-like `/app` prototype UI, local/rig model routing, safe memory, project-aware chat, scaffold/maintenance runs, and an early training/eval loop.

**Product direction:** the web UI is a development/prototyping surface. The intended product is a desktop app like Codex/Claude Desktop, using Tauri as the shell and the local backend/model stack behind it.

If you are resuming this project, start here:

1. Read [`CURRENT.md`](CURRENT.md).
2. Read [`docs/LOCAL_WINDOWS_WORKSPACE_20260929.md`](docs/LOCAL_WINDOWS_WORKSPACE_20260929.md).
3. Read [`docs/CURRENT_ARCHITECTURE.md`](docs/CURRENT_ARCHITECTURE.md).
4. Use dated continuity logs only as historical evidence.

## Quick start

For normal use, launch the Windows thin client. In the rig profile the
always-on frontdoor owns on-demand backend/model activation; do not launch a
second backend beside it.

Open:

- Web workspace: <http://127.0.0.1:5000/app>
- Legacy dashboard: <http://127.0.0.1:5000/>
- Legacy chat: <http://127.0.0.1:5000/chat>

## Verify before changing

```bash
/home/tillo/devin_ai_ide/.venv-rig/bin/python3 -m pytest -q
```

The exact count evolves; the required baseline is zero failures in the current
checkout. Linux-only integration tests may run in an isolated workspace on the
rig without touching live services or models.

## Current safety rule

Training/eval output is not promoted directly into good memory. Automatic benchmark results are stored as `auto_success`, `auto_failure`, or `runner_error`; a human/Teacher validation step must promote or correct them.

## Latest mini bench report

The historical mini-bench findings are incorporated into
[`docs/TRAINING.md`](docs/TRAINING.md); the original receipt is archived under
`archive/old_docs/`.

## Teacher / Colibrì / external review direction

The target rig roles remain DEVIN, TEACHER, and HERMES. Colibrì/GLM-5.2 is planned as an optional offline deep-review component for batch benchmark artifacts, not a always-on role. Optional OpenAI/Claude review adapters may be added later for final checks, with explicit approval and redaction/privacy controls. See [`ROADMAP_DEVIN_UI.md`](ROADMAP_DEVIN_UI.md#fase-6-teacher--colibrì-batch-review-pipeline).




## Dataset and benchmark roadmap

See [Training](docs/TRAINING.md) for the current staged benchmark plan,
Teacher/reviewer boundaries and anti-contamination rules.
## Repository cleanup policy

The active root is kept intentionally small: current README/roadmap, package/requirements, launcher/utilities, core source, tests, scripts, Tauri shell, docs, and runtime folders. Historical planning files and generated diagnostics are archived under `archive/` instead of being deleted. Local scratch secrets live under `archive/private_local/`, which is ignored by git.

Policy: archive first, delete only after a separate explicit review.


## Desktop validation

For the current desktop-first test path, follow
`docs/DESKTOP_VALIDATION_CHECKPOINTS.md`. It covers the Windows-native Tauri
client against the rig frontdoor, lifecycle, Diagnostics, linked projects and
safe validation. WSL is not part of the current architecture.


## Current desktop launcher

Preferred launcher after the Windows host has been prepared:

```text
C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop.cmd
```

The repo-side `scripts/DEVIN Desktop.cmd` is only a delegating helper. The desktop app runs from a native Windows host in `%LOCALAPPDATA%\DEVIN\desktop-host` and is a thin client: it talks to the frontdoor on the rig at port 5000. The FastAPI backend runs **on the rig**, from `/opt/devin-ai-ide-frontend`, not in WSL — there is no WSL on the Windows machine. See `AGENTS.md` §1-3.

The executable under `%LOCALAPPDATA%\DEVIN AI IDE` is the installed release and
contains the frontend bundled when that release was built. During UI iteration,
regenerate `src-tauri/frontend`, sync `desktop-host`, and use the launcher above;
reopening an old installed EXE does not load new source files.

The main Workspace is intentionally light: project switching uses lite project overview, and Runs/Training/Memory/Knowledge/Sandbox/Settings live in Diagnostics tabs. External folders such as ForgeStudio must be linked with the Workspace `Link` button before crawl/sandbox can access them.
