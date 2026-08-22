# DEVIN Windows release receipt — 2026-08-22

## Release identity

- source: `main` at `340c8d69ad1f0b98fbf8b5e6cdd6829c778815e4`;
- manifest: `devin_windows_release_v1`, `source_dirty=false`;
- target: `x86_64-pc-windows-msvc`;
- profile: Windows thin client (`bundled_backend=false`,
  `bundled_models=false`).

| Bundle | File | Bytes | SHA-256 |
|---|---|---:|---|
| NSIS | `DEVIN AI IDE_0.1.0_x64-setup.exe` | 1,917,571 | `316047a7179ef5a1b76c4a2e8b434a8ffada8ced0a4920dae983bddedc9e104a` |
| MSI | `DEVIN AI IDE_0.1.0_x64_en-US.msi` | 2,871,296 | `eba7757b62cd3476cb0b8a4420ad2b9ea8da7fbd8f93305e15d2ec9f7e4d43c6` |

Both hashes were recomputed after copying the artifacts to `dist\windows`.
The release is intentionally unsigned; code signing remains a Phase 4 gate.

## Offline validation

- PowerShell parser: PASS for all repository `.ps1` scripts;
- Rust/Tauri unit tests: 3 passed;
- Node UI tests: 6 passed;
- Windows Python suite: 641 passed, 5 skipped, 3 expected symlink-test
  deselections;
- Tauri produced both NSIS and MSI bundles with the installed MSVC/WebView2
  toolchain;
- repository and generated desktop host contain no `src-tauri\target`;
- all Cargo intermediates live in the single external cache
  `%LOCALAPPDATA%\DEVIN\build-cache\cargo-target`.

## NSIS install/uninstall exercise

The current-user installer was exercised without launching the app or
contacting the rig:

1. preflight found no existing DEVIN installation or running desktop process;
2. silent install returned exit code 0;
3. install record pointed to `%LOCALAPPDATA%\DEVIN AI IDE`;
4. Start-menu and Desktop shortcuts existed;
5. installed payload contained exactly the application executable and
   uninstaller (8,789,937 bytes total), with no filename matching Python,
   backend, llama, GGUF or safetensors;
6. silent uninstall returned exit code 0 and removed the install directory,
   uninstall record and both shortcuts;
7. `%APPDATA%\DEVIN\desktop.json` had the same SHA-256 before/after the cycle;
8. the `main` artifact was installed again successfully and left stopped,
   ready for owner acceptance from the Desktop shortcut.

No model execution, NVML, USB, model SHA or 32K probe was performed. Clippy
remained active and DEVIN inactive on the rig during packaging/deployment.

## Remaining acceptance

- owner opens the installed app and accepts the UI/connection behavior;
- clean non-developer Windows account or VM exercise;
- signing and update channel after functional acceptance.
