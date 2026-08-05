# Phase 0 Research: AgentControl v0.1

**Date**: 2026-08-04 | **Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

Every third-party interface below was read in the vendored clones under `refs/`. Line numbers are against the pinned commits in the table; they are reproducible, not remembered.

## Pinned sources

| Clone | Commit | Date | Used for |
|---|---|---|---|
| `refs/langchain` | `a6b904fd5b1edfd9db45302285fb959f77228056` | 2026-08-03 | `AgentMiddleware`, `wrap_tool_call`, `create_agent` |
| `refs/langgraph` | `b2926a0ff9589c28c7e01fe7cdbb337b86d5a4b4` | 2026-07-30 | `ToolNode`, `ToolCallRequest`, `interrupt`, `Command`, errors |
| `refs/opa` | `515aea3fa8cad0c6d975e0d03e00803c72563487` | 2026-08-03 | REST data API types, decision-id generation |
| `refs/opentelemetry-python` | `cd298d545dfdd04a16906e934a0f8b31bf834090` | 2026-07-31 | SDK/exporter versions, semconv deprecation state |
| `refs/semantic-conventions-genai` | `9af08349db7e70b2528accde90bae81d4ebcfa1e` | 2026-08-02 | canonical `gen_ai.*` names, `execute_tool` span shape |

Package versions read from the same clones: `langchain` 1.3.14, `langgraph` 1.2.10, `opentelemetry-sdk` 1.45.0.dev, `opentelemetry-semantic-conventions` 0.66b0.dev, genai conventions pinned to `SEMCONV_VERSION=v1.43.0`.

---

## R1 — Interception point and hook signature

**Decision**: Ship `AgentControlMiddleware(AgentMiddleware)` implementing **both** `wrap_tool_call` and `awrap_tool_call`. Do not use the `@wrap_tool_call` decorator.

**Verified signatures** (`refs/langchain/libs/langchain_v1/langchain/agents/middleware/types.py`):

```python
# :662-666
def wrap_tool_call(
    self,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
) -> ToolMessage | Command[Any]: ...

# :744-748
async def awrap_tool_call(
    self,
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]: ...
```

`ToolCallRequest` is a dataclass re-exported from langgraph-prebuilt (`types.py:29`), defined at `refs/langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py:132-149`:

```python
@dataclass
class ToolCallRequest:
    tool_call: ToolCall          # {"name", "args", "id"}
    tool: BaseTool | None        # None when the tool is not registered with ToolNode
    state: Any                   # agent state (dict | list | BaseModel)
    runtime: ToolRuntime         # LangGraph runtime; carries config/context/store
```

Argument modification is `request.override(tool_call=…, state=…)`, returning a new instance (`tool_node.py:170-199`); direct attribute assignment is deprecated (`:151-168`).

**Rationale for rejecting the decorator**: `wrap_tool_call(func)` inspects `iscoroutinefunction(func)` and builds a middleware subclass carrying **exactly one** hook — `awrap_tool_call` for async functions, `wrap_tool_call` for sync ones (`types.py:2105-2157`). The base class's other hook then raises `NotImplementedError` with the message *"Synchronous implementation of wrap_tool_call is not available… you defined only the async version (awrap_tool_call) and invoked your agent in a synchronous context"* (`types.py:732-742`). Root `plan.md` §8 sketches `@wrap_tool_call` on an `async def`, which would break every host application that calls `agent.invoke()`. A subclass carrying both hooks costs a few extra lines and removes the failure mode.

**Composition**: middleware are chained first-defined-outermost (`refs/langchain/…/agents/factory.py:626-670`), and `create_agent` passes the composed wrappers into a single `ToolNode` (`factory.py:1055-1067`). AgentControl should therefore be registered **first** in the middleware list so it authorizes before any retry/caching middleware can re-enter the tool.

**Alternatives considered**: wrapping each `BaseTool` individually (misses dynamically registered tools and duplicates per-tool state); a custom `ToolNode` subclass (forks upstream behavior and breaks on every ToolNode change); `before_model`/`after_model` inspection like `HumanInTheLoopMiddleware` uses (`human_in_the_loop.py:384`) — rejected because it acts on the model's *proposal*, one hop before execution, and cannot see arguments injected at execution time.

---

## R2 — Blocking, and where exceptions go

**Decision**: Short-circuit by simply **not calling** `handler(request)` and returning a synthesized `ToolMessage(status="error")`. Confirmed supported and intended.

**Evidence** (`refs/langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py:1044-1067` sync, `:1191-1222` async): the wrapper receives an `execute` callable and its return value is used directly; skipping it is the documented short-circuit pattern (`types.py:2089-2099`, "Short-circuit with cached result").

