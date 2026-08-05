# Feature Specification: AgentControl v0.1 — Runtime Governance for AI Agents

**Feature Branch**: `001-agentcontrol-runtime-governance`

**Created**: 2026-08-03

**Status**: Draft

**Input**: User description: "Build AgentControl v0.1 as specified in plan.md — a runtime governance layer for AI agents: a Python SDK with a ControlPlane entrypoint that intercepts LangGraph tool-call intents via wrap_tool_call, builds an ActionIntent, gets a synchronous deterministic authorization decision from OPA (allow/deny/review), fails closed when the policy engine is unreachable, pauses the graph via checkpointer interrupt on REVIEW with a 15-minute auto-deny timeout, validates adapter capability manifests against policy enforcement requirements at startup (hard failure on mismatch), and exports the full decision as OpenTelemetry spans using gen_ai.* plus agentcontrol.* attributes to any OTLP backend."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Block an unauthorized agent action before it happens (Priority: P1)

An application developer runs an AI agent that can call real, consequential tools (delete a repository, issue a refund, send an email). Today the agent decides on its own whether to call them. The developer wants every tool call to be authorized by a separate, deterministic policy before it executes, so that an agent influenced by untrusted content cannot take a destructive action.

The developer registers the governance layer with their existing agent, points it at their policy service, and changes nothing else about the agent. From then on, every tool call the agent attempts is checked first. Denied calls never execute; the agent receives a denial reason instead of a tool result and can continue reasoning.

**Why this priority**: This is the core value proposition. Without enforcement the product is just another tracer. Everything else in this specification exists to make this decision trustworthy, explainable, or safe to operate.

**Independent Test**: Run a test agent whose input contains an injection-style instruction to delete a production resource, with a policy that denies destructive tools against untrusted context. Verify the tool never executes, the agent receives a denial reason, and the run continues without crashing. This alone delivers value: unsafe actions are prevented.

**Acceptance Scenarios**:

1. **Given** a policy that denies a specific destructive tool, **When** the agent attempts to call that tool, **Then** the tool does not execute, and the agent receives a structured denial containing the reason and the identifier of the policy rule that fired.
2. **Given** a policy that allows a tool, **When** the agent calls it, **Then** the tool executes normally and its result is returned unchanged to the agent.
3. **Given** a policy whose denial depends on the trust level of the context that triggered the action, **When** the same tool is called once from trusted context and once from untrusted context, **Then** the trusted call is allowed and the untrusted call is denied.
4. **Given** the policy service is unreachable or exceeds its response deadline, **When** the agent attempts any tool call, **Then** the call is denied, the denial is distinctly marked as a policy-unavailability failure rather than a normal policy denial, and no tool executes.
5. **Given** an operator has explicitly opted into fail-open behavior for a deployment, **When** the policy service is unreachable, **Then** the call proceeds, and the fail-open use is recorded prominently on every occurrence.
6. **Given** the governance layer is registered with no policy or telemetry providers configured, **When** the agent runs, **Then** the agent produces the same behavior and results as an ungoverned run.

---

### User Story 2 - Explain any governance decision from the observability backend alone (Priority: P2)

A security engineer or auditor is asked why an agent was blocked from performing an action last Tuesday. They open the observability backend the organization already uses. Without reading application logs or attaching a debugger, they can see: which agent, on whose behalf, tried to call which tool against which resource; what the trust level and provenance of the influencing context were; what evidence was gathered; what the verdict was; which policy rule produced it; and whether the deciding system was actually able to block the call.

**Why this priority**: An enforcement decision nobody can explain after the fact is not auditable, and an unauditable control fails the compliance use case that justifies deploying it. This must ship with the enforcement, but enforcement without it is still useful in a way that telemetry without enforcement is not.

**Independent Test**: Trigger a denied action, then reconstruct the full decision using only the observability backend's own interface. Success is the reviewer answering "who tried to do what, and why was it blocked" without any other source.

**Acceptance Scenarios**:

