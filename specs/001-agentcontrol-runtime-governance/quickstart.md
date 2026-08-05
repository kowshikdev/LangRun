# Quickstart & Validation Guide: AgentControl v0.1

**Plan**: [plan.md](./plan.md) | **Contracts**: [contracts/](./contracts/)

Six runnable scenarios that together prove the feature. Each maps to spec acceptance scenarios and success criteria. This is a validation guide — implementation lives in `tasks.md` and the code.

## Prerequisites

```bash
# Library + dev tooling
uv sync --all-groups          # or: pip install -e ".[dev]"

# OPA (Linux/macOS container; on Windows use Docker Desktop or WSL)
docker run -d --name opa -p 8181:8181 \
  -v "$PWD/policies:/policies" \
  openpolicyagent/opa:latest run --server \
  --set decision_logs.console=true \
  /policies

# OTLP sink — either one
docker run -d -p 6006:6006 -p 4318:4318 arizephoenix/phoenix:latest
# or Langfuse per its compose file, then point OTEL_EXPORTER_OTLP_ENDPOINT at it

export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

`--set decision_logs.console=true` is not cosmetic: without a decision-log plugin OPA omits `decision_id` entirely (`refs/opa/v1/runtime/runtime.go:930-938`), and Scenario 2's `agentcontrol.policy.decision_id` assertion will fail.

Verify OPA is answering before anything else:

```bash
curl -s localhost:8181/v1/data/agentcontrol/authz/result \
  -H 'Content-Type: application/json' \
  -d '{"input":{"agent":{"id":"t"},"action":{"tool":"noop","arguments":{}},"context":{"trust":"unknown"},"evidence":{},"trace":{"trace_id":"0","span_id":"0"}}}'
