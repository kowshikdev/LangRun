---

description: "Task list for AgentControl v0.1 — Runtime Governance for AI Agents"
---

# Tasks: AgentControl v0.1 — Runtime Governance for AI Agents

**Input**: Design documents from `/specs/001-agentcontrol-runtime-governance/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Included, and not optional here. The specification makes tests a deliverable rather than a practice: FR-030 requires every capability proven by an automated conformance suite, SC-008 requires 100% manifest-field coverage, and root `plan.md` §11 makes Phase 4 a test suite. Test tasks below are therefore first-class scope.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete work)
- **[Story]**: Maps to the user story in spec.md (US1–US4)
- Every task names its exact file path

## Path Conventions

Flat package at repository root, per [plan.md](./plan.md#source-code-repository-root): `agentcontrol/`, `policies/`, `tests/{unit,integration,conformance}/`, `examples/`.

## Phase ↔ root plan.md milestone mapping

| This file | Root `plan.md` §11 | Gate |
|---|---|---|
| Phase 1 Setup + Phase 2 Foundational | Phase 0 scaffolding, Phase 1 interception | passthrough is behavior-neutral; every tool call produces an attributed span |
| Phase 3 (US1) | Phase 2 policy — deny + fail-closed | scripted destructive action denied; OPA-down denies |
| Phase 4 (US2) | Phase 3 telemetry completeness | denial explainable from Langfuse/Phoenix alone |
| Phase 5 (US3) | Phase 2 policy — REVIEW | pause, resume, auto-deny on timeout |
| Phase 6 (US4) | Phase 4 conformance | manifest proven; misconfiguration fails startup |

Do not start a phase before the previous gate passes.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, pinned dependencies, tooling

- [ ] T001 Create `pyproject.toml` at repository root with `requires-python = ">=3.10"`, package `agentcontrol`, and the pins from [plan.md](./plan.md#technical-context): `langchain==1.3.14`, `langgraph==1.2.10`, `httpx>=0.27,<1`, `opentelemetry-sdk~=1.45`, `opentelemetry-api~=1.45`, `opentelemetry-exporter-otlp-proto-http~=1.45`, `opentelemetry-semantic-conventions~=0.66b0`; dev group `pytest`, `pytest-asyncio`, `respx`, `langgraph-checkpoint-sqlite`, `ruff`, `mypy`
- [ ] T002 [P] Create the package skeleton with `__init__.py` files: `agentcontrol/`, `agentcontrol/core/`, `agentcontrol/providers/`, `agentcontrol/providers/policy/`, `agentcontrol/providers/tracing/`, `agentcontrol/adapters/`, `agentcontrol/adapters/langgraph/`, plus empty `policies/`, `examples/`, `tests/unit/`, `tests/integration/`, `tests/conformance/`
- [ ] T003 [P] Configure `ruff` and `mypy` in `pyproject.toml` — strict mode on `agentcontrol`, type hints and return types required on every public function
- [ ] T004 [P] Configure `pytest` in `pyproject.toml` with `asyncio_mode = "auto"` and markers `integration`, `conformance`, `live_opa`, so network-dependent suites are opt-in and `tests/unit` stays offline
- [ ] T005 [P] Create `docker-compose.yml` at repository root running `openpolicyagent/opa` with `--server --set decision_logs.console=true` mounting `./policies`, plus `arizephoenix/phoenix` for the OTLP sink, per [quickstart.md](./quickstart.md#prerequisites)
- [ ] T006 [P] Create `.github/workflows/ci.yml` running lint, type-check, and `tests/unit` on Python 3.11/3.12/3.13, and `tests/integration` on Linux only (the OPA container path is Linux-only while development is on Windows)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Types, telemetry plumbing, and interception that every user story builds on. Corresponds to root `plan.md` Phase 0–1.

**⚠️ CRITICAL**: No user story work can begin until T024 and T025 pass.

- [ ] T007 Implement all core dataclasses and protocols in `agentcontrol/core/types.py` — `Verdict`, `Evidence`, `ControlResult`, `ActionIntent`, `CapabilityManifest`, `RequiredCapabilities`, `InlineControl`, `AsyncAnalyzer`, `PolicyProvider`, `FrameworkAdapter` — with the fields and new additions listed in [data-model.md](./data-model.md)
- [ ] T008 [P] Implement `CapabilityMismatchError`, `PolicyUnavailableError`, and `ConfigurationError` in `agentcontrol/core/errors.py`
- [ ] T009 [P] Implement `ControlPlaneConfig`, `PolicyConfig`, `ReviewConfig`, and `TelemetryConfig` in `agentcontrol/core/config.py` with the defaults table from [data-model.md](./data-model.md#configuration) — `timeout_ms=300`, `fail_mode="closed"`, `default_timeout_seconds=900`, `record_tool_arguments=False`, `record_tool_results=False`, `context_trust="unknown"`
- [ ] T010 [P] Vendor the `gen_ai.*` and `agentcontrol.*` attribute constants in `agentcontrol/core/semconv.py` per [contracts/otel-attributes.md](./contracts/otel-attributes.md), with a module docstring recording the pin (genai conventions `9af0834`, `SEMCONV_VERSION=v1.43.0`) and why they are vendored rather than imported
- [ ] T011 [P] Write invariant tests in `tests/unit/test_types.py` — `ABSTAIN` rejected in `ControlResult`; `unavailable=True` forces `policy_id=None` and a set `fail_mode_applied`; `verdict=REVIEW` requires `review_timeout_seconds`; `reason` non-empty for every verdict
- [ ] T012 [P] Write config validation tests in `tests/unit/test_config.py` — invalid `fail_mode` raises `ConfigurationError`; `context_trust` defaults to `unknown` and never to `trusted`
- [ ] T013 [P] Write the semconv drift test in `tests/unit/test_semconv_drift.py` — assert every vendored `gen_ai.*` constant against `refs/semantic-conventions-genai/model/gen-ai/registry.yaml`, skipping cleanly when `refs/` is absent (it is gitignored)
- [ ] T014 Implement the span builder in `agentcontrol/core/otel.py` emitting the required `gen_ai.*` attributes only — span name `execute_tool {gen_ai.tool.name}`, kind `INTERNAL`, per [contracts/otel-attributes.md](./contracts/otel-attributes.md#span-identity)
- [ ] T015 [P] Write span-shape tests in `tests/unit/test_otel_span.py` using `InMemorySpanExporter` — name, kind, and required attributes present
- [ ] T016 [P] Implement OTLP/HTTP exporter wiring in `agentcontrol/providers/tracing/otlp.py` — acquire a tracer from the ambient `TracerProvider`, bootstrap one only when explicitly asked, never hijack the host's provider (research R8)
- [ ] T017 Implement `ToolCallRequest` → `ActionIntent` construction in `agentcontrol/adapters/langgraph/intent.py`, reading `tool_call["name"|"args"|"id"]`, `request.state`, `request.runtime.config["configurable"]["thread_id"]`, and the ambient OTel span context (research R1, R7)
- [ ] T018 [P] Write intent-construction tests in `tests/unit/test_intent.py` — trust defaults to `unknown`; unrecognized trust coerces to `unknown`; missing trace context yields all-zero ids and sets the orphan flag; empty `tool` or `agent_id` raises
- [ ] T019 Implement `AgentControlMiddleware(AgentMiddleware)` in `agentcontrol/adapters/langgraph/middleware.py` implementing **both** `wrap_tool_call` and `awrap_tool_call` — not the `@wrap_tool_call` decorator, which installs only one hook and breaks sync agents (research R1). At this stage it builds the intent, emits the span, and always calls `handler(request)`
- [ ] T020 Implement `LangGraphAdapter` and the `LANGGRAPH_CAPABILITIES` manifest in `agentcontrol/adapters/langgraph/adapter.py` with the declared values and source citations from [contracts/capability-manifest.md](./contracts/capability-manifest.md#manifest)
- [ ] T021 Implement the `ControlPlane` entrypoint in `agentcontrol/core/control_plane.py` — constructor, `attach(agent)`, `middleware` property, `aclose()`, per [contracts/python-api.md](./contracts/python-api.md#controlplane); startup validation is a stub raising nothing until Phase 6
- [ ] T022 Export the public surface from `agentcontrol/__init__.py` exactly as listed in [contracts/python-api.md](./contracts/python-api.md)
- [ ] T023 [P] Create `examples/governed_agent.py` with `--mode {passthrough,deny,review}` and `--export {none,otlp}` flags, driving the scenarios in [quickstart.md](./quickstart.md)
- [ ] T024 Write the passthrough gate test in `tests/integration/test_passthrough.py` — `ControlPlane()` with no providers produces outputs identical to an ungoverned agent, emits no spans, and adds under 5 ms per tool call (FR-031, SC-007, quickstart Scenario 1)
- [ ] T025 Write the interception gate test in `tests/integration/test_span_emission.py` — every tool call in a test `create_agent` graph produces exactly one correctly attributed span in an in-memory collector, with everything still `ALLOW`

**Checkpoint**: Root plan Phase 0 and Phase 1 acceptance both pass. User story work may begin.

---

## Phase 3: User Story 1 — Block an unauthorized agent action (Priority: P1) 🎯 MVP

**Goal**: Every tool call is authorized by OPA before execution; denials short-circuit; an unreachable policy engine denies rather than opening the gate.

**Independent Test**: Run a test agent prompted from untrusted context to call `github.delete_repository` against a policy that denies destructive tools. The tool never executes, the agent receives a structured denial and keeps reasoning, and the run does not crash. Then stop OPA and confirm every call is denied and marked as unavailability.

### Tests for User Story 1

> Write these first and confirm they fail before implementing T032–T041.

- [ ] T026 [P] [US1] Write the OPA request contract test in `tests/unit/test_opa_contract.py` — the body AgentControl sends validates against [contracts/opa-input.schema.json](./contracts/opa-input.schema.json), and every response the client accepts validates against [contracts/opa-result.schema.json](./contracts/opa-result.schema.json)
- [ ] T027 [P] [US1] Write the denial test in `tests/integration/test_deny.py` using `respx` — destructive tool from untrusted context: tool side effect never fires, agent receives `ToolMessage(status="error")` carrying reason and rule id, run continues (US1 scenarios 1–2, SC-001)
- [ ] T028 [P] [US1] Write the trust-dimension test in `tests/integration/test_trust_context.py` — the same tool is allowed from trusted context and denied from untrusted context (US1 scenario 3)
- [ ] T029 [P] [US1] Write the fail-closed matrix test in `tests/integration/test_fail_closed.py` covering all six failure rows in [contracts/opa-authz.md](./contracts/opa-authz.md#failure-mapping-fr-009-fr-010) — connect error, timeout, non-2xx, non-JSON body, **absent `result` (undefined Rego document returns HTTP 200)**, and an out-of-range `decision`; each yields `DENY` with `unavailable=True` and no `policy_id` (US1 scenario 4, SC-003)
- [ ] T030 [P] [US1] Write the fail-open override test in `tests/integration/test_fail_open.py` — with `fail_mode="open"` the call proceeds, `unavailable=True`, `fail_mode_applied="open"`, and a WARNING is logged on every occurrence (US1 scenario 5, FR-011)
- [ ] T031 [P] [US1] Write Rego unit tests in `policies/tool_authorization_test.rego` — default allow, both deny rules, the review rule, and the empty-evidence case; runnable via `opa test policies/`

### Implementation for User Story 1

- [ ] T032 [US1] Write the example bundle `policies/tool_authorization.rego` returning the **result object** (`decision`, `reason`, `policy_id`, `review_timeout_seconds`), using `object.get(input, ["evidence","nemo_injection","score"], 0)` so an undefined evidence path does not make the whole rule undefined — full source in [contracts/opa-authz.md](./contracts/opa-authz.md#example-bundle--policiestool_authorizationrego)
- [ ] T033 [US1] Implement the `PolicyProvider` base and shared fail-mode resolution in `agentcontrol/providers/policy/base.py` — converts any failure into a `ControlResult`, never raises into the hot path
- [ ] T034 [US1] Implement `OPAPolicyProvider` in `agentcontrol/providers/policy/opa.py` — `POST {url}/v1/data/{path}` with `{"input": …}` via a pooled `httpx.AsyncClient`, per-request `timeout=policy.timeout_ms`, mapping `result` plus `decision_id` into `ControlResult`
- [ ] T035 [US1] Implement the six failure detections from T029 in `agentcontrol/providers/policy/opa.py`, setting `unavailable=True`, `fail_mode_applied`, and leaving `policy_id` as `None`
- [ ] T036 [US1] Implement concurrent inline-evidence collection in `agentcontrol/adapters/langgraph/middleware.py` via `asyncio.gather(..., return_exceptions=True)` with a 50 ms budget — a collector that raises or times out drops its signal and is never defaulted to a passing value (FR-003, spec edge case)
- [ ] T037 [US1] Wire the policy call into `agentcontrol/adapters/langgraph/middleware.py` — build intent, collect evidence, authorize, then short-circuit on `DENY` by **not** calling `handler(request)` (research R2)
- [ ] T038 [US1] Implement the denial response builder in `agentcontrol/adapters/langgraph/middleware.py` returning `ToolMessage(status="error", …)` carrying reason and `policy_id`, so the agent can keep reasoning rather than crash (FR-012)
- [ ] T039 [US1] Add a sync bridge in `agentcontrol/adapters/langgraph/middleware.py` so the sync `wrap_tool_call` hook drives the same async provider path against a dedicated loop, keeping one code path for sync and async agents (research R5)
- [ ] T040 [US1] Record authorization round-trip time as `agentcontrol.policy.latency_ms` in `agentcontrol/core/otel.py` for the SC-002 budget
- [ ] T041 [US1] Emit a WARNING on every fail-open use in `agentcontrol/providers/policy/base.py`, naming the deployment setting that enabled it (FR-011)

**Checkpoint**: US1 is fully functional and independently testable. This is the MVP — unsafe actions are prevented even with nothing else built.

---

## Phase 4: User Story 2 — Explain any decision from the observability backend alone (Priority: P2)

**Goal**: Every decision is exported as a correlated span carrying enough governance detail that a reviewer needs no application logs.

**Independent Test**: Trigger a denied action, open Phoenix or Langfuse, and have someone unfamiliar with the code answer who tried to do what, against what, and why it was blocked — from the UI alone.

### Tests for User Story 2

- [ ] T042 [P] [US2] Write the full attribute-set test in `tests/unit/test_governance_attributes.py` using `InMemorySpanExporter` — every attribute in [contracts/otel-attributes.md](./contracts/otel-attributes.md#agentcontrol-attributes) present for allow, deny, review, and unavailable, matching the two worked examples (US2 scenarios 1–2, FR-022)
- [ ] T043 [P] [US2] Write the opt-in privacy test in `tests/unit/test_attribute_optin.py` — `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result` absent by default and present only when the flags are set (research R4)
- [ ] T044 [P] [US2] Write the correlation test in `tests/integration/test_correlation.py` — governance spans share `trace_id` with surrounding agent spans, no separate run id exists anywhere in the payload, and a decision made with no ambient context is still exported with `agentcontrol.correlation.orphan=true` (US2 scenario 3, FR-023, spec edge case)
- [ ] T045 [P] [US2] Write the one-span-per-decision test in `tests/integration/test_span_cardinality.py` — exactly one governance span per allow and per deny; retried and concurrent tool calls each get their own (FR-019, spec edge cases)

### Implementation for User Story 2

- [ ] T046 [US2] Extend `agentcontrol/core/otel.py` with the full `agentcontrol.*` attribute set — decision, provider, id, decision_id, reason, unavailable, fail_mode, latency_ms, context trust and source, capability enforcement, action resource
- [ ] T047 [US2] Implement evidence attribute namespacing as `agentcontrol.evidence.{collector}.{signal}` in `agentcontrol/core/otel.py`, with the v0.1 empty-collector case producing no attributes rather than empty ones
- [ ] T048 [US2] Implement orphan-context handling in `agentcontrol/core/otel.py` — start a new trace and set `agentcontrol.correlation.orphan=true` when `INVALID_SPAN_CONTEXT` is current (research R7)
- [ ] T049 [US2] Force `ALWAYS_ON` sampling for governance spans in `agentcontrol/providers/tracing/otlp.py` regardless of the host sampler — a sampled-away denial is an unauditable denial (FR-019, research R8)
- [ ] T050 [US2] Record the outcome of allowed tool executions in `agentcontrol/adapters/langgraph/middleware.py`, including `error.type` when the tool itself fails (FR-025)
- [ ] T051 [US2] Add `record_tool_arguments` / `record_tool_results` gating in `agentcontrol/core/otel.py`, defaulting off, with a docstring citing the upstream sensitive-data warning
- [ ] T052 [US2] Write `docs/backend-validation.md` with the Langfuse and Phoenix round-trip procedure from [quickstart.md](./quickstart.md#scenario-5--explain-a-denial-from-the-backend-alone-phase-3), and record the observed result for both (SC-004, SC-009, US2 scenario 4)

**Checkpoint**: US1 and US2 both work independently. Root plan Phase 3 acceptance passes.

---

## Phase 5: User Story 3 — Hold a risky action for human approval (Priority: P3)

**Goal**: A `review` verdict pauses the run at the tool call, resumes on a human decision, and auto-denies when the window expires — durably.

**Independent Test**: Configure a policy returning `review` for a production resource. Confirm the run pauses without executing the tool; resume with approval and confirm execution; repeat and let the window expire, confirming denial.

**Depends on**: US1 (the policy path must exist to return `review`).

### Tests for User Story 3

- [ ] T053 [P] [US3] Write the approve/reject tests in `tests/integration/test_review_resolution.py` — `Command(resume={"decision":"approve"})` executes the tool and records `review.state="approved"`; `reject` returns a denial with `review.state="rejected"` (US3 scenarios 1–3)
- [ ] T054 [P] [US3] Write the expiry test in `tests/integration/test_review_timeout.py` — with a 2-second test window, resuming late with `approve` is **denied anyway** and recorded as `timed_out`, distinctly from `rejected` (US3 scenario 4, FR-017, FR-018)
- [ ] T055 [P] [US3] Write the durability test in `tests/integration/test_review_restart.py` using `SqliteSaver` — tear down and rebuild the process objects mid-hold, resume late, and confirm the deadline read from the persisted payload still denies (FR-018, research R3)
- [ ] T056 [P] [US3] Write the watchdog test in `tests/integration/test_review_watchdog.py` — a live process auto-resolves an expired hold to denial within 30 seconds (SC-006)
- [ ] T057 [P] [US3] Write the policy-driven window test in `tests/integration/test_review_window_from_policy.py` — changing `review_timeout_seconds` in the Rego bundle changes the effective window with no application code change (US3 scenario 5, FR-016)

### Implementation for User Story 3

- [ ] T058 [US3] Implement the `ReviewHold` payload, `hold_id = f"{thread_id}:{tool_call_id}"`, and absolute ISO-8601 UTC deadline serialization in `agentcontrol/adapters/langgraph/review.py` per [data-model.md](./data-model.md#reviewhold-new--fr-014fr-018)
- [ ] T059 [US3] Implement the interrupt path in `agentcontrol/adapters/langgraph/middleware.py` — emit the pending span, then call `langgraph.types.interrupt(payload)`; the deadline travels **inside** the payload so it is persisted in the host's checkpointer (research R3)
- [ ] T060 [US3] Implement resume resolution in `agentcontrol/adapters/langgraph/review.py` — parse `{"decision","actor","reason"}`, compare `now()` against the deadline **read back from the persisted payload**, and let expiry override an `approve` unconditionally (FR-018)
- [ ] T061 [US3] Implement the idempotency guard in `agentcontrol/adapters/langgraph/middleware.py` — LangGraph re-executes the node from the start on resume, so a pending hold for this `hold_id` skips evidence collection and the OPA call; a cold guard after restart re-authorizes and marks the duplicate span `agentcontrol.review.replay=true` (research R3)
- [ ] T062 [US3] Implement terminal-state handling in `agentcontrol/adapters/langgraph/review.py` — a second resume against a resolved hold returns the recorded outcome and does not re-authorize
- [ ] T063 [US3] Implement the in-process review watchdog in `agentcontrol/core/control_plane.py` — an `asyncio` task resuming expired holds with a `__agentcontrol_timeout__` sentinel, started by `attach()` and stopped idempotently by `aclose()` (research R3)
- [ ] T064 [US3] Add the review span attributes — `review.state`, `review.deadline`, `review.hold_id`, `review.replay` — in `agentcontrol/core/otel.py`, emitting one pending span and one linked resolution span
- [ ] T065 [US3] Plumb `review_timeout_seconds` from the OPA result through `ControlResult` into the hold deadline in `agentcontrol/providers/policy/opa.py` and `agentcontrol/adapters/langgraph/review.py`, falling back to `review.default_timeout_seconds` only when the policy is silent (FR-016)
- [ ] T066 [US3] Document the cross-restart gap in `docs/review-holds.md` — a process that dies mid-hold and is never resumed stays pending; it can never become an `ALLOW`, but no active auto-deny fires (research R3, [plan.md](./plan.md#open-risk-carried-into-implementation))

**Checkpoint**: US1, US2, and US3 all work independently. Root plan Phase 2 acceptance fully passes.

---

## Phase 6: User Story 4 — Fail loudly when the runtime cannot enforce (Priority: P4)

**Goal**: Capability gaps are hard startup failures naming the exact gap, never a silent downgrade to observation-only.

**Independent Test**: Pair a policy requiring human approval with a graph compiled without a checkpointer; `attach()` must raise `CapabilityMismatchError` naming that gap and suggesting the fix.

**Depends on**: US1 for `block_before_tool` proofs and US3 for `human_approval` proofs. The conformance suite exercises real capabilities, so it cannot precede the features it proves — an honest dependency, not a design flaw.

### Tests for User Story 4

- [ ] T067 [P] [US4] Write observation-capability conformance tests in `tests/conformance/test_observation.py` covering `observe_model_calls`, `observe_tool_calls`, `intercept_model_input`, `intercept_model_output` against a real `create_agent` graph
- [ ] T068 [P] [US4] Write tool-class conformance tests in `tests/conformance/test_tool_classes.py` — `intercept_function_tools` with a real `@tool`; `intercept_mcp_tools` with a **real MCP-backed tool**, not a stand-in `BaseTool`; `intercept_hosted_tools` proving the declared `False` is truthful by confirming a hosted-tool call is not intercepted
- [ ] T069 [P] [US4] Write enforcement conformance tests in `tests/conformance/test_enforcement.py` — `block_before_tool` (side effect never happens), `modify_tool_arguments` (tool observes overridden args), `human_approval` (interrupt, resume, and deadline auto-deny), `streaming_interception` proving the declared `False` is truthful
- [ ] T070 [P] [US4] Write the startup-validation negative test in `tests/conformance/test_startup_validation.py` — `RequiredCapabilities(human_approval=True)` against a checkpointer-less graph fails `attach()` with a message naming the gap and the fix, matching the shape in [contracts/capability-manifest.md](./contracts/capability-manifest.md#startup-validation) (US4 scenarios 2–3, SC-005)
- [ ] T071 [P] [US4] Write the manifest drift guard in `tests/conformance/test_manifest_coverage.py` — enumerate `CapabilityManifest.__dataclass_fields__` and fail if any field lacks a conformance test, making an unproven twelfth capability impossible (SC-008)
- [ ] T072 [P] [US4] Write the no-downgrade test in `tests/conformance/test_no_downgrade.py` — grep the package for any flag, env var, or config key that converts a capability mismatch into observation-only mode, and fail if one exists (FR-029, US4 scenario 4)

### Implementation for User Story 4

- [ ] T073 [US4] Implement `RequiredCapabilities` field-by-field validation in `agentcontrol/core/control_plane.py` — every `True` requirement must be `True` in the adapter manifest (FR-027)
- [ ] T074 [US4] Implement the checkpointer presence check in `agentcontrol/core/control_plane.py` — `human_approval` required without a checkpointer on the compiled graph is a startup failure, since `interrupt()` cannot work without one
- [ ] T075 [US4] Implement the `handle_tool_errors` inspection in `agentcontrol/adapters/langgraph/adapter.py` — when `human_approval` is required, the resolved `ToolNode._handle_tool_errors` must be the default re-raising handler, otherwise a review hold silently becomes a tool error (research R2)
- [ ] T076 [US4] Implement the `thread_id` resolvability check in `agentcontrol/core/control_plane.py` — a hold with no thread cannot be resumed, so this is a startup failure rather than a runtime surprise
- [ ] T077 [US4] Implement runtime capability enforcement in `agentcontrol/adapters/langgraph/middleware.py` — a verdict the manifest cannot enforce denies the call and raises `CapabilityMismatchError`, so a wrong declaration fails loudly on first contact instead of under-enforcing silently
- [ ] T078 [US4] Implement the structured error message in `agentcontrol/core/errors.py` — required capability, adapter manifest value, reason, and suggested fix, exactly as specified in [contracts/capability-manifest.md](./contracts/capability-manifest.md#startup-validation) (FR-028)
- [ ] T079 [US4] Set `agentcontrol.capability.enforcement` on every span from the active manifest in `agentcontrol/core/otel.py`, so a reviewer can tell whether a given call was actually blockable (FR-022)
- [ ] T080 [US4] Implement the optional `strict_policy_scan` in `agentcontrol/core/control_plane.py` — run `opa eval` to enumerate reachable decision values and cross-check the declaration; off by default because it needs the `opa` binary, and a mismatch fails startup when on

**Checkpoint**: All four user stories independently functional. Root plan Phase 4 acceptance passes.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T081 [P] Write `README.md` at repository root — what AgentControl is, the two integration shapes from [contracts/python-api.md](./contracts/python-api.md#two-integration-shapes), and the v0.1 scope boundary
- [ ] T082 [P] Write `docs/configuration.md` from the [data-model.md](./data-model.md#configuration) settings table, stating explicitly that no authorization-affecting threshold may be added there (FR-013)
- [ ] T083 [P] Write the performance benchmark in `tests/integration/test_performance.py` — governance overhead ≤400 ms p95 and ≤500 ms p99 with a reachable OPA (SC-002); ≤5 ms with no providers (SC-007)
- [ ] T084 Implement the startup `decision_id` warning in `agentcontrol/providers/policy/opa.py` — a probe response omitting `decision_id` logs a loud warning naming OPA decision logging, without failing startup (research R5)
- [ ] T085 Add a threshold-relocation test in `tests/integration/test_policy_thresholds.py` — changing one authorization cutoff end to end requires editing only the Rego bundle, with zero application code or component config changes (SC-010)
- [ ] T086 Run the full [quickstart.md](./quickstart.md) validation and record results, including the manual SC-004 explainability check with a reviewer unfamiliar with the codebase
- [ ] T087 Review argument and result handling for sensitive-data leakage across `agentcontrol/core/otel.py` and `agentcontrol/providers/policy/opa.py` — arguments go to OPA in full but must stay off spans by default (spec edge case)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks all user stories**
- **US1 (Phase 3)**: depends on Foundational. No dependency on other stories
- **US2 (Phase 4)**: depends on Foundational. Testable against US1 verdicts, but the span machinery stands alone
- **US3 (Phase 5)**: depends on Foundational **and US1** — a `review` verdict has to come from somewhere
- **US4 (Phase 6)**: depends on Foundational, **US1** (to prove `block_before_tool`), and **US3** (to prove `human_approval`). Conformance tests exercise real capabilities, so they cannot precede them
- **Polish (Phase 7)**: depends on the stories being delivered

### Within Each User Story

Tests are written first and must fail before implementation. Types before providers, providers before middleware wiring, middleware before integration.

### Parallel Opportunities

- Setup: T002–T006 all parallel
- Foundational: T008–T010 parallel; T011–T013 parallel; T015, T016, T018 parallel
- US1: all six test tasks T026–T031 parallel; then T032 (Rego) parallel with T033–T035 (client)
- US2: T042–T045 parallel; T046–T051 mostly touch `otel.py` and should be sequential
- US3: T053–T057 parallel
- US4: T067–T072 parallel
- Cross-story: US1 and US2 can be built simultaneously by two people after Foundational

## Parallel Example: User Story 1

```bash
# All US1 tests together (they must fail first):
Task: "OPA request contract test in tests/unit/test_opa_contract.py"
Task: "Denial test in tests/integration/test_deny.py"
Task: "Trust-dimension test in tests/integration/test_trust_context.py"
Task: "Fail-closed matrix test in tests/integration/test_fail_closed.py"
Task: "Fail-open override test in tests/integration/test_fail_open.py"
Task: "Rego unit tests in policies/tool_authorization_test.rego"

