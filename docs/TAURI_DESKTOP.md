# DEVIN Tauri Desktop

## Current architecture

DEVIN Desktop is a Windows-native thin client for the authenticated front door
on the rig. The PC owns the Tauri window; the rig owns FastAPI, workspaces,
training jobs, runs and the DEVIN model slot.

Normal desktop startup does not require WSL, a local Python process, a backend
sidecar or local models. Closing the window also does not stop remote work: the
front door releases the DEVIN session only after its idle/busy checks pass.

The local `frontendDist` bundle is intentionally small. It shows connection
status, invokes the protected Rust command `connect_frontdoor`, and is replaced
by the same-origin `/app` served through the front door after authentication.

The cockpit roadmap and its no-NVML status contract are recorded in
`DEVIN_DESKTOP_COCKPIT_ROADMAP_2026-08-22.md`.

## Configuration

Run the interactive helper from Windows PowerShell:

```powershell
npm run desktop:configure
```

It prompts for the URL and a hidden token, then writes:

```text
%APPDATA%\DEVIN\desktop.json
```

The directory ACL is reduced to the current Windows user and `SYSTEM`. The file
schema is:

```json
{
  "schema": "devin_desktop_frontdoor_v1",
  "frontdoor_url": "http://rig-address:5000",
  "access_token": "frontdoor-secret"
}
```

Rust validates scheme, host, path and token length. It probes only TCP
reachability, builds `/app?token=...` with URL encoding and navigates the
webview without returning the token to JavaScript. The front door converts the
query token into an `HttpOnly` cookie and redirects to a clean `/app` URL.

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
source-checkout path. The host preserves `src-tauri\target` between runs so
compiled dependencies remain reusable.

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
cargo test --manifest-path src-tauri/Cargo.toml
```

The older WSL/local-backend scripts remain available only for explicit legacy
development workflows. They are not called by `desktop:windows-host` or by the
generated DEVIN Desktop launchers.

## Development cache policy

`src-tauri/target` is a regenerable Cargo build cache, not application data.
The dev profile disables Rust debug symbols and incremental objects because
the previous defaults grew the native host cache to about 7 GB. Cache cleanup
must target only the resolved native host `src-tauri\target`; it must never
delete the source checkout, `%APPDATA%\DEVIN` configuration or DEVIN logs.
