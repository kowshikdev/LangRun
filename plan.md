# AgentControl — v0.1 build plan

## 0. How to use this document

This file is the build spec for a coding agent. It locks the architectural decisions
made during design so the agent doesn't need to re-derive them. Where a decision was
genuinely open, this doc picks a default rather than leaving it ambiguous — treat
anything under "Architecture decisions (locked)" as fixed unless a human overrides it
in writing. Build in the phase order given in section 11; do not start Phase 2 work
before Phase 1's acceptance criteria pass.

## 1. Mission

AgentControl is a runtime governance layer for AI agents. It intercepts an agent's
tool-call intent, enriches it with identity/context/provenance evidence, gets a
deterministic authorization decision, and preserves the entire decision as
interoperable OpenTelemetry telemetry — regardless of which agent framework, policy
engine, or observability backend the developer already uses.

It is not a new agent framework, tracer, guardrail library, or eval engine. It is the
layer that makes existing ones reason about the same execution and govern it together.

## 2. Non-goals for v0.1

- No custom telemetry format. Extend OpenTelemetry GenAI semantic conventions; do not
  invent a parallel schema.
- No UI, no CLI trajectory viewer. LangSmith/Langfuse already render traces.
- No automatic policy generation or deployment. Incidents produce *proposed* policy
  diffs for human review, never auto-merged, auto-deployed changes.
- No risk score that mutates live authorization automatically. Risk scores feed
  dashboards, eval datasets, and human-reviewed policy proposals only, unless a human
  has explicitly opted into a bounded, audited `adaptive_enforcement` mode (not built
  in v0.1).
- No multi-framework support yet. One framework adapter (LangGraph) is the entire
  v0.1 surface.
- No DeepEval, RAGAS, NeMo, Guardrails AI, Presidio integrations in v0.1. Evidence
  collectors and async analyzers are a plugin interface in v0.1; the plugins
  themselves are v0.2+.

## 3. v0.1 scope

**In scope**
- Python SDK with a `ControlPlane` entrypoint.
- One framework adapter: LangGraph, wired through `wrap_tool_call` middleware.
- One policy provider: OPA, called synchronously before tool execution.
- OpenTelemetry export of the full decision (`gen_ai.*` + `agentcontrol.*` attributes)
  to any OTLP-compatible backend (Langfuse and Phoenix both accept this natively).
- Capability manifest system: adapters declare what they can intercept; startup
  validation fails loudly if the configured policy requires enforcement the adapter
  can't provide.
- Fail-closed behavior when the policy engine is unreachable.

**Out of scope (v0.2+, do not build now)**
- Additional framework adapters (OpenAI Agents SDK, Google ADK, CrewAI, AutoGen).
- Additional policy providers (Cedar).
- Evidence collector plugins (Presidio, NeMo Guardrails, Guardrails AI, LLM Guard).
- Async analyzer plugins (DeepEval, RAGAS).
- The incident → regression-test → eval-case → policy-proposal automation loop.

## 4. Architecture decisions (locked)

1. **Telemetry substrate.** Every event rides on OpenTelemetry spans using standard
   `gen_ai.*` attributes where they exist, extended with a private `agentcontrol.*`
   attribute namespace for governance-specific fields. Do not build an independent
   event schema.
2. **Correlation.** Use the W3C trace context (`trace_id` / `span_id`) that OTel
   already propagates as the join key across the agent trace, the OPA decision log,
   and any evidence collector output. Do not mint a separate run ID.
3. **Hot path vs. async path.** Policy checks and any evidence collectors registered
   as `InlineControl` run synchronously and can block execution. Anything registered
   as `AsyncAnalyzer` (LLM-judge evals, trajectory analysis) runs after the span is
   exported and never blocks the live tool call.
4. **Verdict model.** Evidence collectors never make the allow/deny decision
   themselves. They produce structured evidence. The policy provider (OPA) is the
   single place a request is authorized, denied, or sent to review. This avoids
   undefined behavior from multiple providers independently voting.
5. **Fail-closed default.** If the policy provider is unreachable or times out
   (default timeout: 300ms), the verdict is `DENY` and the failure is logged as a
   distinct `agentcontrol.policy.unavailable` event, not silently conflated with a
   normal policy denial. A fail-open override exists but must be explicitly set per
   deployment (`policy.fail_mode: open`) and is logged loudly on every use.
6. **REVIEW has a timeout.** A `REVIEW` verdict pauses the LangGraph run via its
   interrupt/checkpoint mechanism. Default timeout is 15 minutes, after which it
   resolves to `DENY` unless a human has acted. This is configurable in policy, not
   in code.
