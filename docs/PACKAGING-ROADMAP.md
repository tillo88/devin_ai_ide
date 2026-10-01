# Roadmap — DEVIN come app Windows installabile

Goal: double-click to open the DEVIN workspace while all backend, training and
model work remains on the rig.

## Architecture decision

The supported profile is rig-first and thin-client-only:

- Tauri/WebView2 runs the full local frontend on Windows;
- the trusted-LAN front door and FastAPI run on the rig;
- selected Windows workspaces use bounded, filtered snapshots and
  conflict-checked apply through the native bridge;
- the front door activates DEVIN lazily and returns to Clippy only when idle;
- no Python, llama-server, GGUF model or backend sidecar is bundled on Windows.

A future local profile is a separate product decision, not an automatic
fallback. The desktop must fail visibly if its configured rig is unavailable.

## Phase 1 — thin client foundation (complete)

- [x] Bundled connection/retry screen.
- [x] Rust-side URL validation and front-door reachability check.
- [x] Bounded native HTTP transport to the trusted rig LAN; the local UI
      receives response chunks and never loads remote frontend code.
- [x] Protected `%APPDATA%\DEVIN\desktop.json` configurator.
- [x] Windows-native cached development host.
- [x] Removal of automatic WSL/local-backend startup and sidecar resources.

## Phase 2 — release build

- [x] Run the guarded `npm run desktop:build` release on the existing Windows
      MSVC toolchain.
- [x] Produce and artifact-verify NSIS `.exe` and MSI installers.
- [x] Exercise current-user NSIS install, Start-menu/Desktop shortcuts and
      clean uninstall while preserving `%APPDATA%\DEVIN\desktop.json`.
- [x] Confirm the installed release executable opens the configured rig front
      door from the owner's normal Windows session and completes one real
      Clippy -> DEVIN -> cockpit connection.
- [ ] Repeat install/onboarding on a clean non-developer Windows VM with
      WebView2 before declaring distribution hardening complete.

Release intermediates use the single external cache
`%LOCALAPPDATA%\DEVIN\build-cache\cargo-target`. Only installers and a redacted
hash manifest are copied to `dist\windows`; neither credentials nor runtime
state enter the release directory. A normal release refuses a dirty Git tree;
`-AllowDirty` exists only for local build experiments.

The exact build and install/uninstall evidence is recorded in
[`WINDOWS_RELEASE_RECEIPT_2026-08-22.md`](WINDOWS_RELEASE_RECEIPT_2026-08-22.md).
The live installed-client lifecycle and chat evidence is recorded separately in
[`WINDOWS_FUNCTIONAL_RECEIPT_2026-08-22.md`](WINDOWS_FUNCTIONAL_RECEIPT_2026-08-22.md).

## Phase 3 — onboarding

- [x] Native first-run form for the front-door URL.
- [x] Save through a Rust command with the same validation/ACL contract as the
      PowerShell configurator.
- [x] Add a TCP-only connection test that cannot activate the DEVIN model.
- [x] Allow editing connection settings from the failure screen.

Release and validation evidence:
[`WINDOWS_ONBOARDING_RECEIPT_2026-08-22.md`](WINDOWS_ONBOARDING_RECEIPT_2026-08-22.md).

## Phase 4 — hardening

- [ ] Code-sign executable and installer to reduce SmartScreen warnings.
- [ ] Define a signed auto-update channel.
- [x] Add a mutation-tested local-bundle smoke for native API/SSE transport
      and the no-remote-navigation policy.
- [ ] Verify the installer on a clean Windows VM with WebView2.

## Rule

Packaging must not reintroduce a local backend, model fallback or remote cleanup
on window close. The rig front door remains the single owner of activation,
busy detection and idle release.
