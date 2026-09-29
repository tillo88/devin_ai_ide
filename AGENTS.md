# AGENTS.md - DEVIN AI IDE working rules

Practical instructions. Read the first section before touching anything: the
single most expensive mistake in this project's history has been diagnosing a
fault on a copy of the code that nobody runs.

Current handoff and active objective: read `CURRENT.md` first.

## 1. Source, generated copies and runtime are distinct

| where | what it is | who executes it |
|---|---|---|
| `F:\devin_ai_ide` (Windows) | **primary development checkout** | local Codex, Git, frontend/Tauri tools |
| `origin/main` on GitHub | shared repository truth | CI and source deploy |
| `/opt/devin-ai-ide-frontend` (on the rig) | **the code in production** | `devin-backend.service` |
| `/home/tillo/devin_ai_ide` (on the rig) | Linux verification checkout | pytest/diagnostics, never production |
| `%LOCALAPPDATA%\DEVIN\desktop-host` | generated Windows mirror | Tauri development runtime |
| `%LOCALAPPDATA%\DEVIN AI IDE` | installed release | stable packaged executable |

The unit says so:

```
devin-backend.service
  WorkingDirectory=/opt/devin-ai-ide-frontend
  ExecStart=/opt/devin-ai-ide-frontend/.venv-rig/bin/python devin/ui/fast_app.py
```

On 2026-09-18 the copies were all different: the development checkout was **50
commits behind**, production was three behind, and the Windows checkout matched
neither. Half a day was spent diagnosing defects that only existed on a dormant
copy.

Since 2026-09-29 normal source work starts in `F:\devin_ai_ide`. SSHFS mappings
to the rig are visibility/transfer surfaces, not alternative editing roots.
Build output in `src-tauri/frontend` and `desktop-host` is regenerable and must
never become the source of a fix.

**Before diagnosing anything, compare the fingerprints:**

```bash
for f in devin/ai/client.py devin/ui/routers/chat.py devin/ui/static/js/codex_app.js; do
  printf "%-42s opt=%s  origin=%s  dev=%s\n" "$f" \
    "$(sha256sum /opt/devin-ai-ide-frontend/$f | cut -c1-12)" \
    "$(git -C /home/tillo/devin_ai_ide show origin/main:$f | sha256sum | cut -c1-12)" \
    "$(git -C /home/tillo/devin_ai_ide show main:$f      | sha256sum | cut -c1-12)"
done
```

If the columns disagree, **realign first, investigate second**. A defect found
on a stale copy is not a defect of the system.

Deploy is a `git pull --ff-only` in `/opt` run as `tillo`; the backend picks up new code
on its next activation (it is `PartOf` the model slot), so no manual restart.

The Tauri copy under `%LOCALAPPDATA%\DEVIN\desktop-host` is generated runtime
output, not a source repository.

## 2. Environment

- **There is no WSL on the Windows machine.** It was reformatted; WSL and git are
  not part of the runtime. Historical continuity notes that describe a WSL
  workflow, and any `/mnt/c/...` path, describe a setup that no longer exists.
  Do not follow them.
- Windows frontend work happens in `F:\devin_ai_ide`. Linux-only checks and
  service operations happen on the rig in an SSH-hosted Codex chat.
- Verify a repository root with `git rev-parse --show-toplevel`; never infer it
  from a path in an old document.
- Keep DEVIN and `ai-rig-ops` in separate checkouts and separate PRs.
- **Interpreter with pytest:** `/home/tillo/devin_ai_ide/.venv-rig/bin/python3`
  (Python 3.12, pytest 9.1.1). The system `python3` has no pytest. This detail
  has cost time three separate times.
- `node` is available on the rig (v18) for `node --check` and `.mjs` tests.

## 3. Ports

| port | listener | bound to | called by |
|---|---|---|---|
| 5000 | `ai-rig-devin-frontdoor` | `0.0.0.0` — the only one on the LAN | the Windows thin client |
| 5001 | `devin-backend` (FastAPI) | `127.0.0.1` | the frontdoor only |
| 18081 | the model, via broker | `127.0.0.1` | the backend only |
| ~~8080~~ | **gone** | — | the pre-broker `llama-server` |

`_validated_rig_base_url` rejects any endpoint that is not loopback, by design:
a stale configuration fails at startup rather than passing unnoticed.

A backend that is `inactive` while Clippy is the resident role is **normal**, not
a fault.

## 4. Editing and verification

