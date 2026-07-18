# Tauri Desktop Roadmap for DEVIN

## Status

DEVIN now has a working Tauri 2 desktop shell through a Windows-native host. The implementation is intentionally brownfield: Tauri opens the FastAPI `/app` surface at `http://127.0.0.1:5000/app`, while the backend runs headless inside WSL `Ubuntu`.

This keeps the risk low:

- no Python/CUDA/model launcher packaging yet;
- no WSL sidecar permissions yet;
- all current browser-tested APIs remain unchanged;
- `/`, `/chat`, and `/history` still exist as fallback surfaces.

## Files

- `package.json` - Node/Tauri CLI scripts.
- `src-tauri/tauri.conf.json` - Tauri 2 config for the desktop window.
- `src-tauri/Cargo.toml` - Rust crate for the Tauri shell.
- `src-tauri/src/main.rs` - Tauri app entrypoint, including close cleanup hook.
- `src-tauri/capabilities/default.json` - minimal `core:default` capability.
- `scripts/devin-tauri-dev.ps1` - Windows helper to start WSL backend and then run Tauri dev.
- `scripts/prepare-windows-desktop-host.ps1` - syncs the native Windows desktop host into `%LOCALAPPDATA%\DEVIN\desktop-host`.
- `scripts/launch-windows-desktop-host.ps1` - launches Tauri from the native Windows host and streams build output.
- `scripts/DEVIN Desktop.cmd` - repo-side delegating launcher.
- `src-tauri/icons/icon.ico` - required Windows icon for Tauri build resources.

## Run manually

Start backend in WSL `Ubuntu`:

```bash
cd /home/tillo/devin_ai_ide
venv/bin/python devin/ui/fast_app.py
```

Then run Tauri from a shell with Node/Rust available:

```bash
npm install
npm run desktop:dev
```

The npm scripts call npx --no-install tauri so the repo-local Tauri CLI is used and npm will not fetch a different Tauri CLI implicitly. Run npm install from the same OS context that will launch Tauri.

## Windows helper

From PowerShell at the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/devin-tauri-dev.ps1
```

The helper starts the backend through WSL and waits for `/app` before launching Tauri dev.


## Windows vs WSL toolchains

For the Windows desktop app, launch Tauri from Windows PowerShell. That same Windows shell must see Windows Node/npm and Windows Rust/Cargo.

Tauri CLI uses platform-specific optional npm packages. If `npm install` is run from WSL/Linux, `node_modules` can contain `@tauri-apps/cli-linux-x64-gnu`; Windows PowerShell instead needs `@tauri-apps/cli-win32-x64-msvc` and the `.cmd` shim under `node_modules/.bin`.

Rule of thumb: run `npm install` from the same OS context that will run `npm run desktop:dev`. For our DEVIN setup, that should usually be Windows PowerShell for the desktop shell, while the FastAPI backend keeps running inside WSL `Ubuntu`.


## Direct launcher for debugging

If `npm run desktop:dev` exits with a nearly empty npm log, use the direct launcher. It bypasses npm scripts, prints the resolved Windows tool paths, runs the same preflight, and then calls the repo-local Tauri CLI through `npx.cmd`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-tauri-desktop.ps1
```

For a non-GUI diagnostic run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-tauri-desktop.ps1 -Info
```

## Future sidecar plan

The next desktop phase should replace the manual backend start with a sidecar/launcher. Tauri's official sidecar documentation says external binaries are configured through `bundle.externalBin`; relative paths are resolved from `src-tauri/tauri.conf.json`, and platform-specific binaries need the target-triple suffix. It also requires shell permissions such as `shell:allow-spawn`/`shell:allow-execute` when launched from JavaScript.

For DEVIN, the sidecar should probably be a small Windows launcher that calls:

```powershell
wsl.exe -d Ubuntu --cd /home/tillo/devin_ai_ide --exec bash -lc "venv/bin/python devin/ui/fast_app.py"
```

That keeps model paths, venv, CUDA/WSL behavior, memory files, and workspace state inside the existing WSL environment.

## Preflight

Before running the desktop shell on Windows, check the host toolchain:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check-tauri-env.ps1
```

The script checks Node/npm, Rust/Cargo, Tauri CLI availability through `npx tauri --version`, and whether `http://127.0.0.1:5000/app` is reachable.

## Headless WSL backend launcher

`scripts/devin-tauri-dev.ps1` can start the FastAPI backend headless inside WSL with `nohup`, writing logs to `logs/fast_app_headless.log`, waiting for `/api/health`, then launching Tauri. This keeps the backend shell hidden so Tauri is the only visible interface during normal desktop use.

## First clickable launcher

`scripts/DEVIN Desktop.cmd` is the first Windows double-click launcher. It enters the repo with `pushd`, starts the WSL FastAPI backend through the headless launcher, then starts the Tauri dev shell. This is still a development launcher, not the final installer, but it is the intended first desktop GUI path for manual testing.

## Windows-native desktop host

Do not run Tauri directly from `\wsl.localhost` for day-to-day testing. Windows can prompt on executables launched from UNC paths, and npm-generated `.cmd` shims are unreliable there. The recommended first-test path is `scripts/DEVIN Desktop.cmd`, which syncs a tiny Tauri host to `%LOCALAPPDATA%\DEVIN\desktop-host`, starts the WSL backend headless, and runs Tauri from the native Windows path.


After preparing the host, use `%LOCALAPPDATA%\DEVIN\DEVIN Desktop.cmd` as the preferred launcher. It lives on a native Windows path and calls the synced host scripts with the WSL source repo passed explicitly.


## Close cleanup policy

When the main Tauri window closes, the desktop shell sends a best-effort local POST to `/api/desktop/close_cleanup`. The backend only shuts down model processes tracked by `LocalModelLauncher`; remote rig models are not controlled by this hook. Set `DEVIN_DESKTOP_CLOSE_KILLS_LOCAL_MODELS=0` to disable this behavior.


## Current preferred launcher

Use the native launcher after host preparation:

```text
C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop.cmd
```

If launched from the repo-side `scripts/DEVIN Desktop.cmd`, it prepares the host and delegates to the native launcher. The first Rust/Tauri build can take a few minutes; output is streamed live so compilation is visible.

The Windows Tauri build requires `src-tauri/icons/icon.ico`. If this icon is missing, Tauri fails during Windows resource generation.