1. **Given** any tool-call decision (allowed, denied, or held for review), **When** the run completes, **Then** exactly one governance record exists for that decision in the configured observability backend.
2. **Given** a decision record, **When** a reviewer inspects it, **Then** it carries the verdict, the deciding provider, the policy rule identifier, the trust level and source of the influencing context, whether the policy service was unavailable, and whether the runtime was actually capable of blocking the call.
3. **Given** an agent run that spans several tool calls, **When** a reviewer inspects the trace, **Then** every governance record is correlated with the surrounding agent activity through the same standard correlation identifiers used by the rest of the trace, with no separate product-specific run identifier required.
4. **Given** two different observability backends that both accept the standard telemetry protocol, **When** the same run is exported to each, **Then** both display the governance fields, not only the generic agent fields.

---

### User Story 3 - Hold a risky action for human approval, with a bounded wait (Priority: P3)

Some actions are neither clearly safe nor clearly forbidden — a write against a production resource, a large refund. For these, the policy returns "review". The agent run pauses at that exact point rather than proceeding or failing. A human decides. If the human approves, the run resumes and the tool executes. If the human rejects, the run resumes with a denial. If nobody responds within the configured window, the hold resolves to a denial automatically, so a paused run can never linger indefinitely or quietly become an approval.

**Why this priority**: This converts binary allow/deny into a workable operational model for genuinely ambiguous actions. It is valuable but strictly less critical than deterministic blocking, and it depends on the runtime supporting pause/resume at all.

**Independent Test**: Configure a policy that returns "review" for a specific resource. Trigger it, confirm the run pauses without executing the tool; resume with an approval and confirm the tool executes; repeat and let the window expire, confirming automatic denial.

**Acceptance Scenarios**:

1. **Given** a policy returns "review" for an action, **When** the agent attempts it, **Then** the tool does not execute and the run is suspended in a resumable state.
2. **Given** a suspended run, **When** a human approves, **Then** the run resumes and the tool executes.
3. **Given** a suspended run, **When** a human rejects, **Then** the run resumes and the agent receives a denial with the rejection reason.
4. **Given** a suspended run and no human response, **When** the configured review window elapses, **Then** the hold resolves to a denial, the tool does not execute, and the timeout-driven resolution is recorded distinctly from a human rejection.
5. **Given** an operator changes the review window in policy, **When** a review is triggered, **Then** the new window applies without any change to application code.

---

### User Story 4 - Fail loudly when the runtime cannot enforce what the policy demands (Priority: P4)

A platform operator connects the governance layer to an agent runtime that cannot actually block a particular class of tool call, while the loaded policy contains rules that deny or hold such calls. Instead of starting up and silently degrading into observation-only mode — the failure mode where everyone believes actions are being enforced and none are — the system refuses to start and names the specific gap: which capability the policy requires and which the runtime does not provide.

**Why this priority**: This prevents the most dangerous failure of a governance product: false assurance. It is last only because it protects the other three stories rather than delivering standalone user value.

**Independent Test**: Deliberately pair a policy requiring human approval with a runtime declared incapable of it, and confirm startup fails with a specific, correct error naming that exact gap.

**Acceptance Scenarios**:

1. **Given** every supported runtime declares which governance capabilities it provides, **When** the system starts, **Then** those declarations are compared against what the loaded policy actually requires.
2. **Given** a policy that can deny actions and a runtime that cannot block tool calls, **When** the system starts, **Then** startup fails with an error naming the missing blocking capability.
3. **Given** a policy that can hold actions for review and a runtime without human-approval support, **When** the system starts, **Then** startup fails with an error naming the missing approval capability.
4. **Given** any capability mismatch, **When** startup fails, **Then** the system never falls back to running in a reduced or observation-only mode.
5. **Given** a declared capability, **When** the conformance test suite runs, **Then** each declared capability is proven by exercising the real runtime, not by asserting the declared value against itself.

---

### Edge Cases