- Keep changes incremental; commit at a green test point.
- Never read or commit secrets or runtime state: `.env`, `tinyfish api.txt`,
  live memory JSONL, logs, models, workspace outputs.
- Keep `/`, `/chat` and `/history` as fallbacks while `/app` matures.
- After a change, with the venv interpreter:
  - `python -m py_compile devin/ui/fast_app.py`
  - `node --check devin/ui/static/js/codex_app.js` — for any JS change
  - `node test_pannello_ragionamento.mjs` — for the reasoning panel
  - `python -m pytest -q` before a broad commit
- Check `codex_app.html` / `.js` / `.css` for unintended non-ASCII.

**`config/settings.json` is not tracked by git, and eight test files read it.**
In CI it does not exist and the suite is green; on the rig it exists and changes
outcomes. When you report a suite result, state which `settings.json` produced
it, or the number means nothing. The production one lives in
`/opt/devin-ai-ide-frontend/config/`.

**A check that has never gone red is not a check.** Any bench added here must be
mutation-tested: break the thing on purpose and prove the bench turns red. A
mutation that fails to find its target leaves the bench green and looks like a
confirmation — assert that the substitution applied.

## 5. Guardrails (the operator's, confirmed)

- branch + PR, never a direct write to `main`
- bounded SIGTERM, never SIGKILL
- **never NVML while a model is running** (no `nvidia-smi`)
- no forced or lazy unmount; no manual deletion of locks or journals
- no silent fallback
- never teach the held-out Golden tasks
- `/opt/ai-rig-ops` is never touched directly
- one controlled SSH command per message
- **no secrets in chat**
- every script is syntax-checked **and dry-run** before delivery; embedded Python
  blocks must be compiled separately — `bash -n` does not look inside heredocs,
  and nested heredocs need distinct terminators

## 6. Local project, SSH host and SSHFS surfaces

The active Codex project is local on Windows at `F:\devin_ai_ide`. A chat bound
to an SSH host sees that host's filesystem and cannot add `F:` retroactively;
start a local-host chat for Windows/frontend work.

Current Windows mappings:

| mapping | target | contract |
|---|---|---|
| `Z:\` | `/home/tillo` on `192.168.1.100` | read/transfer/diagnostics; do not develop in `Z:\devin_ai_ide` |
| `ai-rig-shared` network location | `/mnt/ai-rig-shared` | artifacts/evidence/recovery under their ownership rules |
| `R:\` | home on Raspberry `192.168.1.86` | watcher diagnostics only |

Validate mappings at session start. SSHFS provides file access, not a shell or
service ownership. Use the SSH-hosted Codex project for `systemctl`, journal,
Linux tests and deploy. Do not edit `/opt`, live memory, locks, journals or
runtime outputs through SSHFS.

The historical `_bridge` relay can remain as local recovery material but is no
longer the primary workflow. Never commit `_bridge/`, `_backup/` or
`Claude outputs/`; never remove them with `git clean`.

## 7. Product direction

- DEVIN evolves toward a local Codex/Claude-Desktop-like workspace: left
  workspace, centre conversation/work-stream, right Mind/context panel.
- Operational endpoints keep chat/knowledge metadata on the DEVIN project but
  execute against its validated linked `work_dir` when present; keep that routing
  consistent across run, scaffold, resume and generate-patch.
- Memory stays anti-contamination-first: verified successes/failures and
  human-confirmed lessons are recall-safe; hypotheses, quarantine and
  pending-review are review-only.
- A failure is a useful memory only when stored with cause, evidence and retry
  rule.
- Chat continuity checkpoints are per-conversation handoff state, never
  recall-safe long-term memory. Keep them bounded, evidence-labelled, paired with
  recent verbatim turns, and transferable to a successor chat without copying
  history.

## 8. The model reasons

The selected model thinks before answering, sometimes for minutes. Code written
for a model that did not think is wrong here in specific ways:

- **sampling** comes from `models.sampling`; temperature below ~0.3 makes a
  thinking model loop (measured: 11,000 characters of circular reasoning, no
  code);
- **reasoning effort** reaches the model through
  `chat_template_kwargs.reasoning_effort`; an unknown value raises rather than
  being silently ignored;
- **thought and answer are separate channels.** `stream_eventi()` yields typed
  events; `stream()` returns the answer only. Thought must never enter chat
  history — sending it back next turn teaches the model to repeat itself;
- **timeouts** follow the declared effort. The rig does 8.8 tokens/second on a
  27B; 60 seconds was written for a model that did not think.
