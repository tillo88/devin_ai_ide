# DEVIN Codex-like Mental Model

This document describes how DEVIN should evolve after the baseline: not just a UI clone, but a local coding agent with a visible, reliable work loop.

## North star

DEVIN should feel like a compact local sibling of Codex/Claude Desktop:

- calm desktop-grade interface;
- project-aware chat;
- visible context and memory;
- explicit plan/action/verification state;
- no fake execution or fake completion;
- failures saved as failures and used to improve retries.

## Cognitive loop

Every serious task should pass through this loop:

1. **Perceive**: collect project files, chat history, user intent, pins, knowledge, relevant memories, model/GPU state, and web evidence when required.
2. **Frame**: decide whether the user wants discussion, planning, maintenance, scaffold, eval, or memory work.
3. **Plan**: produce a small, inspectable plan with assumptions and risk.
4. **Act**: modify files through tools, not through chat snippets, when the task is operational.
5. **Verify**: run syntax checks, tests, smoke checks, and quality gates proportionate to risk.
6. **Reflect**: classify the result as success, failure, hypothesis, correction, or pending review.
7. **Remember**: store structured memory with status, evidence, polarity, scope, and promotion policy.
8. **Surface**: show the user what happened in the UI: files, commands, tests, memories used, memories written, and remaining uncertainty.

## UI mental structure

A Codex-like DEVIN desktop should be organized around three permanent surfaces:

### Left: Workspace

- projects;
- chats;
- pinned files;
- current branch/baseline;
- quick create/open actions.

### Center: Conversation and work stream

- chat messages;
- agent activity cards;
- command/test output;
- diff previews;
- explicit success/failure states.

### Right: Mind panel

- current plan;
- project context being used;
- model and GPU state;
- web/search state;
- recall-safe memories used;
- new memories written;
- eval/quality gate results;
- warnings about uncertainty or contamination risk.

## Backend contract for the Mind panel

The UI should not infer DEVIN's mind from free text. It should use structured APIs:

- `GET /api/mind/status`: stable high-level cognitive state, memory policy, model status, and product direction.
- Future: `GET /api/run/{run_id}/events`: streaming plan/action/verification/memory events.
- Future: `GET /api/project/context`: files, pins, instructions, knowledge, and memories used for one request.
- Future: `GET /api/evals`: eval scenarios and last outcomes.

## Learning policy

DEVIN does not learn by mixing everything into one memory soup.

Recall-safe:

- `verified_success`
- `verified_failure`
- `human_confirmed`

Review-only:

- `pending_review`
- `hypothesis`
- `quarantine`
- `syntax_only`
- `inconclusive`
- `revoked`
- `superseded`

A failure is useful only when it has a cause, evidence, and retry rule. A hypothesis is useful only when it remains labeled as a hypothesis until tested or confirmed.

## Immediate implementation path

1. Expose `/api/mind/status` for the future right sidebar.
2. Add run event structure: plan/action/verify/remember events.
3. Render a minimal right-side Mind panel in the existing web UI. **Done via `/app` MVP shell.**
4. Refactor the web UI into a single SPA route. **Started: `/app` has the three-panel skeleton and reads structured APIs.**
5. Move to Tauri once the web app has stable structured APIs.