**Sharp edge worth knowing**: the wrapper invocation is wrapped in a bare `except Exception` that converts wrapper exceptions into an error `ToolMessage` when `handle_tool_errors` is truthy (`tool_node.py:1054-1067`). Unlike the tool-execution path, which explicitly re-raises `GraphBubbleUp` first (`tool_node.py:973-983`), the wrapper path has **no such guard**. `GraphInterrupt` extends `GraphBubbleUp` extends `Exception` (`refs/langgraph/libs/langgraph/langgraph/errors.py:50-51`, `:102-107`), so a naive reading says our `interrupt()` gets swallowed into a tool error.

It does not, under the default configuration: `handle_tool_errors` defaults to the callable `_default_handle_tool_errors`, which returns a message only for `ToolInvocationError` and **re-raises everything else** (`tool_node.py:383-392`). And `create_agent` builds its `ToolNode` without passing `handle_tool_errors` at all (`factory.py:1061-1064`), so the default applies.

The risk is real but narrow: a host application that builds its own `ToolNode` with `handle_tool_errors=True`, a string, or a custom callable that does not re-raise will silently convert a `REVIEW` hold into a tool error. Startup validation therefore inspects the resolved `ToolNode._handle_tool_errors` and, if the policy can return `review` and the handler is not `_default_handle_tool_errors`, fails startup under FR-028 rather than shipping a governance hole.

---

## R3 — REVIEW: pause, resume, and the re-execution problem

**Decision**: `interrupt()` from inside the middleware, with an **absolute UTC deadline in the interrupt payload**, an idempotency guard against duplicate spans, and an in-process watchdog for liveness.

**Verified API** (`refs/langgraph/libs/langgraph/langgraph/types.py`):
- `interrupt(value: Any) -> Any` (`:811`) — first call in a task raises `GraphInterrupt` carrying `value`; requires a checkpointer (`:830-831`).
- Resume with `Command(resume=…)` (`:768-772`) — either a single value for the next interrupt, or a mapping of interrupt id → value.
- The interrupt surfaces to the client as `{'__interrupt__': (Interrupt(value=…, id=…),)}` (`:881`).

**The consequence that changes the design** (`types.py:824`): *"The graph resumes from the start of the node, **re-executing** all logic."* The middleware body therefore runs a second time for every held call. Naively, that means a second evidence sweep, a second OPA call, and a second governance span — violating FR-019 ("exactly one governance record per decision").

**Design**:

1. **Pass 1** — build intent → collect evidence → OPA → verdict `review`. Emit the governance span immediately with `agentcontrol.review.state = "pending"` and `agentcontrol.review.deadline` (absolute ISO-8601 UTC, computed from the policy-supplied window). Register the hold in a process-local map keyed by `(thread_id, tool_call_id)`. Call `interrupt({...payload including deadline...})`, which raises.
2. **Pass 2 (resume)** — the guard sees a pending hold for this key and skips evidence + OPA entirely, going straight to `interrupt()`, which now returns the human's decision. Compare `now()` against the deadline **read back from the persisted payload**, not from memory. Emit one resolution span (`agentcontrol.review.state ∈ {approved, rejected, timed_out}`) linked to the pending span, and return allow/deny accordingly.
3. **Cold pass 2** (process restarted, guard map empty) — evidence + OPA re-run, which is safe: a policy is a pure function of its input, so the verdict is identical, and the deadline is re-read from the persisted payload rather than recomputed. The duplicate pending span is tolerated and marked `agentcontrol.review.replay=true` so audit can collapse it. Documented, not hidden.
4. **Deadline enforcement is unconditional at resume**: expired ⇒ `DENY`, whatever the human said. This is what makes FR-018 ("never resolves to approval") hold across restarts, because the deadline lives in the checkpoint rather than in a timer.
5. **Watchdog** — an `asyncio` task started by `ControlPlane` resumes expired holds with a `__agentcontrol_timeout__` sentinel while the process is alive, satisfying SC-006's 30-second bound for live processes.

**Known gap**: a process that dies mid-hold and is never resumed leaves the hold pending indefinitely. It can never become an `ALLOW`, so the safety property holds, but the *active* auto-deny does not fire. LangGraph checkpointers expose no portable "enumerate threads with pending interrupts" query, so a sweeper cannot be written generically in v0.1. Carried as an open risk in [plan.md](./plan.md#open-risk-carried-into-implementation), with a proposed amendment to SC-006.

**Alternatives considered**: blocking on an `asyncio.Event` inside the middleware (holds the process, dies with it, no durability — rejected); `HumanInTheLoopMiddleware`'s `after_model` interrupt (fires before the tool call is even dispatched, so it cannot see execution-time arguments and does not compose with a policy verdict — rejected); returning a `Command` that routes to a human node (requires editing the host's graph topology, which the SDK must not do — rejected).

