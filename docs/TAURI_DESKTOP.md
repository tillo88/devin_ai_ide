# DEVIN Tauri Desktop

## Current architecture

DEVIN Desktop is a Windows-native thin client for the authenticated front door
on the rig. The PC owns the Tauri window; the rig owns FastAPI, workspaces,
training jobs, runs and the DEVIN model slot.

Normal desktop startup does not require WSL, a local Python process, a backend
sidecar or local models. Closing the window also does not stop remote work: the
front door releases the DEVIN session only after its idle/busy checks pass.

The local `frontendDist` bundle is intentionally small. It provides first-run
onboarding, connection/retry/settings views and invokes protected Rust
commands. After authentication it is replaced by the same-origin `/app` served
through the front door.

The cockpit roadmap and its no-NVML status contract are recorded in
`DEVIN_DESKTOP_COCKPIT_ROADMAP_2026-08-22.md`.

## Configuration

On first launch the app asks for the front-door root URL and token. The native
form can test TCP reachability without sending the token or activating DEVIN,
then saves through Rust. From an error screen, **Impostazioni** reopens the same
form; an empty token field preserves the existing protected token.

The PowerShell configurator remains an administrative fallback:

```powershell
npm run desktop:configure
```

It prompts for the URL and a hidden token, then writes:

```text
%APPDATA%\DEVIN\desktop.json
```

Both paths reduce the directory ACL to the current Windows user and `SYSTEM`.
Rust uses a same-directory temporary file, flushes it, and atomically replaces
the configuration. The file schema is:

```json
{
  "schema": "devin_desktop_frontdoor_v1",
  "frontdoor_url": "http://rig-address:5000",
  "access_token": "frontdoor-secret"
}
```

Rust validates scheme, host, path and token length. Stored credentials are
never returned to JavaScript. Rust builds `/app?token=...` with URL encoding
and navigates the webview; the front door converts the query token into an
`HttpOnly` cookie and redirects to a clean `/app` URL.

For development, `DEVIN_DESKTOP_CONFIG` can select another JSON file;
`DEVIN_FRONTDOOR_URL` and `DEVIN_FRONTDOOR_TOKEN` override individual fields.

## Windows-native host

Prepare the cached host once from a Windows checkout:

```powershell
python scripts/build_frontend_bundle.py
npm run desktop:prepare-host
```

The preferred launcher is then:

```text
C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop.cmd
```

The generated launcher is installation-stable: it invokes the scripts already
under `%LOCALAPPDATA%\DEVIN\desktop-host`, so it does not retain a WSL, USB or
source-checkout path. Every source checkout and the generated host share one
regenerable Cargo cache in `%LOCALAPPDATA%\DEVIN\build-cache\cargo-target`.

Use the silent launcher for the normal GUI-only experience:

```text
C:\Users\tillo\AppData\Local\DEVIN\DEVIN Desktop (silenzioso).vbs
```

Logs remain in `%LOCALAPPDATA%\DEVIN\logs`.

## Development and validation

Required Windows tools are Node/npm, Rust stable, Visual Studio C++ Build Tools
and WebView2. Install dependencies and build the bootstrap before Tauri:

```powershell
npm install
python scripts/build_frontend_bundle.py
npm run desktop:dev
```

Useful non-GUI checks:

```powershell
npm run desktop:preflight
npm run desktop:windows-info
npm run desktop:test
```

The older WSL/local-backend scripts remain available only for explicit legacy
development workflows. They are not called by `desktop:windows-host` or by the
generated DEVIN Desktop launchers.

## Development cache policy

`src-tauri/target` is a regenerable Cargo build cache, not application data.
The dev profile disables Rust debug symbols and incremental objects because
the previous defaults grew native caches to several GB. The supported scripts
export `CARGO_TARGET_DIR` to one location outside the repository.

```powershell
npm run desktop:cache
npm run desktop:cache:clean-legacy
npm run desktop:cache:clean
```

The cleanup script accepts only exact, resolved DEVIN cache paths. It refuses
to delete a repository `target` if Git reports tracked files, and never touches
the checkout, `%APPDATA%\DEVIN` configuration or logs.

## Release build

Build both supported Windows installers from the native host:

```powershell
npm run desktop:build
```

The release command regenerates the local bootstrap bundle, builds NSIS and
MSI packages, and copies only the installers plus `build-manifest.json` to
`dist\windows`. The manifest records size and SHA-256 for transport integrity;
it never reads or copies `desktop.json`. Python, WSL, backend binaries, GGUF
models and runtime data are not Tauri resources.
