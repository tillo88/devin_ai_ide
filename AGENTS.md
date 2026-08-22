# AGENTS.md - DEVIN AI IDE working rules

These instructions are intentionally practical. The authoritative development
checkout is the clean Git worktree selected by the operator/Codex Desktop. The
Tauri copy under `%LOCALAPPDATA%\DEVIN\desktop-host` is generated runtime output,
not a second source repository.

## Environment routing

- Verify the repository root with `git rev-parse --show-toplevel`; never infer it
  from an old WSL path recorded in historical continuity notes.
- Keep DEVIN and `ai-rig-ops` in separate Git worktrees and separate PRs.
- WSL is optional. When it is absent, edit/test on Windows and use an isolated
  workspace on the rig for Linux-only tests.

## Windows/WSL quoting rules

- Prefer small, boring commands. Avoid shell pipes, command separators, subshells, and regex alternation `|` in commands passed through `cmd.exe`; Windows may interpret them before WSL receives them.
- For grep searches, prefer repeated `-e` patterns instead of a single alternation pattern containing `|`.
- For multi-line inspections or edits, prefer a temporary Python script stored under `/mnt/c/Users/tillo/AppData/Local/Temp/`, then execute it inside WSL `Ubuntu`.
- For generated scripts or large file content, use `repr()` as the primary quoting strategy. Avoid nested heredocs/triple-quoted strings when the content contains Markdown, backticks, `$`, pipes, or newlines.
- If a generated script needs to concatenate a newline, prefer `chr(10)` or a value produced with `repr()` rather than embedding an ambiguous literal newline in the generator.

## Editing and verification

- Keep changes incremental and commit after a green test point.
- Preserve local secrets and runtime state. Do not read or commit `tinyfish api.txt`, `.env`, live memory JSONL, logs, models, or workspace runtime outputs.
- For UI shell work, keep `/`, `/chat`, and `/history` as fallbacks while `/app` matures.
- After UI/backend changes, run at least the equivalent commands with the
  active repository venv (Windows or Linux):
  - `python -m py_compile devin/ui/fast_app.py`
  - `python -m pytest -q --capture=no test_understory_hybrid.py test_scaffold_resilience.py`
- Before committing broad changes, run the full suite when practical:
  - `python -m pytest -q --capture=no`
- For the new `/app` assets, check that `devin/ui/templates/codex_app.html`, `devin/ui/static/js/codex_app.js`, and `devin/ui/static/css/codex_app.css` do not contain mojibake/non-ASCII surprises unless intentionally added.

## Product direction reminders

- DEVIN should evolve toward a local Codex/Claude Desktop-like workspace: left workspace, center conversation/work-stream, right Mind/context panel.
- Operational endpoints must keep chat/knowledge metadata on the DEVIN project but execute against its validated linked `work_dir` when present; keep this routing consistent across run, scaffold, resume, and generate-patch flows.
- Memory must stay anti-contamination-first: verified successes/failures and human-confirmed lessons are recall-safe; hypotheses/quarantine/pending-review are review-only.
- Failures are useful memories only when stored with cause, evidence, and retry rule.
- Chat continuity checkpoints are per-conversation handoff state, never recall-safe
  long-term memory. Keep them bounded, evidence-labelled, paired with recent
  verbatim turns, and transferable to a successor chat without copying history.