- **Policy service slow but reachable**: the response deadline is exceeded mid-flight. Treated identically to unreachable — denied and marked as unavailability, not as a policy denial.
- **Policy service returns an unrecognized or malformed verdict**: treated as a decision failure and resolved by the configured failure mode (deny by default), never coerced into "allow".
- **Evidence gathering fails or exceeds its budget**: the missing signal must not silently become a passing value. The authorization proceeds with that signal explicitly absent, and the policy decides what absence means.
- **Multiple evidence sources disagree**: evidence sources never vote. Only the policy provider produces a verdict, so disagreement is data, not a tie to break.
- **A held run's process restarts before a human responds**: the hold must not resolve to approval as a side effect of the restart.
- **The agent retries a denied tool call in a loop**: each attempt is independently decided and independently recorded; denial does not terminate the agent.
- **Correlation context missing**: a decision must still be recorded and still be attributable, even when the surrounding trace context is incomplete.
- **Tool arguments contain sensitive data**: the decision record must be usable for audit without becoming a new place that sensitive payloads accumulate unreviewed.
- **A numeric cutoff needs changing** (e.g. "how suspicious is too suspicious"): changing it must be a reviewed policy change, not a configuration toggle on the component that produced the signal.
- **Concurrent tool calls in one run**: each is decided and recorded separately, and a hold on one does not silently allow another.

## Requirements *(mandatory)*

### Functional Requirements

**Interception and intent**

- **FR-001**: System MUST intercept every tool call an agent attempts, before that tool executes.
- **FR-002**: System MUST construct, for each intercepted call, a structured action intent containing at minimum: the acting agent's identity, the human user on whose behalf it acts (when known), the task in progress, the tool being called, its arguments, the target resource, the trust level of the influencing context, the provenance of that context, and the correlation identifiers of the surrounding trace.
- **FR-003**: System MUST allow registered inline evidence sources to attach structured evidence to the intent before authorization, and MUST run them concurrently rather than serially.
- **FR-004**: System MUST expose evidence gathering and post-hoc analysis as extension points in v0.1 without shipping any concrete evidence source or analyzer.

**Authorization**

- **FR-005**: System MUST obtain an authorization verdict from a single policy provider before allowing any tool call to proceed.
- **FR-006**: System MUST treat the policy provider as the sole authority for allow/deny/review; evidence sources MUST NOT be able to authorize or block an action themselves.
- **FR-007**: System MUST support exactly three enforceable outcomes — allow, deny, review — and MUST NOT execute the tool for the latter two.
- **FR-008**: System MUST bound the authorization call with a configurable response deadline, defaulting to 300 milliseconds.
- **FR-009**: System MUST deny by default when the policy provider is unreachable, times out, or returns an unusable response.
- **FR-010**: System MUST record policy-unavailability denials distinctly from policy-authored denials, so the two are never conflated in audit.
- **FR-011**: System MUST support an explicit per-deployment fail-open override that is off by default and is recorded prominently on every use.
- **FR-012**: System MUST return, on denial, a structured response to the agent containing the reason and the identifier of the rule that fired, allowing the agent to continue reasoning rather than crashing.
- **FR-013**: System MUST NOT allow any numeric cutoff that changes an authorization outcome to be configured outside the versioned policy.

**Human review**

- **FR-014**: System MUST suspend the agent run, in a resumable state and without executing the tool, when the verdict is review.
- **FR-015**: System MUST resume a suspended run on a human approval by executing the tool, and on a human rejection by returning a denial.
- **FR-016**: System MUST enforce a review window, configurable in policy and defaulting to 15 minutes, after which an unanswered hold resolves to a denial.
- **FR-017**: System MUST record timeout-driven denials distinctly from human rejections.
- **FR-018**: System MUST NOT resolve an unanswered hold to an approval under any circumstance, including process restart.

**Telemetry and audit**

- **FR-019**: System MUST emit exactly one governance record per authorization decision to the configured observability destination, plus exactly one additional resolution record per human-review hold once that hold reaches a terminal state. A record re-emitted because the runtime replayed an interrupted step MUST be marked as a replay so audit can collapse it.
- **FR-020**: System MUST express governance records using the industry-standard agent telemetry conventions where they exist, and MUST NOT define a parallel, product-specific event format.
- **FR-021**: System MUST namespace governance-specific fields so they remain stable for consumers even as the upstream conventions evolve.
- **FR-022**: Each governance record MUST carry: the verdict, the deciding provider, the policy rule identifier, whether the policy provider was unavailable, the context trust level, the context provenance, each evidence signal gathered, and whether the runtime was actually capable of blocking that call.
- **FR-023**: System MUST correlate governance records with surrounding agent activity using the standard trace correlation identifiers, and MUST NOT mint a separate product-specific run identifier.
- **FR-024**: System MUST export to any destination that accepts the standard telemetry protocol, without requiring a vendor-specific integration.
- **FR-025**: System MUST record the outcome of allowed tool executions in addition to the authorization decision.