# Then policy bundle and client in parallel:
Task: "Write policies/tool_authorization.rego"
Task: "Implement OPAPolicyProvider in agentcontrol/providers/policy/opa.py"
```

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 Setup
2. Phase 2 Foundational — **do not skip**; T024 and T025 are the root plan's Phase 0 and Phase 1 gates
3. Phase 3 US1
4. **Stop and validate**: quickstart Scenarios 1–3
5. At this point unsafe actions are actually prevented — the product's whole reason to exist works, without telemetry polish, review holds, or conformance proofs

### Incremental Delivery

1. Setup + Foundational → passthrough is behavior-neutral, spans emit
2. + US1 → enforcement works, fails closed (**MVP**)
3. + US2 → decisions explainable from the observability backend alone
4. + US3 → ambiguous actions get bounded human review
5. + US4 → capability gaps become loud startup failures instead of false assurance

### Parallel Team Strategy

After Foundational: Developer A takes US1 (the critical path), Developer B takes US2 (independent span machinery). US3 starts when US1 lands; US4 starts when US3 lands.

## Notes

- `[P]` = different files, no dependency on incomplete work
- Every task cites the requirement, research decision, or contract that produced it — a task with no such anchor is scope creep
- Tests fail before implementation; commit after each task or logical group
- **Open risk carried from planning**: SC-006's restart clause is not fully satisfiable in v0.1. The deadline is durable, so an expired hold can never become an `ALLOW`, but actively auto-denying a hold in a dead process needs a checkpointer query LangGraph does not portably expose. T066 documents it; amending SC-006 to scope the 30-second bound to a live process is the recommended resolution
