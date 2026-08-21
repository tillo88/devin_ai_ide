# DEVIN on the AI Rig model slot

DEVIN AI IDE keeps its dedicated frontend and local project-aware backend. The
backend sends inference to the broker-owned `devin` role; it does not own GPU
lifecycle and never starts, stops or selects a model directly.

## Routing contract

The configured inference origin must be HTTP loopback. There are two supported
placements:

- backend on the rig: direct `http://127.0.0.1:18081`;
- backend on the workstation: the same local URL through an SSH tunnel to the
  rig's loopback port.

`DEVIN_RIG_BASE_URL` may override the per-machine settings. The active defaults
are `rig_required=true` and `allow_local_fallback=false`.

Before a request is admitted, `AIClient` reads `/v1/models` and requires exactly
one non-empty OpenAI-compatible model ID. That discovered ID is used for both
coding and reasoning calls. No model family, artifact path or historical role
name is stored in this repository. Missing or ambiguous inventory fails closed.

Local/cloud fallback is possible only for explicit development setups that set
both `rig_required=false` and `allow_local_fallback=true`. It is never selected
because the rig or tunnel happens to be unavailable.

## Role lifecycle

The AI Rig model-slot broker remains the only lifecycle authority. A rig-hosted
DEVIN backend service must bind to `ai-rig-model-slot@devin.service` and start
only after a successful broker lease. When that role unit stops, systemd stops
the backend as well; this prevents the long-lived frontend from talking to the
resident Clippy role after the shared port changes ownership.

The desktop backend remains local so it can read workstation files. Opening the
SSH tunnel does not switch roles. Role acquisition/release stays a separate,
reviewed broker operation.

## Memory boundary

AutoMem and Understory are disabled in the example configuration until
DEVIN-private namespaces exist. DEVIN must never point at Clippy's collection,
graph, storage roots, tokens or direct Understory endpoint. Future cross-role
knowledge enters through reviewed promoted artifacts, not a shared live memory
database.
