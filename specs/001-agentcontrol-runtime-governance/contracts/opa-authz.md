# Contract: OPA authorization

**Verified against** `refs/opa@515aea3` — `v1/server/types/types.go:242-282`, `v1/server/server.go:2764-2768`, `v1/runtime/runtime.go:930-938`.

## Endpoint

```
POST {policy.url}/v1/data/{policy.path}
Content-Type: application/json
```

Default `policy.path` is `agentcontrol/authz`, i.e. the Rego package `agentcontrol.authz`. Timeout 300 ms (`policy.timeout_ms`).

## Request

`DataRequestV1` is `{"input": <any>}` (`types.go:242-243`). AgentControl sends:

```json
{
  "input": {
    "agent":   { "id": "finance-agent" },
    "user":    { "id": "kowshik" },
    "task":    "analyze-quarterly-report",
    "action":  {
      "tool": "github.delete_repository",
      "arguments": { "repo": "company/production" },
      "resource": "company/production",
      "tool_type": "function"
    },
    "context": { "trust": "untrusted", "source": "sharepoint_document" },
    "evidence": {},
    "trace":   { "trace_id": "4bf92f...", "span_id": "00f067...", "thread_id": "t-42" }
  }
}
```

Schema: [opa-input.schema.json](./opa-input.schema.json).

Notes:
- `evidence` is `{}` in v0.1 — no collectors ship. Policies **must** tolerate absent signals.
- `action.arguments` is sent in full so policies can inspect them, independent of whether they are recorded on the span.
- `trace` lets the OPA decision log be joined to the OTel trace on the same key (locked decision §4.2).

## Response

`DataResponseV1` (`types.go:267-282`):

```json
{
  "decision_id": "b1f2...",
  "result": {
    "decision": "deny",
    "reason": "destructive tool blocked for untrusted context",
    "policy_id": "agentcontrol.authz.deny_destructive_untrusted",
    "review_timeout_seconds": 900
  }
}
```

Schema: [opa-result.schema.json](./opa-result.schema.json).

| Field | Required | Maps to |
|---|---|---|
| `result.decision` | yes | `ControlResult.verdict` — must be `allow` \| `deny` \| `review` |
| `result.reason` | yes | `ControlResult.reason` |
| `result.policy_id` | yes | `ControlResult.policy_id` → `agentcontrol.policy.id` |
| `result.review_timeout_seconds` | when `decision == "review"` | `ControlResult.review_timeout_seconds` |
| `decision_id` | no | `ControlResult.decision_id` → `agentcontrol.policy.decision_id` |

**`decision_id` is conditional.** OPA emits it only when the decision-log plugin is configured; without one the server's id factory returns `""` (`server.go:2764-2768`, `runtime.go:930-938`). This is why the rule identifier is carried in `result.policy_id` rather than being read off `decision_id` as root `plan.md` §7 proposed — otherwise FR-022 fails on any OPA started without decision logging. Startup warns loudly when the probe response omits it.

**Why an object, not a bare string.** FR-013 forbids authorization-affecting numbers outside the bundle and FR-016 requires the review window to be policy-configurable, so the window must travel with the decision. A bare `decision := "allow"` also leaves no place for a reason or a rule id.

## Failure mapping (FR-009, FR-010)

Every row below produces `unavailable=True`, `agentcontrol.policy.unavailable=true`, and the `fail_mode` verdict (`DENY` closed, `ALLOW` open):

| Condition | Detected as |
|---|---|
| connection refused / DNS failure | `httpx.ConnectError` |
| no response within `timeout_ms` | `httpx.TimeoutException` |
| HTTP status outside 2xx | status check |
| body is not JSON | `json.JSONDecodeError` |
| `result` key absent | **undefined Rego document — OPA returns 200 with no `result`** |
| `result.decision` missing or not in `{allow, deny, review}` | validation |

The `result`-absent row is the dangerous one: an undefined or misnamed rule returns a *successful* 200, and reading that as allow would silently disable enforcement. It is treated as provider failure.

## Example bundle — `policies/tool_authorization.rego`

Rewritten from root `plan.md` §7 to return the result object, and to be safe against the empty v0.1 evidence set.

```rego
package agentcontrol.authz

import rego.v1

default result := {
	"decision": "allow",
	"reason": "no rule matched; default allow",
	"policy_id": "agentcontrol.authz.default_allow",
	"review_timeout_seconds": 900,
}

# Thresholds live here, never in provider config (FR-013).
injection_block_threshold := 0.8

review_window_seconds := 900

result := {
	"decision": "deny",
	"reason": sprintf("injection score %.2f exceeds %.2f for untrusted context", [score, injection_block_threshold]),
	"policy_id": "agentcontrol.authz.deny_injection_untrusted",
	"review_timeout_seconds": review_window_seconds,
} if {
	score := object.get(input, ["evidence", "nemo_injection", "score"], 0)
	score > injection_block_threshold
	input.context.trust == "untrusted"
}

result := {
	"decision": "deny",
	"reason": "destructive tool is blocked unconditionally",
	"policy_id": "agentcontrol.authz.deny_destructive_tool",
	"review_timeout_seconds": review_window_seconds,
} if {
	input.action.tool == "github.delete_repository"
}

result := {
	"decision": "review",
	"reason": "write against a production resource requires human approval",
	"policy_id": "agentcontrol.authz.review_production_resource",
	"review_timeout_seconds": review_window_seconds,
} if {
	input.action.resource == "company/production"
	input.action.tool != "github.delete_repository"
}
```

Differences from root `plan.md` §7 and why:
- `object.get(..., 0)` instead of `input.evidence.nemo_injection.score` — with no collectors in v0.1 that path is undefined, and an undefined comparison makes the whole rule undefined rather than false. The default is `0`, which is safe (no injection signal ⇒ no injection-based denial), and the trust check still applies.
- Returns the result object described above.
- `import rego.v1` — future-proof syntax; `if` bodies already require it in the v1 dialect.

## Test obligations

- **Contract**: request body validates against `opa-input.schema.json`; every response the client accepts validates against `opa-result.schema.json`.
- **Integration (respx)**: each failure row above maps to `DENY` + `unavailable=True`.
- **Integration (live `opa run --server`)**: each example rule returns the expected decision; `decision_id` present with `--set decision_logs.console=true` and absent without it.
- **Policy unit tests**: `opa test policies/` covering allow, both denies, review, and the empty-evidence case.
