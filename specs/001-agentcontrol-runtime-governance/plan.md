# Implementation Plan: AgentControl v0.1 — Runtime Governance for AI Agents

**Branch**: `001-agentcontrol-runtime-governance` | **Date**: 2026-08-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-agentcontrol-runtime-governance/spec.md`, plus the locked architecture decisions in the repo-root [plan.md](../../plan.md).

## Summary

Deliver a Python library that inserts a deterministic authorization checkpoint in front of every LangGraph tool call, and records the whole decision as OpenTelemetry telemetry that any OTLP backend can render.

The technical approach, verified against the vendored upstream sources in `refs/`:

- Intercept via a **`AgentMiddleware` subclass implementing both `wrap_tool_call` and `awrap_tool_call`** — not the `@wrap_tool_call` decorator that the root `plan.md` §8 sketches. The decorator installs exactly one of the two hooks, so a decorator-based async middleware raises `NotImplementedError` under synchronous `invoke()`/`stream()`. (`refs/langchain/libs/langchain_v1/langchain/agents/middleware/types.py:2105-2157`, error text at `:732-742`.)
- Authorize by `POST /v1/data/agentcontrol/authz` against OPA with `{"input": …}`, mapping the response `result` object plus `decision_id` into a `ControlResult`. (`refs/opa/v1/server/types/types.go:242-282`.)
- Pause for human review with `langgraph.types.interrupt()` raised from inside the middleware, carrying an **absolute deadline in the interrupt payload** so the timeout survives process restarts. (`refs/langgraph/libs/langgraph/langgraph/types.py:811-899`.)
- Emit spans with hand-frozen `gen_ai.*` constants mirrored from the GenAI conventions repo — **not** imported from `opentelemetry.semconv._incubating.attributes.gen_ai_attributes`, whose entire gen_ai surface is now marked deprecated/moved. (`refs/opentelemetry-python/…/gen_ai_attributes.py:240-285`; canonical source `refs/semantic-conventions-genai/model/gen-ai/registry.yaml`.)
- Validate the adapter's capability manifest against the loaded policy's enforcement requirements at startup, and fail hard rather than degrade.

Three findings from source verification materially change the design versus the root plan; they are detailed in [research.md](./research.md) and summarized under [Deviations](#deviations-from-the-root-planmd).

## Technical Context

**Language/Version**: Python ≥3.10 (floor set by `langchain` 1.3.14 `requires-python = ">=3.10.0,<4.0.0"` and `langgraph` 1.2.10 `>=3.10`). Target and CI matrix 3.11–3.13.

**Primary Dependencies** (pinned; versions are the ones verified in `refs/`):

| Package | Pin | Why | Verified at |
|---|---|---|---|
| `langchain` | `1.3.14` | `AgentMiddleware`, `wrap_tool_call`/`awrap_tool_call`, `create_agent` | `refs/langchain@a6b904f` |
| `langgraph` | `1.2.10` | `interrupt()`, `Command(resume=…)`, checkpointer | `refs/langgraph@b2926a0` |
| `langgraph-prebuilt` | transitive | `ToolNode`, `ToolCallRequest` | `refs/langgraph/libs/prebuilt` |
| `langgraph-checkpoint-sqlite` | latest 2.x | durable review holds in dev/test | `refs/langgraph/libs/checkpoint-sqlite` |
| `httpx` | `>=0.27,<1` | async OPA client with per-request timeout | — |
| `opentelemetry-sdk` / `-api` | `1.45.x` | span creation, W3C context | `refs/opentelemetry-python@cd298d5` |
| `opentelemetry-exporter-otlp-proto-http` | `1.45.x` | OTLP export (Langfuse and Phoenix both ingest OTLP/HTTP) | `refs/opentelemetry-python/exporter/` |
| `opentelemetry-semantic-conventions` | `0.66b0` | **stable attributes only** (`error.type`, `service.*`); gen_ai constants are vendored, see research R4 | `refs/opentelemetry-python/…/semconv/version/__init__.py` |

**GenAI semantic conventions pin**: `semantic-conventions-genai` @ `9af0834`, which itself pins `SEMCONV_VERSION=v1.43.0` (`refs/semantic-conventions-genai/versions.env`). This repo publishes **no Python package** — it is YAML models, Weaver templates, and generated Markdown — so the attribute names are vendored as literal constants and covered by a drift test.

**Storage**: None owned by this library. Review holds live in the host application's LangGraph checkpointer (SQLite in dev/test, Postgres in production). Decision history lives in the OTLP backend and in OPA's decision log.

**Testing**: `pytest` + `pytest-asyncio`, `respx` (httpx transport mocking for OPA), `opentelemetry-sdk` `InMemorySpanExporter` for span assertions, `InMemorySaver` and `SqliteSaver` for interrupt/resume tests. Integration tests against a real `opa run --server` container are opt-in via marker; conformance tests (Phase 4) run a real `create_agent` graph.

**Target Platform**: Any platform running CPython ≥3.10; the library is embedded in the host agent process, not deployed as a service. Development is on Windows; CI must also run Linux because the OPA container path is Linux-only.

**Project Type**: Single installable Python library (SDK) plus a Rego policy bundle and example scripts.

**Performance Goals**: Governance overhead ≤400 ms p95 and ≤500 ms p99 per tool call (SC-002), which the 300 ms OPA deadline plus a 50 ms inline-collector budget fits inside. With no providers configured, ≤5 ms per tool call (SC-007).

**Constraints**:
- Fail-closed: any OPA transport error, timeout, non-2xx, or unparseable body yields `DENY` with `agentcontrol.policy.unavailable=true`.
- No numeric authorization cutoff may live outside the Rego bundle (FR-013) — including the review window, which OPA returns per decision.
- Exactly one governance span per decision (FR-019), which must hold across LangGraph's re-execution of an interrupted node.
- No custom event schema; `gen_ai.*` where defined, `agentcontrol.*` for everything else.

**Scale/Scope**: v0.1 is one adapter, one policy provider, zero shipped evidence collectors. Roughly 1,500–2,000 lines of library code, one Rego bundle, and a conformance suite covering 11 manifest fields.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status: VACANT — no gates enforced.**

`.specify/memory/constitution.md` is the unmodified Spec Kit template: every principle is still a `[PRINCIPLE_N_NAME]` / `[PRINCIPLE_N_DESCRIPTION]` placeholder, and the governance section is `[GOVERNANCE_RULES]`. There are no ratified principles to check this design against, so no gate can pass or fail on evidence.

This is recorded as a risk, not a pass. A governance product with no stated engineering constitution is a poor look, and three decisions in this plan (test-first for the conformance suite, fail-closed as a non-negotiable, no silent capability downgrade) are exactly the kind of thing a constitution should be pinning down rather than a plan asserting. **Recommendation: run `/speckit-constitution` before implementation begins**, seeding it with at least: fail-closed by default, no silent degradation, upstream interfaces verified against source before use, and every declared capability proven by an executable test.

Re-check after Phase 1: unchanged — still vacant, no violations recorded in Complexity Tracking because there is nothing to violate.

## Project Structure

### Documentation (this feature)

```text
specs/001-agentcontrol-runtime-governance/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — verified upstream interfaces + decisions
├── data-model.md        # Phase 1 output — entities, invariants, state machine
├── quickstart.md        # Phase 1 output — runnable validation scenarios
├── contracts/           # Phase 1 output
│   ├── python-api.md          # Public SDK surface
│   ├── opa-authz.md           # OPA request/response contract
│   ├── opa-input.schema.json  # JSON Schema for the OPA input document
│   ├── opa-result.schema.json # JSON Schema for the OPA result object
│   ├── otel-attributes.md     # Span shape: gen_ai.* + agentcontrol.*
│   └── capability-manifest.md # Manifest fields, required/provided semantics
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
agentcontrol/
├── __init__.py                 # exports ControlPlane, Verdict, and the core dataclasses
├── core/
│   ├── types.py                # Verdict, Evidence, ControlResult, ActionIntent,
│   │                           #   CapabilityManifest, RequiredCapabilities, protocols
│   ├── config.py               # ControlPlaneConfig, PolicyConfig (fail_mode, timeout_ms)
│   ├── errors.py               # CapabilityMismatchError, PolicyUnavailableError, …
│   ├── control_plane.py        # ControlPlane entrypoint + startup validation
│   ├── semconv.py              # vendored gen_ai.* constants + agentcontrol.* namespace
│   └── otel.py                 # span construction and attribute mapping
├── providers/
│   ├── policy/
│   │   ├── base.py             # PolicyProvider protocol + shared fail-mode handling
│   │   └── opa.py              # OPA REST client, decision_id capture, fail-closed
│   └── tracing/
│       └── otlp.py             # TracerProvider/OTLP exporter wiring helper
└── adapters/
    └── langgraph/
        ├── adapter.py          # FrameworkAdapter impl + LANGGRAPH_CAPABILITIES
        ├── middleware.py       # AgentControlMiddleware (sync + async hooks)
        ├── intent.py           # ToolCallRequest -> ActionIntent
        └── review.py           # interrupt payload, deadline, resume resolution