---

## R4 — Telemetry attribute source

**Decision**: Vendor the `gen_ai.*` names as literal constants in `agentcontrol/core/semconv.py`, pinned to genai conventions `9af0834` / `SEMCONV_VERSION=v1.43.0`, guarded by a drift test. Import only *stable* attributes (`error.type`, resource attributes) from `opentelemetry-semantic-conventions`.

**Why not the obvious import**: every gen_ai constant in `opentelemetry-semantic-conventions` now carries `"""Deprecated: Moved to the [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai)."""` — verified across `GEN_AI_TOOL_NAME`, `GEN_AI_TOOL_CALL_ID`, `GEN_AI_TOOL_TYPE`, `GEN_AI_TOOL_CALL_ARGUMENTS`, `GEN_AI_TOOL_CALL_RESULT`, and the `GenAiOperationNameValues` enum (`refs/opentelemetry-python/opentelemetry-semantic-conventions/src/opentelemetry/semconv/_incubating/attributes/gen_ai_attributes.py:240-285`, `:351-374`). Building on a deprecated module that is scheduled for removal would guarantee a break.

The successor repo ships YAML models, Weaver templates, and generated Markdown — no Python distribution (`refs/semantic-conventions-genai/` contains `model/`, `docs/`, `templates/`, `policies/`; no `pyproject.toml`). So there is nothing to depend on, and the names must be vendored. Root `plan.md` §6's instruction to "pin the semantic conventions package version" is satisfied in spirit by pinning the *convention* version and asserting against it.

**Names taken as canonical** (`refs/semantic-conventions-genai/model/gen-ai/registry.yaml`, rendered in `docs/gen-ai/gen-ai-spans.md:1045-1120`):

The **execute tool span** requires `gen_ai.operation.name` (value `execute_tool`) and `gen_ai.tool.name`; recommends `gen_ai.tool.call.id`, `gen_ai.tool.description`, `gen_ai.tool.type` (`function` | `extension` | `datastore`); marks `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result` **Opt-In** with an explicit *"may contain sensitive information"* warning. Span name SHOULD be `execute_tool {gen_ai.tool.name}`; span kind SHOULD be `INTERNAL`; `error.type` is conditionally required on failure.

**Consequence for FR-002/edge case "tool arguments contain sensitive data"**: arguments and results are Opt-In upstream, so AgentControl defaults them **off** and exposes `record_tool_arguments` / `record_tool_results` flags. The governance decision stays fully explainable without them, because tool name, resource, trust, provenance, verdict, and rule id are all on the span regardless.

`gen_ai.evaluation.*` (`registry.yaml:849-874`) exists for scores and explanations but belongs to the v0.2 async-analyzer path; not emitted in v0.1.

**Drift test**: a unit test reads `refs/semantic-conventions-genai/model/gen-ai/registry.yaml` when present and asserts every vendored constant still matches, skipping cleanly when `refs/` is absent (it is gitignored). Cheap insurance against a silent rename.

---

## R5 — OPA request/response contract

**Decision**: `POST {opa_url}/v1/data/agentcontrol/authz` with body `{"input": {…}}`; read `result` as a **structured object**, not a bare string.

**Verified types** (`refs/opa/v1/server/types/types.go`):

```go
// :242-243
type DataRequestV1 struct { Input *any `json:"input"` }

// :267-282
type DataResponseV1 struct {
    DecisionID  string        `json:"decision_id,omitempty"`
    Provenance  *ProvenanceV1 `json:"provenance,omitempty"`
    Explanation TraceV1       `json:"explanation,omitempty"`
    Metrics     MetricsV1     `json:"metrics,omitempty"`
    Result      *any          `json:"result,omitempty"`
    Warning     *Warning      `json:"warning,omitempty"`
}
```

**`decision_id` is conditional.** `Server.generateDecisionID()` returns `""` unless a factory is installed (`refs/opa/v1/server/server.go:2764-2768`), and the runtime installs one only when the decision-log plugin is present: `if logs.Lookup(rt.Manager) != nil { return generateDecisionID() }` (`refs/opa/v1/runtime/runtime.go:930-938`). Root `plan.md` §7 maps `decision_id` onto `agentcontrol.policy.id` unconditionally; that would leave the audit id empty on any OPA started without decision logging.

