# DEVIN P2–P8 — implementation and acceptance checkpoint

**Date:** 2026-08-22

**Scope:** canonical roadmap in `devin_grounding_master_v1.md`

**State:** accepted, merged and live-validated. Repository, source-deploy,
runtime and desktop gates remain recorded separately below so that a passing
source test is never conflated with a live lifecycle receipt.

## Safety invariants

- The rig model-slot remains the only owner of GPU lifecycle transitions.
- No code in P4–P8 starts, stops or swaps a model.
- Clippy, DEVIN, future Hermes and future Teacher keep separate raw AutoMem and
  Understory stores. Cross-role knowledge uses a separate reviewed exchange.
- A Council result is a candidate verdict. It never promotes training data or
  memory without the existing deterministic rerun and promotion gates.
- Capability routing returns `ready`, `activation_required` or `unavailable`;
  `automatic_switch` is always false.
- Hermes and Teacher are declared as future roles and disabled. No absent model
  is claimed as installed or usable.

## P2 — bounded deterministic orchestrator

The existing Goal/orchestrator path already owns step/time budgets, timeout,
no-progress protection, verifier gates and explicit maintenance checkpoints.
Acceptance is regression certification of those contracts, not a second
orchestrator implementation.

## P3 — controlled subagents

The existing mini-swarm dispatches distinct scaffolder, tester and debugger
roles behind the same project/work-dir allowlist and bounded retry loop. P3
acceptance requires the existing Goal Mode and scaffold resilience tests to
remain green.

## P4 — Context Steward

Implemented:

- explicit compaction triggers;
- content-addressed checkpoint proposals tied to the exact transcript boundary;
- orchestrator-side validation before persistence;
- checkpoint state explicitly marked `promotion=none`;
- stable prompt prefix fingerprint separated from ephemeral per-turn retrieval;
- recent verbatim user/assistant tail remains outside the summary.

The fingerprint is observability metadata; DEVIN does not pretend to own or
force a model runtime KV cache.

## P5 — anti-contamination memory exchange

`KnowledgeExchangeStore` is physically separate from every raw role store.
Proposals are content-addressed and quarantined. Promotion requires verified
source evidence plus verified evidence from an independent reviewer role.
Reviews are append-only; rejected or revoked artifacts cannot later be promoted
in place. Audience and expiry are enforced when promoted knowledge is read.

API: status, proposal, review and promoted-artifact read. There is deliberately
no endpoint that exposes another role's raw AutoMem/Understory records.

## P6 — Federated Evidence Council

Implemented backend foundation:

- five independent review axes;
- blind per-reviewer packets without other verdicts;
- coverage-first routing, bounded reviewer/token/time budgets and family
  deduplication per axis;
- structured verdict validation and fail-closed aggregation;
- disagreement outcome `arbiter_required` rather than majority voting;
- arbiter resolution authority is a content-addressed deterministic experiment
  result, never the model's provisional opinion;
- every result reports `promotion_performed=false`.

Today only deterministic local reviewers for constraints and security are
declared available. The three semantic axes correctly remain missing until a
real reviewed adapter/model is installed; this produces `needs_evidence`.

## P7 — desktop UX and observability

The `/app` Mind rail now surfaces:

- promoted/quarantined knowledge-exchange counts and raw-store separation;
- Council axis coverage and missing semantic reviewers;
- enabled versus future-disabled roles;
- a read-only capability-plan preview that states no switch was executed.

The historical `/`, `/chat` and `/history` fallbacks remain untouched.

## P8 — versioned routing profiles and canary

`config/routing_profiles.v1.json` contains capabilities and role policy, not
model artifact paths. Active roles are Clippy and DEVIN; Hermes and Teacher are
future-disabled. Coding/scaffold/debug/test/repository capabilities are
dedicated to DEVIN. Compatible already-resident roles may answer a quick
question, avoiding an unnecessary swap.

Canary assessment requires distinct verified receipts tied to the exact profile
fingerprint. Passing a canary still performs neither promotion nor switch. A
real role transition must go through the ai-rig lifecycle owner and its normal
receipt path.

## Verification and deployment ledger

Fill this ledger only from receipts:

| Gate | Result |
|---|---|
| Local syntax / diff check | PASS: Python `compileall`, JavaScript `node --check`, `git diff --check` |
| Targeted Linux tests | PASS: 212 passed, 4 warnings, isolated rig workspace at `2ed9493` |
| Full Linux suite | PASS: 609 passed, 1 skipped, 4 warnings in 22.64s; includes governance HTTP/UI smoke |
| DEVIN PR / merge | PASS: PR `tillo88/devin_ai_ide#12`, merge `92a15cbc440f362ff9fe0c30444aaba8a3972d8f` |
| Rig source deploy | PASS: `tillo88/ai-rig-ops#1015`, request `20260822T014935Z-2ef811378281`, receipt `/mnt/ai-rig-shared/runner-bridge-receipts/source-deploy-broker-20260822T015013Z-issue1015-2ef811378281` |
| Runtime service install | PASS: merged sources installed; frontdoor, SearXNG and shutdown-v2 enabled and active; legacy commit timer disabled |
| Desktop source sync / smoke | PASS: protected Tauri config, authenticated `/app` HTTP 200 from Windows, governance panel and routing preview present |
| Live DEVIN activation | PASS: broker lease, selected RVN Q6_K MTP model, frontdoor `ready=true`, backend loopback-only, zero restarts |
| Live governance API | PASS: health, knowledge exchange, Council status, routing status and read-only routing plan |
| Natural DEVIN release | PASS: backend and DEVIN closed, `DEVIN_SESSION_RECOVERY=PASS`, Clippy restored as resident, zero restarts |
| Final resident state | PASS: `devin status` reports READY / resident Clippy / DEVIN idle; Clippy health is healthy, admission open, no active requests, recycle not required |

## Live lifecycle evidence

- The first source-deploy request, `ai-rig-ops#1013`, changed nothing: the
  `DISABLED_NEUTRAL` gate detected the still-listening frontdoor on port 5000
  and failed closed. The gate was not weakened. After an exact SIGTERM stop of
  that listener and restoration of the shared memory containers, issue `#1015`
  completed the source-only deployment.
- DEVIN cold activation loaded the selected
  `RVN-Q6_K-mtp.gguf` in approximately 8 minutes 38 seconds on the currently
  slow external storage path. A bounded inference reached approximately
  9.9 generated tokens/second; its cold prompt incurred storage/page-fault
  latency, so no redundant long-context probe was run.
- Authenticated desktop bootstrap reached the always-on frontdoor from Windows.
  The backend stayed bound to loopback and unauthenticated LAN access to the
  frontdoor returned HTTP 401.
- Natural session stop closed DEVIN, then loaded the pinned Clippy
  `Qwen3.8-27B-Q5_K_M.gguf` in approximately 7 minutes 37 seconds before the
  session unit was allowed to finish. The final model-slot has Clippy only.
- Clippy-private AutoMem/Understory containers and the separate shared
  DEVIN/future-role AutoMem/Understory containers were simultaneously healthy.
  No raw-store path was shared between them.
- `ai-rig-role-shutdown-v2.service` is installed, enabled and active. This
  checkpoint deliberately did not reboot or power off the host merely to
  repeat destructive shutdown coverage; its next natural host shutdown will
  produce the host-level receipt. The model-role transition itself was
  exercised end to end with SIGTERM-only lifecycle ownership.