**Capabilities and startup**

- **FR-026**: Every runtime adapter MUST declare, as an explicit machine-readable manifest, which governance capabilities it provides — including whether it can observe model and tool calls, intercept model input and output, intercept each class of tool, block before execution, modify tool arguments, support human approval, and intercept streaming.
- **FR-027**: System MUST establish what enforcement the loaded policy requires — either derived from the policy itself or accepted as an explicit operator declaration — and compare it against the active adapter's declared capabilities at startup. When the requirement is declared rather than derived, the system MUST additionally verify it at runtime: the first verdict the adapter cannot enforce MUST deny the action and fail loudly, so an incorrect declaration cannot silently under-enforce.
- **FR-028**: System MUST fail startup with a specific error naming the exact missing capability when the policy requires enforcement the adapter does not provide.
- **FR-029**: System MUST NOT downgrade to an observation-only mode when a capability is missing.
- **FR-030**: System MUST prove each declared capability against the real runtime in an automated conformance suite, rather than asserting declared values against themselves.
- **FR-031**: System MUST run an agent with no measurable behavior change when registered with no providers configured.
- **FR-032**: System MUST provide an explicit, configurable mechanism by which the integrating application supplies each identity and context field the policy authorizes against — acting agent, human principal, task, target resource, context trust, and context provenance. Each mechanism MUST have a defined fallback when the application supplies nothing, and no fallback may be more permissive than "unknown".

### Key Entities *(include if feature involves data)*

- **Action Intent**: A single proposed tool call, captured before execution. Carries identity (agent, user), purpose (task), the action (tool, arguments, resource), the trust and provenance of the context that influenced it, and the correlation identifiers linking it to the surrounding run.
- **Evidence**: A structured observation about an action intent, produced by a named source, identifying the signal, its value, and optionally a confidence. Never a verdict.
- **Verdict**: One of allow, deny, or review as an enforceable authorization outcome; plus abstain, which an evidence source may express to mean "no opinion" and which is never an authorization outcome.
- **Control Result**: The authorization outcome for one action intent: the verdict, the provider that produced it, a human-readable reason, the evidence considered, and the identifier of the policy rule that fired.
- **Capability Manifest**: A runtime adapter's explicit declaration of what it can observe, intercept, block, modify, and hold for approval. The input to startup validation.
- **Governance Record**: The exported, correlated, durable representation of one decision, readable in the organization's existing observability backend and sufficient on its own to explain the decision.
- **Review Hold**: A suspended run awaiting a human decision, with its originating intent, its deadline, and its eventual resolution (approved, rejected, or timed out).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tool calls attempted by a governed agent produce an authorization decision before execution; zero tool executions occur without a corresponding decision record.
- **SC-002**: Governance adds no more than 400 milliseconds at the 95th percentile to a tool call, and no more than 500 milliseconds at the 99th percentile, under a reachable policy service.
- **SC-003**: When the policy service is unavailable, 100% of tool calls are denied and 100% of those denials are labeled as unavailability rather than policy denial; zero tools execute.
- **SC-004**: A reviewer unfamiliar with the codebase can correctly explain who attempted what and why it was blocked, for 5 out of 5 sampled denials, using only the observability backend.
- **SC-005**: A configuration pairing a policy that requires blocking or approval with a runtime that cannot provide it fails to start 100% of the time, and the error names the specific missing capability in 100% of cases.
- **SC-006**: While the governing process is running, an unanswered review hold resolves to denial within 30 seconds of its configured window expiring, in 100% of trials.
- **SC-006a**: An expired review hold never resolves to approval, in 100% of trials, including trials where the process is stopped for longer than the window and the hold is answered afterwards. A hold whose process stops and never resumes remains pending and un-executed; actively resolving such holds is deferred to v0.2.
- **SC-007**: With no providers configured, a governed agent produces outputs identical to the same ungoverned agent and adds under 5 milliseconds per tool call.
- **SC-008**: 100% of declared capability-manifest fields are covered by a conformance test that exercises the real runtime.
- **SC-009**: The same exported governance record is readable, with all governance fields visible, in at least two independent observability backends that were not modified for this product.
- **SC-010**: Changing an authorization cutoff requires editing only the versioned policy — zero application code or component configuration changes — verified by changing one cutoff end to end.