**Resolution**: two distinct attributes. `agentcontrol.policy.id` is the **rule identifier**, always supplied by the policy result object, so FR-022 is satisfiable on any OPA. `agentcontrol.policy.decision_id` carries OPA's audit id when present. `ControlPlane` startup issues a loud warning — not a failure — when the first probe response omits `decision_id`, because the missing piece is auditability of the *provider*, not enforcement.

**Why a result object rather than `decision := "allow"`**: root `plan.md` §7's example policy returns a bare string, which gives the SDK nowhere to read a reason, a rule id, or the review window from. Since FR-013 forbids authorization-affecting numbers outside the bundle and FR-016 requires the review window to be "configurable in policy, not in code", the window has to come back with the decision. The bundle therefore returns:

```rego
result := {
    "decision": decision,
    "reason": reason,
    "policy_id": policy_id,
    "review_timeout_seconds": review_timeout_seconds,
}
```

Full schema and the rewritten example bundle: [contracts/opa-authz.md](./contracts/opa-authz.md).

**Fail-closed triggers** (all map to `DENY` + `agentcontrol.policy.unavailable=true`): connect error, read timeout past `timeout_ms` (default 300), non-2xx status, body that is not JSON, `result` absent (which is what OPA returns for an undefined document), `decision` missing or outside `{allow, deny, review}`. The last one matters — an undefined Rego rule yields a *successful* 200 with no `result`, which must never read as allow.

**Transport**: `httpx.AsyncClient` with a per-request `timeout=` and a connection pool reused across calls. The sync hook drives the same client through `asyncio.run_coroutine_threadsafe` against a dedicated loop, so sync and async agents share one code path.

---

## R6 — Capability manifest: what LangGraph actually provides

Each field below is a claim the Phase 4 conformance suite must prove by execution (FR-030), not by asserting the declared constant. Current evidence:

| Field | Value | Evidence |
|---|---|---|
| `observe_model_calls` | `True` | `wrap_model_call` / `awrap_model_call` hooks exist (`types.py:1821-1840`) |
| `observe_tool_calls` | `True` | `wrap_tool_call` receives every dispatched call (`tool_node.py:1030-1055`) |
| `intercept_model_input` | `True` | `ModelRequest` is mutable via override in `wrap_model_call` (`types.py:85-269`) |
| `intercept_model_output` | `True` | `wrap_model_call` returns `ModelResponse` (`types.py:270-325`) |
| `intercept_function_tools` | `True` | client-side `BaseTool`s are exactly what `ToolNode` executes (`factory.py:1055-1067`) |
| `intercept_mcp_tools` | `True` *(to prove)* | MCP tools adapted to `BaseTool` enter the same `ToolNode`; no MCP-specific branch exists in `tool_node.py`. Conformance test must use a real MCP-backed tool, not a stand-in |
| `intercept_hosted_tools` | `False` | provider-executed tools are excluded from `ToolNode`, which receives only `middleware_tools + regular_tools` (`factory.py:1055-1067`). Structural, not version-dependent |
| `block_before_tool` | `True` | skipping `execute` is supported and documented (`tool_node.py:1044-1055`; `types.py:2089-2099`) |
| `modify_tool_arguments` | `True` | `request.override(tool_call=…)` (`tool_node.py:170-199`) |
| `human_approval` | `True` *(conditional)* | `interrupt()` propagates through the wrapper under the default `handle_tool_errors` (R2). Conditional on that default — hence the startup check |
| `streaming_interception` | `False` | no per-token hook on the tool path; `wrap_model_call` sees the completed `ModelResponse` |

**Required-capability derivation** (FR-027): parse the loaded Rego bundle for the decision values it can return. Static Rego parsing is fragile, so v0.1 derives requirements from an explicit `RequiredCapabilities` declaration in `ControlPlaneConfig`, cross-checked at runtime — the first time a policy returns a verdict the manifest says is unsupported, the call is denied and a `CapabilityMismatchError` is raised. Declared-and-verified beats a brittle parser; the check still cannot be silently skipped. Detail in [contracts/capability-manifest.md](./contracts/capability-manifest.md).

---

## R7 — Trace correlation

**Decision**: read `trace_id`/`span_id` from the ambient OTel context — `opentelemetry.trace.get_current_span().get_span_context()` — formatted as 32- and 16-char lowercase hex. Never mint a separate id (locked decision §4.2, FR-023).

When no span is recording, `INVALID_SPAN_CONTEXT` is returned (all-zero ids). The governance span is still created and exported — it simply starts a new trace — which satisfies the edge case "correlation context missing: a decision must still be recorded and still be attributable". The `agentcontrol.correlation.orphan=true` attribute marks these so a reviewer can tell a missing-context decision from a correlated one.