# expect: {"decision_id":"...","result":{"decision":"allow","reason":"...","policy_id":"agentcontrol.authz.default_allow",...}}
```

Note the trailing `/result` — querying the bare package path (`/v1/data/agentcontrol/authz`) returns the whole package document (the `result` rule nested one level deeper, alongside the bundle's `injection_block_threshold`/`review_window_seconds` constants), not the decision object shown above.

An empty `{}` here means the bundle did not load — the SDK treats that as provider failure and denies, which is correct but not what you want to be debugging in Scenario 1.

---

## Scenario 1 — Zero-config passthrough (Phase 0)

**Proves**: FR-031, SC-007, spec US1 scenario 6.

```bash
python examples/governed_agent.py --mode passthrough
pytest tests/integration/test_passthrough.py
```

`ControlPlane()` with no providers attaches to an unmodified agent. Expected: identical outputs to the ungoverned run, no spans emitted, added latency under 5 ms per tool call.

---

## Scenario 2 — Deny a destructive action (Phase 2)

**Proves**: FR-001, FR-005…FR-007, FR-012, US1 scenarios 1 and 3, SC-001.

```bash
pytest tests/integration/test_deny.py -v
```

Agent is prompted, from **untrusted** context, to call `github.delete_repository`. Expected:

- the tool's side effect never happens;
- the agent receives a `ToolMessage(status="error")` carrying the reason and rule id, and keeps reasoning rather than crashing;
- one span named `execute_tool github.delete_repository` with `agentcontrol.policy.decision="deny"`, `agentcontrol.policy.id="agentcontrol.authz.deny_destructive_tool"`, `agentcontrol.policy.decision_id` non-empty, `agentcontrol.context.trust="untrusted"`, `agentcontrol.capability.enforcement=true`.

Same tool from **trusted** context against a non-production resource is allowed — the trust dimension is doing real work, not decoration.

---

## Scenario 3 — OPA unreachable, fail closed (Phase 2)

**Proves**: FR-008…FR-011, US1 scenarios 4 and 5, SC-003.

```bash
docker stop opa
pytest tests/integration/test_fail_closed.py -v
docker start opa
```

Expected with `fail_mode: closed` (default): every tool call denied; `agentcontrol.policy.unavailable=true`, `agentcontrol.policy.fail_mode="closed"`, `error.type="agentcontrol.policy_unavailable"`, **no** `agentcontrol.policy.id` — no rule fired, and claiming one would poison the audit trail. Zero tools execute.

Re-run with `fail_mode: open`: calls proceed, `unavailable=true`, `fail_mode="open"`, and a WARNING is logged on **every** occurrence.

Also covered without stopping the container: a policy path that does not exist (200 with no `result`) must deny, not allow.

---

## Scenario 4 — Human review: approve, reject, expire (Phase 2)

**Proves**: FR-014…FR-018, US3 all scenarios, SC-006.

```bash
pytest tests/integration/test_review.py -v
```

Requires a checkpointer — `interrupt()` does not work without one (`refs/langgraph/libs/langgraph/langgraph/types.py:830-831`).

| Case | Action | Expected |
|---|---|---|
| approve | `Command(resume={"decision":"approve"})` before the deadline | tool executes; resolution span `review.state="approved"` |
| reject | `Command(resume={"decision":"reject","reason":…})` | denial returned; `review.state="rejected"` |
| expire | wait past a 2-second test window, then resume with `approve` | **denied anyway**; `review.state="timed_out"`, recorded distinctly from `rejected` |
| restart | kill the process mid-hold, reopen the SQLite checkpointer, resume late | still denied — the deadline is read from the persisted payload, not memory |
| watchdog | leave the process alive past the deadline | auto-resolves to denial within 30 s |

The expire and restart cases are the ones that matter: a review that quietly becomes an approval because a timer died is worse than having no review at all.

Note the documented gap (research R3): a process that dies mid-hold and is *never* resumed leaves the hold pending. It can never become an `ALLOW`, but no active auto-deny fires either.

---

## Scenario 5 — Explain a denial from the backend alone (Phase 3)

**Proves**: FR-019…FR-025, US2 all scenarios, SC-004, SC-009.

```bash
python examples/governed_agent.py --mode deny --export otlp
# open http://localhost:6006 (Phoenix) or the Langfuse UI
```

Manual check, deliberately: hand the trace to someone unfamiliar with the code and have them answer *who tried to do what, against what, and why was it blocked* — using the UI only, no application logs. Repeat against both backends with the same run to prove FR-024's vendor-neutrality claim.

Also assert: governance spans share `trace_id` with the surrounding agent spans (FR-023), and no separate product run id exists anywhere in the payload.

---

## Scenario 6 — Capability conformance and misconfiguration (Phase 4)

**Proves**: FR-026…FR-030, US4 all scenarios, SC-005, SC-008.

```bash
pytest tests/conformance/ -v
```

Eleven tests, one per manifest field, each exercising a real `create_agent` graph. Details and pass conditions: [contracts/capability-manifest.md](./contracts/capability-manifest.md).

Negative case:

```bash
pytest tests/conformance/test_startup_validation.py -v
```

`RequiredCapabilities(human_approval=True)` against a graph compiled **without** a checkpointer must fail `attach()` with a `CapabilityMismatchError` naming that exact gap and suggesting the fix. Passing quietly here would be the worst possible outcome for this project — it is the false-assurance failure the whole product exists to prevent.

Plus the drift guards: manifest fields without a conformance test fail the suite; vendored `gen_ai.*` constants are checked against `refs/semantic-conventions-genai/model/gen-ai/registry.yaml`.

---

## Full validation run

```bash
pytest tests/unit -q                      # no network
pytest tests/integration -q               # needs OPA + OTLP sink
pytest tests/conformance -q               # needs a model or a scripted fake
opa test policies/                        # Rego unit tests
ruff check . && mypy agentcontrol
```

## Phase gates (root plan.md §11)

Do not start a phase before the previous one's scenarios pass:

| Phase | Scenario | Gate |
|---|---|---|
| 0 — scaffolding | 1 | zero behavior change with no providers |
| 1 — interception | 2 (spans only, everything ALLOW) | every tool call produces a correctly attributed span |
| 2 — policy | 2, 3, 4 | deny, fail-closed, review pause/resume/timeout |
| 3 — telemetry | 5 | denial explainable from Langfuse/Phoenix alone |
| 4 — conformance | 6 | manifest proven, not assumed; misconfiguration fails startup |