7. **Thresholds live in policy, not provider config.** Any numeric threshold that
   affects an authorization outcome (e.g. "block if injection score > 0.8") is
   defined in the Rego policy bundle, versioned and reviewed the same way as any
   other policy change. Provider config controls how a signal is produced, never the
   cutoff for what happens because of it.
8. **Capability manifests are declared and validated, not assumed.** Every framework
   adapter ships a manifest (section 5). At startup, `ControlPlane` checks the
   manifest against the configured policy's enforcement requirements. A mismatch is
   a hard startup failure with a specific error, never a silent downgrade to
   observation-only.

## 5. Core interfaces

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Protocol, Any


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"
    ABSTAIN = "abstain"  # evidence collector has no opinion; not a policy outcome


@dataclass
class Evidence:
    collector: str          # e.g. "presidio", "nemo_injection"
    signal: str              # e.g. "pii_detected", "prompt_injection_score"
    value: Any
    confidence: float | None = None


@dataclass
class ControlResult:
    verdict: Verdict
    provider: str             # which PolicyProvider produced this
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    policy_id: str | None = None   # Rego package/rule that fired, for audit


class InlineControl(Protocol):
    """Runs in the hot path. Must be fast (target: <50ms) or it blocks execution."""
    async def collect(self, event: "ActionIntent") -> Evidence: ...


class AsyncAnalyzer(Protocol):
    """Runs after the span is exported. Never blocks the live tool call."""
    async def analyze(self, trajectory_ref: str) -> None: ...


class PolicyProvider(Protocol):
    async def authorize(self, event: "ActionIntent", evidence: list[Evidence]) -> ControlResult: ...


@dataclass
class ActionIntent:
    agent_id: str
    user_id: str | None
    task: str | None
    tool: str
    arguments: dict
    resource: str | None
    context_trust: str | None      # e.g. "trusted" | "untrusted" | "unknown"
    context_source: str | None     # provenance: where did this intent originate
    trace_id: str
    span_id: str


@dataclass
class CapabilityManifest:
    observe_model_calls: bool
    observe_tool_calls: bool
    intercept_model_input: bool
    intercept_model_output: bool
    intercept_function_tools: bool
    intercept_mcp_tools: bool
    intercept_hosted_tools: bool
    block_before_tool: bool        # required=True if any policy has enforcement mode
    modify_tool_arguments: bool
    human_approval: bool           # required=True if any policy can return REVIEW
    streaming_interception: bool


class FrameworkAdapter(Protocol):
    capabilities: CapabilityManifest
    def wrap(self, agent: Any) -> Any: ...
```

## 6. OpenTelemetry attribute schema

Use standard attributes wherever the GenAI semantic conventions define them:
`gen_ai.operation.name`, `gen_ai.tool.name`, `gen_ai.agent.*`, `gen_ai.evaluation.score.*`.
These are pre-stable and may rename between OTel releases — pin the semantic
conventions package version and note it in `requirements.txt`.

AgentControl extension attributes (namespaced, stable within this project regardless
of upstream GenAI convention churn):

| Attribute | Type | Meaning |
|---|---|---|
| `agentcontrol.policy.decision` | string | `allow` \| `deny` \| `review` |
| `agentcontrol.policy.provider` | string | e.g. `opa` |
| `agentcontrol.policy.id` | string | policy/rule identifier that fired |
| `agentcontrol.policy.unavailable` | bool | true if this decision used the fail-mode fallback |
| `agentcontrol.context.trust` | string | trust level of the context that influenced this action |
| `agentcontrol.context.source` | string | provenance of that context |
| `agentcontrol.evidence.*` | varies | one attribute per evidence collector signal, namespaced by collector |
| `agentcontrol.capability.enforcement` | bool | whether the active adapter could actually block this call |

## 7. OPA contract

**Input (sent to OPA on every tool-call intent):**

```json
{
  "agent": { "id": "finance-agent" },
  "user": { "id": "kowshik" },
  "task": "analyze-quarterly-report",
  "action": { "tool": "github.delete_repository", "resource": "company/production" },
  "context": { "trust": "untrusted", "source": "sharepoint_document" },
  "evidence": {
    "presidio": { "pii_detected": false },
    "nemo_injection": { "score": 0.91 }
  }
}
```

**Example policy (`policies/tool_authorization.rego`):**

```rego
package agentcontrol.authz

default decision := "allow"

