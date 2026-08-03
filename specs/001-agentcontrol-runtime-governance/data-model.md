# Phase 1 Data Model: AgentControl v0.1

**Date**: 2026-08-04 | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

All types live in `agentcontrol/core/types.py` unless noted. Field names follow root `plan.md` §5 exactly where it defined them; additions are marked **(new)** with the requirement that forced them.

## Verdict

```python
class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"
    ABSTAIN = "abstain"   # evidence-collector opinion only; never an authorization outcome
```

**Invariants**
- Only `ALLOW`, `DENY`, `REVIEW` may appear in a `ControlResult` (FR-007). `ABSTAIN` in a `ControlResult` is a programming error and raises.
- Any value from the policy provider outside `{allow, deny, review}` is a provider failure → `DENY` + `policy.unavailable=true` (FR-009).

## ActionIntent

One proposed tool call, captured before execution. Immutable.

| Field | Type | Source | Notes |
|---|---|---|---|
| `agent_id` | `str` | config or `runtime.context` | required; falls back to the configured default agent id |
| `user_id` | `str \| None` | `runtime.context` / state | `None` when the host supplies no principal |
| `task` | `str \| None` | state | free-form task label |
| `tool` | `str` | `request.tool_call["name"]` | required |
| `arguments` | `dict` | `request.tool_call["args"]` | passed to OPA in full; **not** put on the span by default (research R4) |
| `resource` | `str \| None` | `resource_resolver` config hook | the thing being acted on, e.g. `company/production` |
| `context_trust` | `str \| None` | state / config | `trusted` \| `untrusted` \| `unknown`; **defaults to `unknown`, never `trusted`** |
| `context_source` | `str \| None` | state / config | provenance label, e.g. `sharepoint_document` |
| `trace_id` | `str` | ambient OTel span context | 32-char lowercase hex; all-zero when orphaned |
| `span_id` | `str` | ambient OTel span context | 16-char lowercase hex |
| `tool_call_id` | `str \| None` **(new)** | `request.tool_call["id"]` | needed for `gen_ai.tool.call.id` and as half of the review idempotency key |
| `thread_id` | `str \| None` **(new)** | `runtime.config["configurable"]["thread_id"]` | the other half of the review key; required whenever a policy can return `review` |
| `tool_type` | `str \| None` **(new)** | derived | `function` \| `extension` \| `datastore` for `gen_ai.tool.type` |

**Validation**
- `tool` and `agent_id` non-empty; construction fails loudly rather than sending a half-built intent to OPA.
- `context_trust`, when present, must be one of `trusted` / `untrusted` / `unknown`. An unrecognized value is coerced to `unknown` and flagged on the span — the policy decides what `unknown` means (spec Assumptions).
- `thread_id` absent while the policy can return `review` is a startup-time capability failure, not a runtime surprise: without it a hold cannot be resumed.

## Evidence

```python
@dataclass
class Evidence:
    collector: str          # e.g. "presidio"
    signal: str             # e.g. "pii_detected"
    value: Any
    confidence: float | None = None
```

**Invariants**
- Evidence never carries a verdict. A collector returning something verdict-shaped is still just data (locked decision §4.4).
- `confidence`, when present, is in `[0.0, 1.0]`.
- Collectors are **empty in v0.1**. The OPA input therefore has `"evidence": {}`, and policies must behave correctly against absent signals — the example bundle uses safe defaults for exactly this reason.
- A collector that raises or exceeds its budget yields **no** entry for that signal. It is never defaulted to a passing value (spec edge case).

## ControlResult

```python
@dataclass
class ControlResult:
    verdict: Verdict
    provider: str                       # "opa"
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    policy_id: str | None = None        # rule identifier from the policy result
    decision_id: str | None = None      # (new) OPA audit id; None without decision logging (R5)
    unavailable: bool = False           # (new) FR-010: distinguishes fallback from real denial
    fail_mode_applied: str | None = None  # (new) "closed" | "open" when unavailable is True
    review_timeout_seconds: int | None = None  # (new) FR-016: window comes from policy
```

**Invariants**
- `unavailable=True` ⇒ `verdict` is `DENY` under `fail_mode: closed`, or `ALLOW` under the explicit `fail_mode: open` override, and `fail_mode_applied` is set (FR-009, FR-011).
- `unavailable=True` ⇒ `policy_id` is `None`; a fallback did not fire a rule, and pretending otherwise would poison the audit trail.
- `verdict == REVIEW` ⇒ `review_timeout_seconds` is set, defaulting to 900 if the policy omits it.
- `reason` is always non-empty — including for `ALLOW`, where it names the rule that permitted the call.

