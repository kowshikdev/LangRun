# Configuration

`ControlPlaneConfig` and its nested sections, from `agentcontrol/core/config.py`. Mirrors [data-model.md's Configuration table](../specs/001-agentcontrol-runtime-governance/data-model.md#configuration).

**No setting here may change an authorization outcome.** Configuration controls how a signal is produced; the versioned Rego policy bundle controls the consequence of that signal (Constitution Principle V). If you find yourself wanting a config key to change what gets allowed or denied, that value belongs in `policies/tool_authorization.rego`, reviewed like any other policy change — not here.

## `PolicyConfig`

| Setting | Default | Notes |
|---|---|---|
| `url` | `""` | Required once a policy provider is configured. |
| `path` | `"agentcontrol/authz"` | Rego package path under `/v1/data/`. |
| `timeout_ms` | `300` | Per-request deadline. Exceeding it is a provider failure (fail-closed), not a policy denial. |
| `fail_mode` | `"closed"` | `"closed"` denies on provider failure; `"open"` allows and is logged loudly on every use. Set `"open"` only with an explicit, deliberate per-deployment decision. |

## `ReviewConfig`

| Setting | Default | Notes |
|---|---|---|
| `default_timeout_seconds` | `900` | Fallback **only** when the policy's `review_timeout_seconds` is silent. Never overrides a value the policy actually returned. |
| `watchdog_enabled` | `True` | In-process auto-resolution of expired holds while the process is alive. |
| `watchdog_poll_seconds` | `5.0` | How often the watchdog checks for expired holds. |

## `TelemetryConfig`

| Setting | Default | Notes |
|---|---|---|
| `endpoint` | `None` | Falls back to `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_ENDPOINT`. |
| `service_name` | `"agentcontrol"` | Only used when `ControlPlane(own_tracer_provider=True)`. |
| `record_tool_arguments` | `False` | Opt-in. Upstream (`gen_ai.tool.call.arguments`) flags this as possibly sensitive. A denial stays fully explainable without it. |
| `record_tool_results` | `False` | Same, for `gen_ai.tool.call.result`. |
| `enabled` | `True` | — |

## `ContextResolvers`

How the integrating application supplies each field the policy authorizes against. Every field has an explicit producer and a defined fallback that is never more permissive than "unknown" (FR-032).

| Field | State key checked first | Fallback |
|---|---|---|
| `agent_id` | `agentcontrol_agent_id` | `default_agent_id` (`"unknown-agent"`) |
| `user_id` | `agentcontrol_user_id` | `None` |
| `task` | `agentcontrol_task` | `None` |
| `resource` | `agentcontrol_resource` | `None` |
| `context_trust` | `agentcontrol_context_trust` | `"unknown"` — never `"trusted"` |
| `context_source` | `agentcontrol_context_source` | `None` |

Set values by putting the state key in your agent's graph state, or by passing a callable resolver (`ContextResolvers(resource=lambda ctx: ...)`) — the callable receives `{"tool_call", "tool", "state", "runtime", "config"}`. A resolver that raises is treated as returning nothing; it can never fabricate a value the policy will authorize against.

## `ControlPlaneConfig`

| Setting | Default | Notes |
|---|---|---|
| `inline_control_budget_ms` | `50` | Hot-path budget for concurrent `InlineControl` evidence collection. A collector that exceeds it drops its signal — never defaulted to a passing value. |
| `strict_policy_scan` | `False` | Optional `opa eval` cross-check of `required_capabilities` against the loaded bundle. Off by default; needs the `opa` binary. |
| `required_capabilities` | `RequiredCapabilities(block_before_tool=True)` | What the loaded policy demands of the adapter. Checked at `attach()`. |

## Environment variables

Only the standard OpenTelemetry ones are read directly: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`. AgentControl defines no `AGENTCONTROL_*` environment variables in v0.1 — all configuration goes through `ControlPlaneConfig`.
