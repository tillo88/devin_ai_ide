# AGENTS.md - DEVIN AI IDE working rules

These instructions are intentionally practical. This project is usually edited from Codex Desktop while the real repo lives in WSL `Ubuntu` at `/home/tillo/devin_ai_ide`; the visible Codex cwd may point at a confusing Windows/UNC/Ubuntu-24.04 path.

## Environment routing

- Treat `/home/tillo/devin_ai_ide` inside WSL distro `Ubuntu` as the real DEVIN repo.
- Do not mix this repo with the ai-rig ISO repo in WSL `Ubuntu-24.04` at `/home/tillo/ai-rig-iso-build`.
- When Codex shell sandboxing/UNC paths get in the way, run commands against the real repo with:
  `/init /mnt/c/WINDOWS/system32/cmd.exe /C wsl.exe -d Ubuntu --cd /home/tillo/devin_ai_ide --exec ...`

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
- After UI/backend changes, run at least:
  - `venv/bin/python -m py_compile devin/ui/fast_app.py`
  - `venv/bin/python -m pytest -q --capture=no test_understory_hybrid.py test_scaffold_resilience.py`
- Before committing broad changes, run the full suite when practical:
  - `venv/bin/python -m pytest -q --capture=no`
- For the new `/app` assets, check that `devin/ui/templates/codex_app.html`, `devin/ui/static/js/codex_app.js`, and `devin/ui/static/css/codex_app.css` do not contain mojibake/non-ASCII surprises unless intentionally added.

## Product direction reminders

- DEVIN should evolve toward a local Codex/Claude Desktop-like workspace: left workspace, center conversation/work-stream, right Mind/context panel.
- Operational endpoints must keep chat/knowledge metadata on the DEVIN project but execute against its validated linked `work_dir` when present; keep this routing consistent across run, scaffold, resume, and generate-patch flows.
- Memory must stay anti-contamination-first: verified successes/failures and human-confirmed lessons are recall-safe; hypotheses/quarantine/pending-review are review-only.
- Failures are useful memories only when stored with cause, evidence, and retry rule.
