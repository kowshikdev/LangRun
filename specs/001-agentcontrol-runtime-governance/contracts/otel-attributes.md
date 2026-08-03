# Contract: OpenTelemetry span shape

**Verified against** `refs/semantic-conventions-genai@9af0834` (`docs/gen-ai/gen-ai-spans.md:1045-1120`, `model/gen-ai/registry.yaml`) and `refs/opentelemetry-python@cd298d5`.

## Convention pin

Conventions pinned at genai `9af0834`, which pins `SEMCONV_VERSION=v1.43.0` (`refs/semantic-conventions-genai/versions.env`).

The `gen_ai.*` constants are **vendored** in `agentcontrol/core/semconv.py`, not imported. Every gen_ai constant in `opentelemetry-semantic-conventions` 0.66b0 carries `"""Deprecated: Moved to the [OpenTelemetry GenAI semantic conventions repository]…"""` (`_incubating/attributes/gen_ai_attributes.py:240-285`, `:351-374`), and the successor repo publishes no Python package. Stable attributes (`error.type`, resource attributes) are still imported normally.

A drift test asserts the vendored strings against `refs/semantic-conventions-genai/model/gen-ai/registry.yaml` and skips when `refs/` is absent.

## Span identity

| Property | Value | Source |
|---|---|---|
| Name | `execute_tool {gen_ai.tool.name}` | `gen-ai-spans.md:1069` |
| Kind | `INTERNAL` | `gen-ai-spans.md:1071` |
| Status | `ERROR` on denial-by-failure and on tool exceptions; `OK` otherwise | recording-errors doc, referenced `:1073` |
| Sampling | always recorded, host sampler bypassed | FR-019 / research R8 |

One span per decision. Held calls produce two: `review.state=pending` and a resolution span linked to it (research R3).

## Standard `gen_ai.*` attributes

| Attribute | Requirement level upstream | AgentControl behavior |
|---|---|---|
| `gen_ai.operation.name` | Required | always `"execute_tool"` |
| `gen_ai.tool.name` | Required | always |
| `gen_ai.tool.call.id` | Recommended | always when the tool call carries an id |
| `gen_ai.tool.description` | Recommended | when the `BaseTool` exposes one |
| `gen_ai.tool.type` | Recommended | `function` \| `extension` \| `datastore` |
| `gen_ai.agent.name` | Conditionally required | when configured |
| `gen_ai.agent.id` | — | when configured |
| `gen_ai.conversation.id` | — | LangGraph `thread_id` when present |
| `error.type` | Conditionally required (stable semconv) | on failure |
| `gen_ai.tool.call.arguments` | **Opt-In** | **off by default** — upstream flags it as possibly sensitive |
| `gen_ai.tool.call.result` | **Opt-In** | **off by default** — same |

## AgentControl attributes

Namespaced so they stay stable regardless of upstream churn (locked decision §4.1, FR-021).

| Attribute | Type | Always? | Meaning |
|---|---|---|---|
| `agentcontrol.policy.decision` | string | yes | `allow` \| `deny` \| `review` |
| `agentcontrol.policy.provider` | string | yes | e.g. `opa` |
| `agentcontrol.policy.id` | string | when a rule fired | rule identifier from `result.policy_id` |
| `agentcontrol.policy.decision_id` | string | when decision logging on | OPA audit id (research R5) |
| `agentcontrol.policy.reason` | string | yes | human-readable reason |
| `agentcontrol.policy.unavailable` | bool | yes | `true` when the fail-mode fallback produced this verdict |
| `agentcontrol.policy.fail_mode` | string | when unavailable | `closed` \| `open` |
| `agentcontrol.policy.latency_ms` | int | yes | authorization round-trip, for SC-002 |
| `agentcontrol.context.trust` | string | yes | `trusted` \| `untrusted` \| `unknown` |
| `agentcontrol.context.source` | string | when known | provenance |
| `agentcontrol.capability.enforcement` | bool | yes | whether this adapter could actually block this call |
| `agentcontrol.action.resource` | string | when resolved | target resource |
| `agentcontrol.correlation.orphan` | bool | when true | no ambient trace context was available |
| `agentcontrol.evidence.{collector}.{signal}` | varies | per signal | one attribute per evidence signal; none in v0.1 |
| `agentcontrol.review.state` | string | review only | `pending` \| `approved` \| `rejected` \| `timed_out` |
| `agentcontrol.review.deadline` | string | review only | absolute ISO-8601 UTC |
| `agentcontrol.review.hold_id` | string | review only | `{thread_id}:{tool_call_id}` |
| `agentcontrol.review.replay` | bool | when true | duplicate pending span after a process restart (research R3) |

`timed_out` vs `rejected` is the distinction FR-017 requires; `unavailable` vs a plain `deny` is FR-010.

## Worked example — denied action

```
span  execute_tool github.delete_repository        kind=INTERNAL status=ERROR
  gen_ai.operation.name           = "execute_tool"
  gen_ai.tool.name                = "github.delete_repository"
  gen_ai.tool.call.id             = "call_a1b2"
  gen_ai.tool.type                = "function"
  gen_ai.agent.name               = "finance-agent"
  gen_ai.conversation.id          = "t-42"
  error.type                      = "agentcontrol.denied"
  agentcontrol.policy.decision    = "deny"
  agentcontrol.policy.provider    = "opa"
  agentcontrol.policy.id          = "agentcontrol.authz.deny_destructive_tool"
  agentcontrol.policy.decision_id = "b1f2c3d4-..."
  agentcontrol.policy.reason      = "destructive tool is blocked unconditionally"
  agentcontrol.policy.unavailable = false
  agentcontrol.policy.latency_ms  = 11
  agentcontrol.context.trust      = "untrusted"
  agentcontrol.context.source     = "sharepoint_document"
  agentcontrol.action.resource    = "company/production"
  agentcontrol.capability.enforcement = true
```

Everything SC-004 asks a reviewer to answer — who, what, against what, why blocked, which rule — is on the span. No tool arguments needed, which is what lets them stay off by default.

## Worked example — OPA unreachable

```
  agentcontrol.policy.decision    = "deny"
  agentcontrol.policy.unavailable = true
  agentcontrol.policy.fail_mode   = "closed"
  agentcontrol.policy.id          = <absent — no rule fired>
  agentcontrol.policy.reason      = "policy provider unreachable: ConnectError"
  error.type                      = "agentcontrol.policy_unavailable"
```

## Export

OTLP/HTTP via `opentelemetry-exporter-otlp-proto-http` + `BatchSpanProcessor`. Endpoint from `OTEL_EXPORTER_OTLP_ENDPOINT` or explicit config. No vendor SDK is a dependency — that is what makes FR-024 testable by pointing the same run at Langfuse and at Phoenix unchanged.

`ControlPlane` acquires a tracer from the ambient `TracerProvider` and bootstraps one only when explicitly asked. A library that hijacks the host's tracer provider is a library nobody adopts twice.

## Test obligations

- Unit: every attribute above produced for allow / deny / review / unavailable, asserted via `InMemorySpanExporter`.
- Unit: arguments and results absent by default; present only when the flags are set.
- Unit: drift test against `refs/semantic-conventions-genai/model/gen-ai/registry.yaml`.
- Integration: exactly one span per decision; exactly two for a held call, correctly linked.
- Integration (Phase 3): same trace rendered in Langfuse and Phoenix, governance attributes visible in both (SC-009).
