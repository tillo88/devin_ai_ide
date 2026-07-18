# DEVIN Mini Bench report — 2026-07-15

This report records the first real run of `DEVIN Mini Bench` after the sandbox setup fix.

## Executive summary

The benchmark runner now works: it created sandbox projects, invoked local models, wrote files, committed outputs, and recorded attempts.

However, the first UI result `ok 3` was misleading. The runner classified all three as `auto_success` because scaffold/file creation succeeded and syntax checks passed. Manual validation showed only one real success.

Final reviewed outcome:

| Case | Runner status | Manual validation | Meaning |
|---|---:|---:|---|
| Create tested add function | auto_success | verified_success | Generated function and tests; pytest passes. |
| Fix off-by-one loop | auto_success | verified_failure | Generated files, but final pytest suite fails. |
| Official API only / Steam checker | auto_success | verified_failure | Generated files, but tests fail and it used invented/non-official Steam endpoints. |

The training data was manually reclassified so the memory is not contaminated by superficial `auto_success` labels.

## Attempts

### 1. Create tested add function

- Attempt: `attempt_882c7b6965b1`
- Run: `train_20260715_070543_500103`
- Artifact: `workspace/_training_runs/devin_mini/create_tested_add_function_train_20260715_070543_500103`
- Files: `add.py`, `test_add.py`
- Runner result: 2/2 files written, syntax OK
- Manual pytest: `5 passed`
- Final status: `verified_success`

### 2. Fix off-by-one loop

- Attempt: `attempt_80b81bb56c71`
- Run: `train_20260715_070654_083426`
- Artifact: `workspace/_training_runs/devin_mini/fix_off_by_one_loop_train_20260715_070654_083426`
- Files: `src/loop_utils.py`, `tests/test_loop_utils.py`, `main.py`
- Runner result: 3/3 files written, syntax OK
- Manual pytest: `1 failed, 1 passed`
- Final status: `verified_failure`

Why it failed: the generated project left an intentionally failing test as a normal test. If a benchmark wants to preserve “fails first” evidence, it must separate baseline failure from final green suite, or mark expected failures explicitly with `xfail`/metadata. The deliverable itself should end clean.

### 3. Official API only / Steam checker

- Attempt: `attempt_ca1a6a015315`
- Run: `train_20260715_070823_813723`
- Artifact: `workspace/_training_runs/devin_mini/official_api_only_train_20260715_070823_813723`
- Files: 10 files, including `src/api/steam_api.py`, `tests/test_api.py`, `README.md`
- Runner result: 10/10 files written, syntax OK
- Manual pytest: failed (`5 failed` before maxfail)
- Final status: `verified_failure`

Why it failed semantically:

- Used invented/non-official base URL: `https://steamcommunity.ste.com/api/steam/`.
- Did not follow the required official host: `api.steampowered.com`.
- Invented many endpoint families instead of limiting to documented Steam Web API endpoints.
- Tests did not align with implementation.

This is a useful failure: it proves the need for source-discipline validators and Teacher review before anything becomes memory/dataset material.

## Key lesson

`auto_success` must mean only “runner completed its mechanical checks”. It must not mean “correct solution”. Promotion requires validation.

Current desired status ladder:

1. `runner_error`: infrastructure problem; not model quality.
2. `auto_success`: scaffold/code generation completed and basic checks passed.
3. `auto_failure`: scaffold/code generation failed mechanically.
4. `verified_success`: human/Teacher/test harness confirms result.
5. `verified_failure`: human/Teacher/test harness confirms failure and records why.

Only `verified_success`, `verified_failure`, and `human_confirmed` should be recall-safe.

## Required next engineering work

### A. Strengthen quality gate

The runner must execute real tests before assigning `auto_success` when tests exist.

Minimum detection:

- `tests/` directory;
- `test_*.py`;
- `*_test.py`;
- `tests.py`;
- `pyproject.toml` pytest config;
- `package.json` test script for JS projects later.

Python command should use the known WSL-safe pattern:

```bash
venv/bin/python -m pytest -q --capture=no
```

Inside generated sandbox, use DEVIN repo venv or a sandbox venv depending on future isolation policy.

### B. Add benchmark validators

Each benchmark case should have validators beyond “files exist”.

For `Official API only`, add allowlist validator:

- required host: `api.steampowered.com`;
- allowed endpoints:
  - `ISteamUser/GetPlayerSummaries`
  - `IPlayerService/GetOwnedGames`
  - `ISteamUserStats/GetUserStatsForGame`
- API key passed as URL/query parameter;
- no invented hosts/endpoints;
- no third-party trackers unless case explicitly allows them.

### C. Add Teacher review queue

Training page should show attempts needing review:

- attempt id;
- case;
- generated artifact;
- runner result;
- pytest result;
- semantic validators;
- buttons: `Promote success`, `Mark failure`, `Write correction`, `Send to Teacher`.

### D. Keep main UI clean

The main desktop page should not show all internal cognitive panels. Move these to a dedicated diagnostics/training area:

- Cognitive loop;
- Memory safety;
- Eval detectors;
- raw run log;
- detailed training cases;
- memory write/read audit.

Main page should focus on:

- projects/chats;
- central conversation/work stream;
- compact current task status;
- composer;
- minimal model/memory badges.

## UI direction after this test

The user wants a desktop app, not a browser-first web UI. `/app` is a prototyping surface. The target is Tauri desktop, similar in cleanliness to Codex/Claude Desktop.

Proposed desktop navigation:

- **Workspace**: main coding/chat surface.
- **Runs**: run history and logs.
- **Training**: benchmark queue, attempts, review, corrections.
- **Memory**: memory browser, promotion queue, contamination guardrails.
- **Settings**: models, rig role, CUDA/VRAM, paths, Tauri/backend status.

## Do not forget

The “ok 3” from the first visible mini bench was not the final truth. After validation it became:

- 1 verified success;
- 2 verified failures;
- 0 current runner errors after the sandbox fix;
- 3 older runner errors preserved from the pre-fix broken run.
