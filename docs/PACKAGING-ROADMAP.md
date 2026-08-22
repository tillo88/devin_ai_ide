# Roadmap — DEVIN come app Windows installabile

Goal: double-click to open the DEVIN workspace while all backend, training and
model work remains on the rig.

## Architecture decision

The supported profile is rig-first and thin-client-only:

- Tauri/WebView2 runs on Windows;
- the authenticated front door and FastAPI run on the rig;
- project workspaces are linked on the rig and synchronized through GitHub;
- the front door activates DEVIN lazily and returns to Clippy only when idle;
- no Python, llama-server, GGUF model or backend sidecar is bundled on Windows.

A future local profile is a separate product decision, not an automatic
fallback. The desktop must fail visibly if its configured rig is unavailable.

## Phase 1 — thin client foundation (complete)

- [x] Bundled connection/retry screen.
- [x] Rust-side URL/token validation and front-door reachability check.
- [x] Token kept out of JavaScript and converted by the front door to an
      `HttpOnly` cookie.
- [x] Protected `%APPDATA%\DEVIN\desktop.json` configurator.
- [x] Windows-native cached development host.
- [x] Removal of automatic WSL/local-backend startup and sidecar resources.

## Phase 2 — release build

- [x] Run the guarded `npm run desktop:build` release on the existing Windows
      MSVC toolchain.
- [x] Produce and artifact-verify NSIS `.exe` and MSI installers.
- [x] Exercise current-user NSIS install, Start-menu/Desktop shortcuts and
      clean uninstall while preserving `%APPDATA%\DEVIN\desktop.json`.
- [ ] Confirm the release executable opens the configured rig front door from
      a normal, non-developer Windows account.

Release intermediates use the single external cache
`%LOCALAPPDATA%\DEVIN\build-cache\cargo-target`. Only installers and a redacted
hash manifest are copied to `dist\windows`; neither credentials nor runtime
state enter the release directory. A normal release refuses a dirty Git tree;
`-AllowDirty` exists only for local build experiments.

The exact build and install/uninstall evidence is recorded in
[`WINDOWS_RELEASE_RECEIPT_2026-08-22.md`](WINDOWS_RELEASE_RECEIPT_2026-08-22.md).

## Phase 3 — onboarding

- [ ] Native first-run form for front-door URL and secret.
- [ ] Save through a Rust command with the same validation/ACL contract as the
      PowerShell configurator.
- [ ] Add a connection test that does not activate the DEVIN model.
- [ ] Allow editing connection settings from the failure screen.

## Phase 4 — hardening

- [ ] Code-sign executable and installer to reduce SmartScreen warnings.
- [ ] Define a signed auto-update channel.
- [ ] Add release-build tests for token redaction, cookie bootstrap and remote
      navigation policy.
- [ ] Verify the installer on a clean Windows VM with WebView2.

## Rule

Packaging must not reintroduce a local backend, model fallback or remote cleanup
on window close. The rig front door remains the single owner of activation,
busy detection and idle release.