policies/
└── tool_authorization.rego     # example bundle returning the result object

tests/
├── unit/                       # types, config, intent building, attribute mapping
├── integration/                # OPA (respx + live container), end-to-end graph runs
└── conformance/                # capability-manifest proofs against a real graph

examples/
└── governed_agent.py           # the quickstart scenario as a runnable script

pyproject.toml                  # packaging, pins, pytest/ruff/mypy config
```

**Structure Decision**: Keeps the layout locked in root `plan.md` §10 verbatim (`agentcontrol/core|providers|adapters`, `policies/`, `tests/conformance/`) and adds only what packaging and validation require: `pyproject.toml`, `tests/unit`, `tests/integration`, `examples/`, plus two new modules inside the locked tree — `core/semconv.py` (forced by research R4, the deprecation of upstream Python gen_ai constants) and `adapters/langgraph/{intent,review}.py` (splitting intent construction and review-hold mechanics out of `middleware.py`, which would otherwise carry four unrelated responsibilities). A flat package at the repo root, rather than `src/`, matches the locked structure.

## Deviations from the root `plan.md`

Each is forced by something read in `refs/`, not by preference. Full evidence in [research.md](./research.md).

| # | Root plan says | Verified reality | Change |
|---|---|---|---|
| 1 | `@wrap_tool_call` decorator on an `async def` (§8) | The decorator installs `awrap_tool_call` **only**; the inherited sync `wrap_tool_call` then raises `NotImplementedError` telling the caller to use `astream`/`ainvoke` (`types.py:732-742`, `:2105-2133`) | Subclass `AgentMiddleware` and implement **both** hooks, so governed agents work under `invoke` and `ainvoke` alike |
| 2 | `pause_for_review(request, result, timeout_s=900)` returns a value inline (§8) | `interrupt()` raises `GraphInterrupt`; on resume LangGraph **re-executes the node from the start** (`types.py:824`), so the middleware body runs twice per held call | Idempotent middleware + deadline carried in the persisted interrupt payload + single-span guard (research R3) |
| 3 | Pin the semantic-conventions package and use `gen_ai.*` from it (§6) | Every gen_ai constant in `opentelemetry-semantic-conventions` `_incubating` is now annotated `Deprecated: Moved to …/semantic-conventions-genai`, and that repo ships no Python package | Vendor the constants in `core/semconv.py`, pinned to genai@`9af0834` / semconv v1.43.0, with a drift test against `refs/` |
| 4 | `intercept_hosted_tools=False  # verify per LangGraph version` (§8) | Provider-executed tools never enter `ToolNode`; only client-side tools are placed there (`factory.py:1055-1067`) | Confirmed `False` — and confirmed as a *structural* limit, not a version artifact |
| 5 | `agentcontrol.policy.id` ← OPA `decision_id` (§7) | `decision_id` is `omitempty` and is generated **only when the decision-log plugin is configured** (`runtime.go:930-938`) | Split into `agentcontrol.policy.id` (rule identifier, always supplied by the policy result) and `agentcontrol.policy.decision_id` (OPA's audit id, present when decision logging is on); startup warns loudly when it is absent |

## Open risk carried into implementation

**SC-006's restart clause is not fully satisfiable in v0.1.** The review deadline is durable — it rides inside the persisted interrupt payload, so a hold can never resolve to `ALLOW` after expiry no matter how long the process was down (FR-018 holds unconditionally). But *actively* resolving an expired hold to `DENY` requires something to resume the thread, and LangGraph checkpointers expose no portable "list every thread with a pending interrupt" query. v0.1 therefore ships: (a) deadline enforced at resume time — the correctness guarantee; (b) an in-process watchdog that auto-resolves holds while the process lives — the liveness guarantee; (c) a documented gap for a process that dies mid-hold and is never resumed.

Recommendation: amend SC-006 to scope the ≤30 s bound to a live process, and track cross-restart sweeping as v0.2 work. Flagged rather than silently absorbed, and re-raised in `/speckit-analyze`.

## Complexity Tracking

No Constitution Check violations to justify — the constitution is vacant (see [Constitution Check](#constitution-check)). Table intentionally empty.