decision := "deny" if {
    input.evidence.nemo_injection.score > 0.8
    input.context.trust == "untrusted"
}

decision := "deny" if {
    input.action.tool == "github.delete_repository"
}

decision := "review" if {
    input.action.resource == "company/production"
    input.action.tool != "github.delete_repository"
}
```

**Output:** OPA's standard decision response, mapped by AgentControl into a
`ControlResult`. Enable OPA decision logging so every verdict has a `decision_id`
that gets attached to the OTel span as `agentcontrol.policy.id`.

## 8. LangGraph adapter

**Capability manifest for the v0.1 LangGraph adapter** (fill in as implemented —
this is the target, verify each field against actual `wrap_tool_call` behavior
before shipping, don't just assume all `True`):

```python
LANGGRAPH_CAPABILITIES = CapabilityManifest(
    observe_model_calls=True,
    observe_tool_calls=True,
    intercept_model_input=True,
    intercept_model_output=True,
    intercept_function_tools=True,
    intercept_mcp_tools=True,       # verify: MCP tool calls go through the same ToolNode
    intercept_hosted_tools=False,   # verify per LangGraph version
    block_before_tool=True,
    modify_tool_arguments=True,
    human_approval=True,            # via checkpointer + interrupt
    streaming_interception=False,   # verify
)
```

**Wiring (target shape, using `wrap_tool_call`):**

```python
from langchain.agents.middleware import wrap_tool_call

@wrap_tool_call
async def agentcontrol_authorize(request, handler):
    intent = build_action_intent(request)          # section 5 ActionIntent
    evidence = await collect_evidence(intent)        # registered InlineControls
    result = await policy_provider.authorize(intent, evidence)
    emit_otel_span(intent, evidence, result)          # section 6 attributes
    if result.verdict == Verdict.DENY:
        return deny_response(result)
    if result.verdict == Verdict.REVIEW:
        return await pause_for_review(request, result, timeout_s=900)
    return await handler(request)                     # ALLOW: proceed to tool
```

**Startup validation:** before the graph runs, compare the loaded policy bundle's
required enforcement (does any rule return `deny`/`review`, implying blocking is
required?) against `LANGGRAPH_CAPABILITIES.block_before_tool` and
`.human_approval`. Raise a hard error naming the specific gap if unsupported —
do not start in a degraded, silently-observational mode.

## 9. Request lifecycle

1. Agent (inside LangGraph) decides to call a tool.
2. `wrap_tool_call` intercepts before execution.
3. `ActionIntent` is built from the request, current graph state, and trace context.
4. Registered `InlineControl` evidence collectors run in parallel (empty set in v0.1).
5. `ActionIntent` + evidence sent to OPA.
6. OPA returns a decision; wrapped into `ControlResult`.
7. Full result recorded as an OTel span with `gen_ai.*` and `agentcontrol.*`
   attributes, exported to the configured OTLP endpoint.
8. On `ALLOW`: handler proceeds, tool executes, result also recorded.
   On `DENY`: tool call short-circuited, denial reason returned to the agent.
   On `REVIEW`: graph interrupts via checkpointer; resumes on human decision or
   timeout-driven auto-deny.
9. (v0.2+) Async analyzers read the exported trace after the fact; nothing in v0.1
   blocks on this step.

## 10. Repo/project structure

```
agentcontrol/
  core/
    types.py            # Verdict, ControlResult, Evidence, ActionIntent, CapabilityManifest
    control_plane.py     # ControlPlane entrypoint, startup validation
    otel.py              # span construction, attribute mapping
  providers/
    policy/
      opa.py             # PolicyProvider implementation
    tracing/
      otlp.py             # generic OTLP exporter config
  adapters/
    langgraph/
      adapter.py          # FrameworkAdapter implementation, capability manifest
      middleware.py        # wrap_tool_call wiring
  policies/
    tool_authorization.rego
  tests/
    conformance/          # adapter capability-manifest conformance tests (section 11, Phase 4)