## ReviewHold **(new — FR-014…FR-018)**

The persisted state of a paused decision. Lives inside the `interrupt()` payload, so it is stored in the host's checkpointer, not by AgentControl.

| Field | Type | Notes |
|---|---|---|
| `hold_id` | `str` | `f"{thread_id}:{tool_call_id}"` — the idempotency key |
| `intent` | `ActionIntent` (serialized) | what is being approved |
| `result` | `ControlResult` (serialized) | the `REVIEW` verdict and its reason |
| `deadline` | `str` | absolute ISO-8601 UTC. **Durable** — this is what makes the timeout survive restarts |
| `requested_at` | `str` | ISO-8601 UTC |

**State machine**

```
                     policy -> REVIEW
                            |
                            v
                       [ PENDING ] ---- deadline passes, then any resume ----> [ TIMED_OUT ] -> DENY
                            |
              human resumes before deadline
                            |
               +------------+------------+
               v                         v
        [ APPROVED ] -> tool runs   [ REJECTED ] -> DENY
```

**Invariants**
- Deadline is checked against the value **read back from the persisted payload**, never a value held in memory. A restart cannot extend a window.
- `PENDING → APPROVED` is impossible once `now() > deadline`, whatever the resume value says (FR-018). Expiry always wins.
- `TIMED_OUT` is recorded distinctly from `REJECTED` on the span (FR-017).
- Terminal states are terminal: a second resume against a resolved hold returns the recorded outcome and does not re-authorize.

## CapabilityManifest

The eleven fields from root `plan.md` §5, unchanged in name and meaning. Values and evidence: [contracts/capability-manifest.md](./contracts/capability-manifest.md).

**RequiredCapabilities (new — FR-027)**: the mirror type describing what the loaded policy demands.

```python
@dataclass(frozen=True)
class RequiredCapabilities:
    block_before_tool: bool = True   # any policy that can deny
    human_approval: bool = False     # any policy that can return review
    modify_tool_arguments: bool = False
```

**Startup rule**: for every field set `True` in `RequiredCapabilities`, the adapter's manifest must also be `True`. Any gap raises `CapabilityMismatchError` naming the exact field, the adapter, and what the policy needs (FR-028). There is no downgrade path (FR-029).

## GovernanceSpan (projection, not a stored type)

The exported record. One per decision (FR-019), except for the documented replay case in research R3. Full attribute list: [contracts/otel-attributes.md](./contracts/otel-attributes.md).

**Invariants**
- Always exported, never sampled away (research R8).
- Carries verdict, provider, rule id, unavailability flag, context trust, context provenance, every evidence signal, and whether the adapter could actually block (FR-022).
- Tool arguments and results are **opt-in** and absent by default (research R4).

## Relationships

```
ActionIntent 1 ── 0..n Evidence
ActionIntent 1 ── 1   ControlResult
ControlResult 1 ── 0..1 ReviewHold        (only when verdict == REVIEW)
ControlResult 1 ── 1..2 GovernanceSpan    (2 = pending + resolution, for held calls)
FrameworkAdapter 1 ── 1 CapabilityManifest
ControlPlaneConfig 1 ── 1 RequiredCapabilities
```

## Configuration

`agentcontrol/core/config.py`:

| Setting | Default | Constraint |
|---|---|---|
| `policy.url` | — | required when a policy provider is configured |
| `policy.path` | `agentcontrol/authz` | Rego package path under `/v1/data/` |
| `policy.timeout_ms` | `300` | locked decision §4.5 |
| `policy.fail_mode` | `closed` | `open` is explicit per deployment and logged on every use (FR-011) |
| `review.default_timeout_seconds` | `900` | fallback only; policy value wins (FR-016) |
| `review.watchdog_enabled` | `True` | in-process liveness only (research R3) |
| `telemetry.endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP |
| `telemetry.record_tool_arguments` | `False` | Opt-In upstream; may contain sensitive data |
| `telemetry.record_tool_results` | `False` | same |
| `defaults.context_trust` | `unknown` | may be raised per deployment, never silently |

**No threshold that affects an authorization outcome may be added to this table** (FR-013). `review.default_timeout_seconds` is the boundary case and is deliberately a *fallback for a silent policy*, never an override of one.
