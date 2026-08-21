# DEVIN Desktop Launcher — next steps

The former WSL/local-backend launcher plan is superseded. The active desktop
architecture is now:

- Windows-native Tauri thin client;
- authenticated front door on the rig;
- FastAPI, workspaces, training jobs and models on the rig;
- lazy DEVIN activation and busy-aware idle release owned by the front door.

Implemented:

1. protected `%APPDATA%\DEVIN\desktop.json` configuration;
2. hidden token prompt and per-user ACL helper;
3. Rust-side validation, reachability check and authenticated navigation;
4. local connection/retry screen with no token in JavaScript;
5. Windows-native cached host and double-click launchers;
6. no automatic WSL, localhost backend, local model or browser fallback.

The next desktop slices are:

1. build a release Tauri executable/installer instead of using `tauri dev`;
2. add first-run connection setup inside the native shell;
3. expose front-door phase/ETA in the local connection screen without sharing
   the token with JavaScript;
4. add a signed update path after the installer is stable;
5. keep legacy local-backend scripts isolated for explicit development only.

The launcher must never apply patches, delete workspaces, start benchmarks or
stop a busy remote session automatically.
