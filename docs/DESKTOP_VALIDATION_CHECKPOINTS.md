# DEVIN Desktop validation checkpoints

Updated: 2026-07-15

This file is the practical test path before legacy cleanup. It mirrors the current product plan: desktop-first Tauri, WSL backend headless, safe local cleanup, and validation-ready Diagnostics.

## 1. Desktop launcher

Use the native Windows launcher, not the UNC WSL path:

```text
C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop.cmd
```

Expected:

- WSL FastAPI backend starts headless on `127.0.0.1:5000`;
- Tauri opens `/app` as desktop window;
- browser is fallback only;
- source remains in WSL `/home/tillo/devin_ai_ide`;
- Tauri/Node/Rust run from `%LOCALAPPDATA%\DEVIN\desktop-host`.

Logs:

```text
C:\Users\tillo\AppData\Local\DEVIN\logs\desktop-launch.log
C:\Users\tillo\AppData\Local\DEVIN\logs\tauri-dev.log
/home/tillo/devin_ai_ide/logs/fast_app_headless.log
```

## 2. Desktop close cleanup

When the Tauri main window closes it calls:

```text
POST /api/desktop/close_cleanup
```

Policy:

- kills only known local DEVIN model servers (`coder`, `planner`) on local ports;
- does not touch remote rig models;
- can be disabled with `DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS=0`.

Validation:

```bash
curl http://127.0.0.1:5000/api/desktop/readiness
curl -X POST http://127.0.0.1:5000/api/desktop/close_cleanup
```

## 3. Desktop UI polish

Current direction: minimal warm desktop shell inspired by the referenced Minimal Agent UI palette and calm/focused layout. The main `/app` should stay clean:

- left: projects/chats;
- center: DEVIN command center/chat/work stream;
- right: context/session only;
- diagnostics/training/memory/sandbox live in `/app/diagnostics`.

## 4. Agent/diff validation

From `/app`:

- use chat/composer for normal DEVIN interaction;
- use Diff preview for manual unified diff preview/apply with explicit confirmation;
- use Command Palette for safe navigation only, not shell execution.

From `/app/diagnostics#runs`:

- inspect run history/logs;
- verify structured events as they mature.

## 5. Training/Teacher validation

From `/app/diagnostics#training`:

- Seed benchmark;
- Run mini bench with confirmation;
- review attempts append-only;
- export Teacher packet/SFT;
- no automatic memory promotion from raw auto_success/auto_failure.

Teacher/Colibri/OpenAI/Claude review remains staged as external/optional review with explicit consent.

## 6. Sandbox validation

From `/app/diagnostics#sandbox`:

- prepare isolated project sandbox;
- prefer `link_venv` for lightweight tests;
- original project is not auto-mutated;
- promotion back to source must go through diff/review/test.

## Current known good validation

- backend readiness detected local model servers on ports 8000/8001;
- close cleanup killed `coder` and `planner` locally;
- backend remained alive on 5000;
- tests cover desktop readiness, close cleanup, Diagnostics wiring, training and sandbox UI scaffolding.

## 2026-07-15 launcher hardening

- Tauri requires `src-tauri/icons/icon.ico`; missing icon makes the first Windows build look stuck while it actually fails in stderr.
- The repo-side `scripts/DEVIN Desktop.cmd` now delegates to `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd` after preparing the host, so long-running Tauri dev sessions happen from a Windows-native path instead of the WSL UNC repo.
- The native launcher streams Tauri output live; if the first build compiles Rust crates, the console may stay busy for a few minutes, but progress/error text is visible.

## 2026-07-15 workspace responsiveness + diagnostics tabs

- Main workspace no longer polls runs/training on every refresh; those live in Diagnostics.
- Project switching uses `/api/project/overview?lite=true`, avoiding file/knowledge scans during sidebar selection.
- Diagnostics now behaves like tabbed pages and lazy-loads only the active section.
- External folders must be explicitly linked from the Workspace `Link` button before crawl/sandbox can use them; this preserves the path allowlist and avoids silent `403 Forbidden` confusion.
