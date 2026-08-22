# DEVIN P2–P8 — implementation and acceptance checkpoint

**Date:** 2026-08-22

**Scope:** canonical roadmap in `devin_grounding_master_v1.md`

**State:** implementation candidate; repository and live-rig gates are recorded
separately below and must not be conflated.

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
| Full Linux suite | PASS: 607 passed, 1 skipped, 4 warnings in 24.30s |
| DEVIN PR / merge | pending |
| Rig source deploy | blocked safely until frontdoor `:5000` is stopped for the source-only gate |
| Runtime service install | pending after source deploy; separate root checkpoint |
| Desktop source sync / smoke | pending after merged backend source |

The source-deploy failure on issue `ai-rig-ops#1013` changed nothing: the
`DISABLED_NEUTRAL` gate detected the still-listening frontdoor on port 5000 and
failed closed. It must not be weakened.