## Assumptions

**Scope boundaries taken from the build plan**

- Exactly one agent runtime adapter is in scope for v0.1 (LangGraph). Additional adapters (OpenAI Agents SDK, Google ADK, CrewAI, AutoGen) are out of scope.
- Exactly one policy provider is in scope for v0.1 (Open Policy Agent, called synchronously over its REST interface). Cedar and other providers are out of scope.
- The telemetry substrate is OpenTelemetry, using the GenAI semantic conventions plus an `agentcontrol.*` extension namespace, exported over OTLP. The GenAI conventions are pre-stable, so their package version is pinned and the pin is recorded as a dependency.
- Evidence sources and post-hoc analyzers exist as interfaces only in v0.1. No concrete plugin (Presidio, NeMo Guardrails, Guardrails AI, DeepEval, Ragas) is built. The evidence set is therefore empty at runtime in v0.1, and policies must behave correctly with absent evidence.
- No user interface, no CLI trajectory viewer, and no automated policy generation are in scope. The existing observability backend is the review surface.
- No risk score mutates live authorization. Adaptive enforcement is explicitly not built in v0.1.

**Defaults chosen where the description did not specify**

- Trust level and provenance of context (`trusted` / `untrusted` / `unknown`) are supplied by the integrating application through agent state or control-plane configuration. When the application supplies nothing, the value is `unknown` — never silently `trusted` — and the policy decides what `unknown` means.
- A human resolves a review hold programmatically, through the SDK's resume path against the persisted run, since no user interface is in scope. Notification and approval routing are the integrator's responsibility in v0.1.
- Run suspension and resumption rely on the agent runtime's own persistence mechanism; durable review holds therefore require the integrator to configure durable persistence, not in-memory persistence.
- The policy provider is reachable over the network from the agent process and is operated by the same organization; mutual authentication to it is a deployment concern, not a v0.1 feature.
- Every decision, including allow, is exported. Sampling is not applied to governance records in v0.1.
- The response deadline (300 ms), the review window (15 minutes), and the failure mode (closed) are the shipped defaults; all three are overridable, with the review window overridable only through policy.
- The delivered artifact is a Python SDK library embedded in the agent process, not a separately deployed network service.

**Dependencies**

- Requires a reachable Open Policy Agent instance with decision logging enabled, so that each verdict carries a decision identifier usable as the audit reference.
- Requires an OTLP-compatible collector or backend (Langfuse and Arize Phoenix are the two verified in v0.1).
- Requires the agent runtime's middleware hook for wrapping tool calls, and its checkpointer-based interrupt mechanism for review holds.
- The project constitution at `.specify/memory/constitution.md` is an unfilled template; no project-specific principles were available to constrain this specification.

**Source of truth for upstream behavior**

Planning and implementation MUST resolve any uncertainty about a third-party interface against real sources, in this order, before guessing or inventing an API:

1. **`refs/`** — vendored read-only clones of the upstream projects, already present in this repo and gitignored. Currently: `refs/langchain`, `refs/langgraph`, `refs/opa`, `refs/opentelemetry-python`, `refs/semantic-conventions-genai`, `refs/langsmith-sdk`, `refs/Guardrails`, `refs/deepeval`. Read the actual source and tests there — hook signatures, Rego builtins, attribute constants, and interrupt/resume semantics are all verifiable rather than assumable.
2. **LangChain docs MCP server** (`docs-langchain`, configured in `.mcp.json`) plus the LangChain API-reference MCP server (`reference-langchain`) for LangChain/LangGraph questions — current published docs and exact symbol signatures.
3. The documentation URLs listed in `plan.md` §12 for anything the first two do not cover.

Any planning artifact that specifies a third-party call signature, attribute name, or protocol field MUST cite where it was verified (`refs/<path>:<line>` or the MCP/doc source). Unverified upstream details are a planning defect, not an implementation detail to be settled later.
