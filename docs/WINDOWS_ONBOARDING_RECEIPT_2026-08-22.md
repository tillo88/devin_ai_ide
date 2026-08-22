# DEVIN Windows onboarding receipt — 2026-08-22

## Release identity

- source: `main` at `6360e7ff05d330e242b39b246f16c709578d483e`;
- application version: `0.2.0`;
- manifest: `devin_windows_release_v1`, `source_dirty=false`;
- profile: Windows thin client, no bundled backend or model.

| Bundle | File | Bytes | SHA-256 |
|---|---|---:|---|
| NSIS | `DEVIN AI IDE_0.2.0_x64-setup.exe` | 1,930,097 | `41a04988027b81b9e6b531b1bb9a861fc2d2a5460ac25b20db1d8e61849401da` |
| MSI | `DEVIN AI IDE_0.2.0_x64_en-US.msi` | 2,883,584 | `8bca4b1d6c2b2e2ba3fc0e5868965a8bdcb528d54560147fd5e602177a355bbf` |

Both hashes were recomputed after the clean-main build. The packages remain
unsigned until the separate signing/update hardening phase.

## Security and lifecycle contract

- Rust returns configuration state and frontdoor URL, never the stored token;
- first-run/save validates the URL and token before writing;
- config file and parent directory symlinks/junctions are rejected;
- Windows user SID and `SYSTEM` are the only ACL grants applied by the native
  save path;
- the JSON is written with `create_new`, flushed, and published with
  `MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH)`;
- blank token on an already configured client preserves the existing token
  Rust-side;
- `Test senza attivare` receives only the URL and performs a bounded TCP probe;
  it cannot send credentials, call `/control/activate` or change the GPU role;
- normal connect remains the only command that consumes the stored token and
  navigates to the frontdoor bootstrap URL.

## Validation evidence

- Windows Python suite: 643 passed, 5 skipped, 3 expected deselections;
- Rust unit tests: 6 passed, including temporary atomic write and real
  user/SYSTEM ACL application;
- Rust Clippy: PASS with warnings denied;
- Node UI tests: 6 passed; bootstrap syntax: PASS;
- isolated Linux source contract: 7 passed; Node syntax: PASS;
- browser QA at desktop size and 540×760: no horizontal overflow, correct
  responsive actions, token autocomplete off and first-run token required;
- browser QA found and closed a bundle-parity issue: root `sw.js` and
  `manifest.webmanifest` are now included and returned HTTP 200;
- no `src-tauri\target` exists in the repository or generated desktop host.

## Installed upgrade

The installed NSIS client was upgraded from 0.1.0 to 0.2.0, then reinstalled
from the clean `main` artifact:

- installer exit code 0;
- registry and executable product version 0.2.0;
- Desktop and Start-menu shortcuts preserved;
- `%APPDATA%\DEVIN\desktop.json` SHA-256 unchanged across both operations;
- application left installed and stopped.

No frontdoor navigation, backend activation, model execution, NVML, USB,
model SHA or 32K probe was performed. Functional owner acceptance remains a
separate deliberate test.
