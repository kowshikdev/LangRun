# Review holds: durability and its limits

What a `review` verdict guarantees, what it doesn't yet, and why. See [research.md §R3, R9](../specs/001-agentcontrol-runtime-governance/research.md) for the full verification trail; this is the operator-facing summary.

## What is guaranteed

**An expired hold can never resolve to approval, ever, no matter what fails.** The deadline is computed once, at the moment the hold is created, and stored *inside* the value passed to `interrupt()` — which LangGraph persists in the checkpointer, not in AgentControl's memory. Every path that resolves a hold — a normal resume, a resume after this middleware instance lost its own tracking of the hold, or the in-process watchdog — reads that same persisted deadline and denies unconditionally past it. A forged or manipulated resume payload cannot extend it (`agentcontrol/adapters/langgraph/review.py::resolve` only ever reads the deadline off the hold it was given, never off the human's response).

This is proven, not asserted: `tests/integration/test_review_timeout.py`, `test_review_restart.py`, and `test_review_watchdog.py` exercise it against a real `create_agent` graph.

## What is not yet guaranteed

**Nothing actively resolves a hold whose process died and never came back**, if that process cannot be resumed at all. Two distinct gaps, at two different levels:

1. **AgentControl's own state loss** (this middleware's in-memory tracking of a hold is gone, but the graph object and checkpointer are still alive and reachable) — **fixed**. `AgentControlMiddleware._recover_hold_from_state` queries the graph's own checkpoint (`agent.aget_state()`) for the persisted interrupt before falling back to computing a fresh deadline, so recovery from this kind of loss now reads the true original deadline. `ControlPlane.attach()` wires this in via `middleware.bind_agent(agent)`.

2. **A genuine process restart of a `create_agent()`-based deployment** — **not fixable from here**. Restarting a process necessarily rebuilds the compiled LangGraph graph object, and `create_agent()` in the pinned langchain version (1.3.14) cannot resume a thread from a freshly rebuilt graph object at all: it raises `KeyError('model')` inside its own routing logic, verified independent of any AgentControl code (a bare `AgentMiddleware` calling `interrupt()` directly hits the same error; a hand-built `langgraph.graph.StateGraph` does not). So today, a `create_agent`-based deployment that restarts mid-hold does not silently mishandle it — it fails loudly on resume — but it also cannot gracefully continue the review. Filed as an upstream limitation, not an AgentControl gap.

**In-process liveness** covers the ordinary operational case: `ControlPlane`'s watchdog auto-resolves expired holds to denial roughly every `review.watchdog_poll_seconds` (default 5s) as long as the process is running, which is what SC-006's ≤30-second bound actually measures — a live process, not a dead one.

## Net effect for operators

- A held action can never execute after its window closes, under any combination of the above. This is the safety property that matters.
- If your process stays up, an unanswered hold auto-denies within the configured poll interval. No operator action needed.
- If your process restarts while a hold is pending, the hold does not resolve on its own — and today, resuming it after restart doesn't work at all via `create_agent`. Until the upstream limitation is resolved (see AGENTS.md's Known open questions), treat a restart during an open review window as requiring a fresh decision by the human on the next attempt, not a resumed one.

## Recommendation carried into the spec

[plan.md's Open Risk section](../specs/001-agentcontrol-runtime-governance/plan.md#open-risk-carried-into-implementation) recommends scoping SC-006's ≤30-second bound explicitly to a live process, and tracking full cross-restart resumption — which depends on an upstream fix or a different framework adapter — as v0.2 work.