```

## 11. Milestones

**Phase 0 — scaffolding**
Repo structure above, `ControlPlane` skeleton that does nothing but validate config
and fail loudly on missing providers. Acceptance: `agentcontrol.ControlPlane()` with
no providers configured runs an unmodified LangGraph agent with zero behavior change.

**Phase 1 — interception**
`wrap_tool_call` wiring, `ActionIntent` construction, OTel span emission with
`gen_ai.*` attributes only (no policy yet — everything is `ALLOW`). Acceptance: every
tool call in a test LangGraph agent produces a correctly-attributed span in a local
OTel collector.

**Phase 2 — policy**
OPA integration, the example Rego policy, fail-closed behavior, `REVIEW` with
checkpointer-based pause/timeout. Acceptance: a scripted "untrusted context tries a
destructive action" scenario is denied; a scripted "OPA unreachable" scenario denies
by default and logs `agentcontrol.policy.unavailable=true`; a `REVIEW` case pauses
and resumes correctly on human input, and auto-denies after the configured timeout
when no input arrives.

**Phase 3 — telemetry completeness**
Full `agentcontrol.*` attribute set on every span, verified round-trip into both
Langfuse and Phoenix (confirm both render the governance attributes, not just the
base `gen_ai.*` fields). Acceptance: a denied action is visibly explainable from the
Langfuse/Phoenix UI alone, without reading application logs.

**Phase 4 — capability manifest conformance**
Test suite that exercises every `CapabilityManifest` field against the real
LangGraph adapter (not just asserts the declared values) — including the "verify"
items flagged in section 8. Acceptance: manifest values are proven, not assumed; a
deliberately misconfigured policy requiring an unsupported capability fails startup
with a specific, correct error message.

## 12. Documentation & repo appendix

Read the docs, don't just clone the repos — the middleware hook names, Rego syntax,
and OTel attribute names all matter for whether this compiles into something
correct rather than something that merely runs.

**LangGraph / LangChain (Phase 0–1, 4)**
- Repo: https://github.com/langchain-ai/langgraph
- Repo (parent framework): https://github.com/langchain-ai/langchain
- Middleware / `wrap_tool_call` reference: https://reference.langchain.com/python/langchain/agents/middleware/types/wrap_tool_call
- Middleware guide: https://docs.langchain.com/oss/python/langchain/middleware/custom
- Persistence / checkpointer (needed for `REVIEW` pause/resume):
  https://docs.langchain.com/oss/python/langgraph/persistence

**OPA (Phase 2)**
- Repo: https://github.com/open-policy-agent/opa
- Docs home / Rego language: https://www.openpolicyagent.org/docs
- REST API reference (for the sync authorize call): https://www.openpolicyagent.org/docs/rest-api
- Integration patterns: https://www.openpolicyagent.org/docs/integration
- Decision logging (for `policy_id` / audit trail): https://www.openpolicyagent.org/docs/monitoring
- Bundles (for how policy changes get distributed/versioned later):
  https://www.openpolicyagent.org/docs/management-bundles

**OpenTelemetry (Phase 1, 3)**
- Python SDK repo: https://github.com/open-telemetry/opentelemetry-python
- GenAI semantic conventions repo (attribute names, still pre-1.0 — pin a version):
  https://github.com/open-telemetry/semantic-conventions-genai
- GenAI attribute registry: https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/

**Tracing/eval backends (Phase 3)**
- Langfuse repo (self-hostable): https://github.com/langfuse/langfuse
- Langfuse OTel ingestion: https://langfuse.com/integrations/native/opentelemetry
- Langfuse LangGraph integration: https://langfuse.com/guides/cookbook/integration_langgraph
- Arize Phoenix: https://github.com/Arize-ai/phoenix

**MCP (context for `intercept_mcp_tools`, not core v0.1 but affects adapter verification)**
- Spec repo: https://github.com/modelcontextprotocol/modelcontextprotocol
- Authorization spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- Note: MCP authorization covers "can this client reach this server," scoped to
  remote/HTTP transports only, and is unevenly adopted. It is not a substitute for
  the action-level authorization this project provides — treat it as informational
  context on `context_trust`, not as a reason to skip the OPA check for MCP tools.

**v0.2+ evidence collector and async analyzer candidates (do not integrate yet, listed for later)**
- DeepEval: https://github.com/confident-ai/deepeval
- Ragas: https://github.com/explodinggradients/ragas
- Guardrails AI: https://github.com/guardrails-ai/guardrails
- NeMo Guardrails: https://github.com/NVIDIA-NeMo/Guardrails — **note: this project
  moved from `NVIDIA/NeMo-Guardrails` to the `NVIDIA-NeMo` org; use this URL, the old
  one may redirect or be stale.**
- Presidio (PII detection): https://github.com/microsoft/presidio
- Cedar (alternative policy provider): https://github.com/cedar-policy
- OpenAI Agents SDK guardrails (reference for a future adapter's capability
  manifest, since it does not treat all tool types identically):
  https://openai.github.io/openai-agents-python/guardrails/