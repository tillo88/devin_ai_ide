# Hardening status - 2026-07-13

## Implemented

- Added a repository `.gitignore` for virtual environments, secrets, logs, caches,
  generated state and local model weights.
- Rejected absolute and parent-traversal paths before any patch backend runs.
- Fixed new-file patches being applied twice and prefer the canonical `-p1` strip level.
- Added dry-run checks before invoking GNU patch on a live sandbox.
- Rejected scaffold filenames outside the selected project.
- Synced modified and newly-created non-Python files back from the sandbox.
- Moved orchestrator state initialization after the effective project path is selected.
- Recomputed the AI endpoint between retries so rig-to-local fallback can work.
- Added runner process-group termination, a hard timeout and a real Stop hook.
- Assigned a unique sandbox directory to every orchestrator run so concurrent runs do
  not delete or overwrite each other's sandbox.
- Disabled automatic dependency installation by default. It can be explicitly enabled
  with `DEVIN_AUTO_INSTALL_DEPS=1`; commands no longer use `shell=True`.
- Restricted operational API project paths to workspace projects or roots explicitly
  connected through the folder picker.
- Added bounded streaming reads for knowledge uploads.
- Rejected knowledge URLs resolving to loopback, private, link-local or reserved IPs.
- Disabled project-local pickle cache loading by default. Temporary compatibility can be
  enabled with `DEVIN_ALLOW_UNSAFE_PICKLE_CACHE=1`.
- Removed API-key prefixes from startup logs.
- Made the real-model pipeline probe skip normal pytest collection.
- Added regression tests for patch traversal, duplicate new files, scaffold traversal,
  runner timeout, non-Python sync, API path validation and SSRF blocking.

## Verification

- Python compileall: pass.
- Pytest: 19 passed, 1 manual integration probe skipped, 0 warnings.
- Dependency consistency: `pip check` passes.

## Remaining priority work

1. Run generated programs in a real OS sandbox/container with restricted filesystem,
   network, CPU, memory and process counts. The current copy-based sandbox is not a
   security boundary.
2. Track and synchronize file deletions, not only creations and modifications.
3. Move blocking model streams off the FastAPI event loop.
4. Require authentication when binding the UI beyond loopback.
5. Replace pickle persistence with a safe versioned format instead of permanently
   re-indexing.
6. Repair the optional sentence-transformers stack. The installed PyTorch uses CUDA 13.0
   while torchvision was built for CUDA 12.6, so semantic search currently falls back to
   TF-IDF.
7. Extend the coding context collector beyond Python files.
8. Create a reviewed initial Git commit after deciding which sample workspace projects
   belong in source control. No commit was created automatically.