LangSmith tracing, if the host enables it, runs alongside and is not a dependency; `refs/langsmith-sdk` was consulted only to confirm no conflict with OTel context propagation.

---

## R8 — Export path

**Decision**: `opentelemetry-exporter-otlp-proto-http` with `BatchSpanProcessor`, endpoint from standard `OTEL_EXPORTER_OTLP_ENDPOINT` env vars or explicit config. Both Phase 3 backends ingest OTLP natively (Langfuse OTLP endpoint; Phoenix OTLP collector), so no vendor SDK is taken as a dependency — which is what makes FR-024 ("without requiring a vendor-specific integration") testable.

`ControlPlane` never installs a global `TracerProvider` when one already exists; it acquires a tracer from the ambient provider and only bootstraps a provider when explicitly asked (`providers/tracing/otlp.py`). Hijacking a host application's tracer provider would be a hostile default in a library.

**Governance spans are never sampled out** (FR-019, spec Assumptions): the governance tracer uses `ALWAYS_ON` regardless of the host's sampler. A sampled-away denial is an unauditable denial.

---

## R9 — Two findings from implementing the review-hold restart path (T055)

Found live while writing the T055 restart tests, not predicted in planning. Both verified by direct reproduction, not inferred.

### R9a — `create_agent()` cannot resume a freshly rebuilt graph object (langchain 1.3.14)

Building a **second, independent** `create_agent()`-compiled graph and resuming a thread whose interrupt was raised by a **different** compiled graph object — even with identical topology, identical tools, and a real serializing `AsyncSqliteSaver` (not just `InMemorySaver`) — raises `KeyError('model')` inside `create_agent`'s own conditional-routing logic (`refs/langchain/libs/langchain_v1/langchain/agents/factory.py`, the `model_to_tools` branch construction, `:1652-1666`).

Reproduced with **zero AgentControl code involved**: a bare `AgentMiddleware` calling `interrupt()` directly hits the same error. A hand-built `langgraph.graph.StateGraph` using the exact pattern from the `interrupt()` docstring example (`refs/langgraph/libs/langgraph/langgraph/types.py:833-890`) does **not** exhibit this — resuming from a freshly built graph object against a real `AsyncSqliteSaver` works exactly as documented.

Consequence: a **true process restart** of a `create_agent()`-based deployment — which necessarily rebuilds the compiled graph, since Python objects don't survive process death — cannot resume a held review at all in this pinned version. It fails loudly (an exception), rather than silently mishandling the hold, which is the better of two bad outcomes, but it means SC-006/FR-018's restart clause is **not exercisable end-to-end via `create_agent`** today. Not fixable from a middleware; filed against langchain 1.3.14 / langgraph 1.2.10. `tests/integration/test_review_restart.py`'s module docstring carries the full repro.

### R9b — the fix for AgentControl's own part: `aget_state` must be called with a thread-scoped config, not the tool-task's config

Independent of R9a, testing "this middleware's own in-memory `_pending` record was lost" (a real, narrower scenario than a full process restart — e.g. a pooled/recycled middleware instance sharing a live graph object) surfaced a genuine bug in the original design: `_run_review`'s cold path recomputed the deadline as `now() + window` instead of recovering the one originally persisted, which could let a late approval through past the true deadline.

The fix — `AgentControlMiddleware._recover_hold_from_state` querying `agent.aget_state(config).interrupts` before falling back to `build_hold` — initially still failed to find the interrupt. Cause: `request.runtime.config` inside a `ToolNode` push-task carries a **nested `checkpoint_ns`** scoped to that task (e.g. `tools:<task-id>`), not the top-level thread. Calling `aget_state` with that task-scoped config silently returns an unrelated (typically empty) snapshot rather than raising — so the pending interrupt, which `interrupt()` recorded at the **thread** level, never surfaced. Fix: reduce the config to bare `{"configurable": {"thread_id": ...}}` before calling `aget_state` (`agentcontrol/adapters/langgraph/middleware.py::_thread_level_config`). Confirmed via direct reproduction: the same `aget_state` call against `{"configurable": {"thread_id": ...}}` finds the interrupt every time; against the full task-scoped `request.runtime.config`, it never does.

`StateSnapshot.interrupts` and `CompiledStateGraph.aget_state` are verified at `refs/langgraph/libs/langgraph/langgraph/pregel/main.py:1436` (signature) and `types.py:643-661` (`StateSnapshot` fields, `interrupts: tuple[Interrupt, ...]`).
