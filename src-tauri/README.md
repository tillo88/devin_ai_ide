# DEVIN AI IDE - Tauri Desktop Client

The Windows app is a thin native client for DEVIN's authenticated front door
on the rig. FastAPI, workspaces, training jobs and model lifecycle stay on the
rig; the desktop process never starts a local Python backend or a local model.

At launch the bundled bootstrap asks Rust to:

1. read `%APPDATA%\DEVIN\desktop.json`;
2. validate the front-door URL and token without exposing the token to JS;
3. verify that the configured host is reachable;
4. navigate the webview to `/app?token=...`.

The front door converts the one-time query into an `HttpOnly` cookie and
redirects to a clean `/app` URL. Closing the window does not stop a remote run;
the front door releases an idle DEVIN session according to its own policy.

## Configure Windows

Run the interactive helper once. The token prompt is hidden and the resulting
directory ACL allows only the current user and `SYSTEM`.

```powershell
npm run desktop:configure
```

The resulting file has this shape:

```json
{
  "schema": "devin_desktop_frontdoor_v1",
  "frontdoor_url": "http://rig-address:5000",
  "access_token": "replace-with-the-frontdoor-token"
}
```

For temporary development sessions, `DEVIN_DESKTOP_CONFIG` can point to a
different file. `DEVIN_FRONTDOOR_URL` and `DEVIN_FRONTDOOR_TOKEN` override the
corresponding JSON values.

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
