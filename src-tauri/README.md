# DEVIN AI IDE - Tauri Desktop Client

The Windows app is a thin native client for DEVIN's trusted-LAN front door
on the rig. FastAPI, workspaces, training jobs and model lifecycle stay on the
rig; the desktop process never starts a local Python backend or a local model.

At launch the bundled bootstrap asks Rust to:

1. read `%APPDATA%\DEVIN\desktop.json`;
2. validate the front-door URL;
3. verify that the configured host is reachable;
4. install an in-memory native transport for the local cockpit.

The WebView never navigates to the rig UI. Rust performs HTTP against the
trusted LAN endpoint and streams response metadata/chunks to the local frontend
through Tauri Channels. Closing the window does not stop a remote run; the front door releases
an idle DEVIN session according to its own policy.

## Configure Windows

Run the interactive helper once. The resulting directory ACL allows only the
current user and `SYSTEM`.

```powershell
npm run desktop:configure
```

The resulting file has this shape:

```json
{
  "schema": "devin_desktop_frontdoor_v1",
  "frontdoor_url": "http://rig-address:5000"
}
```

For temporary development sessions, `DEVIN_DESKTOP_CONFIG` can point to a
different file. `DEVIN_FRONTDOOR_URL` overrides the JSON value.

## Build and launch on Windows

Use one OS context for Node and Rust dependencies. From Windows PowerShell:

```powershell
npm install
python scripts/build_frontend_bundle.py
npm run desktop:dev
```

The installed development host keeps its Rust `target` cache across launches:

```powershell
npm run desktop:prepare-host
npm run desktop:windows-host
```

No WSL checkout, local FastAPI process or backend sidecar is required by the
desktop client.

## Direct Windows workspaces

The native picker authorizes one folder and stores its path only in the
user/SYSTEM-protected local registry. The rig receives an opaque bridge id;
there is no full-folder upload and no total workspace-size limit. Tauri owns
filtered tree/read/retrieval operations. Operational chat requests can produce
a bounded file plan, shown for confirmation before conflict-checked atomic
writes. Existing files require their observed SHA-256 and every apply creates a
local recovery record. Arbitrary local shell execution is not part of this
contract.
