# DEVIN AI IDE - Tauri Desktop Shell

This is the first desktop wrapper for the Codex-like `/app` workspace.

## Current mode: brownfield wrapper

The desktop shell loads the existing FastAPI UI at:

- `http://127.0.0.1:5000/app`

FastAPI remains the source of truth for models, memory, runs, chat, diff preview/apply, and logs. This avoids prematurely bundling Python, CUDA/model launchers, WSL path handling, and GPU/runtime state into the desktop binary.

## Dev flow

Terminal 1, inside WSL `Ubuntu`:

```bash
cd /home/tillo/devin_ai_ide
venv/bin/python devin/ui/fast_app.py
```

Terminal 2, from an environment with Node, Rust, and Tauri CLI available:

```bash
cd /home/tillo/devin_ai_ide
npm install
npm run desktop:dev
```

The npm scripts call npx --no-install tauri so the repo-local Tauri CLI is used and npm will not fetch a different Tauri CLI implicitly. Run npm install from the same OS context that will launch Tauri.

On Windows, the same project can be opened through the WSL path, but the backend should still be the WSL `Ubuntu` DEVIN repo.


For clearer diagnostics than `npm run desktop:dev`, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-tauri-desktop.ps1 -Info
powershell -ExecutionPolicy Bypass -File scripts/run-tauri-desktop.ps1
```



## Windows vs WSL note

Use one OS context for npm dependencies. For the intended Windows desktop wrapper, run `npm install` and `npm run desktop:dev` from Windows PowerShell so Tauri installs the Windows CLI binary. Keep the FastAPI backend in WSL `Ubuntu`.

## Why no sidecar yet?

Tauri sidecars are the correct long-term direction for starting the backend from the desktop app, but they require target-specific binary naming and shell permissions. The next phase should add a small backend launcher sidecar once the Windows build environment is stable.

## Next phase

1. Add a Windows-friendly launcher sidecar that starts `wsl.exe -d Ubuntu --cd /home/tillo/devin_ai_ide --exec venv/bin/python devin/ui/fast_app.py`.
2. Add Tauri shell plugin permissions for the sidecar.
3. Add health probing before showing the workspace, so the app can display "backend starting" instead of a blank localhost page.
