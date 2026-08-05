# AGENTS.md

Working guide for any coding agent operating in this repository. Canonical — `CLAUDE.md` points here.

## What this repository is

AgentControl: a runtime governance layer that authorizes an AI agent's tool calls before they execute and records the decision as OpenTelemetry telemetry. See [README.md](./README.md) for the product framing.

**v0.1 is implemented.** All four user stories are built under `agentcontrol/`, tested (161 passing, 6 honestly skipped without extra tooling — 5 need a live `opa run --server`, 1 needs a live MCP server; 166 pass with OPA up), lint- and type-clean. Three real bugs were found and fixed during implementation via direct reproduction against the real runtime, not guessed at — see research.md §R9/§R10 and the "Verify before you write" table below before touching the review-hold, ALLOW-path, or OPA-client code. All three had genuine bugs that only surfaced by running things — one of them (§R10) only surfaced after installing a real `opa` binary; every mocked test had missed it.

## Non-negotiables

The constitution at [.specify/memory/constitution.md](./.specify/memory/constitution.md) is authoritative and supersedes anything here or in a plan. Six principles, compressed:

1. **Fail closed.** Provider failure, timeout, unparseable response, or an unrecognized verdict all resolve to `DENY`, recorded distinctly from a policy-authored denial. Fail-open is explicit per deployment and logged on *every* use. An unanswered review hold never becomes an approval.
2. **No silent degradation.** A capability gap is a hard startup failure naming the gap. No flag, env var, or config key may turn it into a warning — and the absence of such an escape hatch is itself a test.
3. **Verify upstream, never remember it.** Check `refs/` first, then the docs MCP servers, then published docs. Cite the source. An unverified third-party detail in a planning artifact is a defect, not a detail to settle later.
4. **Prove capabilities, don't declare them.** Every manifest field needs a test that exercises the real runtime. Asserting a declared value against itself is not a test. Negative declarations get proven too.
5. **Thresholds live only in policy.** Config controls how a signal is produced, never the consequence of that signal.
6. **Standard telemetry.** `gen_ai.*` where defined, `agentcontrol.*` for governance fields, W3C trace context for correlation. No parallel event schema, no separate run id, no vendor-specific read path.

## Verify before you write

**Before writing any code that touches LangChain, LangGraph, OPA, or OpenTelemetry, read [research.md](./specs/001-agentcontrol-runtime-governance/research.md).** Five assumptions in the root [plan.md](./plan.md) did not survive contact with the actual sources. Reproducing them from memory will produce code that runs and is wrong:

| Assumption | Reality |
|---|---|
| `@wrap_tool_call` decorator on an `async def` | Installs `awrap_tool_call` **only**; the inherited sync hook raises `NotImplementedError`, breaking every host that calls `invoke()`. Subclass `AgentMiddleware` and implement both hooks. |
| `interrupt()` returns a value inline | It raises `GraphInterrupt`, and on resume LangGraph **re-executes the node from the start** — the middleware body runs twice per held call. |
| `gen_ai.*` constants come from `opentelemetry-semantic-conventions` | Every gen_ai constant there is deprecated and moved to a repo that ships no Python package. Vendor them, pin the convention version, drift-test. |
| OPA always returns `decision_id` | Only when the decision-log plugin is configured. The rule identifier must come from the policy result object instead. |
| `intercept_hosted_tools` is version-dependent | It is structural — provider-executed tools never enter `ToolNode`. |
| Two ALLOW-path spans (decision + execution) | FR-019 requires exactly one; fixed by wrapping execution in the *same* span as the decision (`GovernanceRecorder.governed_execution`). Found by running the span-emission tests, not by inspection. |
| `create_agent()` resumes a rebuilt graph object the same as the original | It cannot — raises `KeyError('model')` inside its own routing, independent of AgentControl (research R9a). Not fixable in a middleware; a hand-built `StateGraph` does not have this problem. |
| `request.runtime.config` is safe to pass straight into `agent.aget_state()` | It carries a task-scoped `checkpoint_ns` that silently returns an empty/unrelated snapshot. Strip to bare `{"configurable": {"thread_id": ...}}` first (`_thread_level_config`, research R9b). |
| `POST /v1/data/agentcontrol/authz` returns the decision object directly | It returns the whole Rego **package** as siblings (`result`, plus the bundle's own threshold constants) — the decision is nested one level deeper, at `.../authz/result`. Every mocked test missed this; only running a real `opa run --server` caught it (research R10). Reflex-check any OPA path against a live server before trusting a mock. |

One sharp edge that is easy to reintroduce: `ToolNode` wraps middleware in a bare `except Exception` with no `GraphBubbleUp` guard. `interrupt()` survives only because the default `handle_tool_errors` re-raises anything that is not a `ToolInvocationError`. A host with a custom handler would silently convert a review hold into a tool error. `adapters/langgraph/adapter.py::_require_reraising_error_handler` checks for exactly this at startup.

### Verification order

1. **`refs/`** — vendored read-only clones, gitignored, pinned commits recorded in [research.md](./specs/001-agentcontrol-runtime-governance/research.md#pinned-sources). Contains `langchain`, `langgraph`, `opa`, `opentelemetry-python`, `semantic-conventions-genai`, `langsmith-sdk`, plus v0.2 candidates. Read the source and its tests.
2. **MCP servers** — `docs-langchain` (configured in `.mcp.json`) for published LangChain/LangGraph docs; `reference-langchain` for exact symbol signatures.
3. **Documentation URLs** in [plan.md](./plan.md) §12 for anything the first two do not cover.

Cite as `refs/<path>:<line>` or the doc source. If `refs/` is missing, clone the repos at the commits in `research.md` rather than proceeding from memory.

## Architecture

The whole system is one decision made in one place, wrapped in evidence and telemetry.

**Interception** — `AgentControlMiddleware` subclasses `AgentMiddleware` and implements both `wrap_tool_call` and `awrap_tool_call`. It runs inside LangGraph's `ToolNode`, receives a `ToolCallRequest` (tool call dict, `BaseTool`, agent state, runtime), and either calls the supplied handler or does not. Not calling it *is* the block.

**Authorization** — the middleware builds an `ActionIntent`, gathers evidence from any registered `InlineControl` concurrently, and hands both to a single `PolicyProvider`. OPA is the only authority; evidence collectors produce data and never a verdict. This is deliberate — multiple independently-voting providers produce undefined behavior.

**Verdicts** — `ALLOW` proceeds. `DENY` short-circuits and returns a `ToolMessage(status="error")` carrying the reason and rule id, so the agent keeps reasoning instead of crashing. `REVIEW` calls `interrupt()` with a payload carrying an **absolute deadline**, which is what makes the timeout survive a restart; on resume, expiry overrides an approval unconditionally.

**Telemetry** — one span per decision, `execute_tool {tool_name}`, kind `INTERNAL`, carrying `gen_ai.*` plus `agentcontrol.*`. Never sampled away: a sampled-away denial is an unauditable denial. Tool arguments and results are opt-in and off by default — the decision stays explainable without them.

**Startup validation** — `ControlPlane.attach()` compares what the policy requires against what the adapter's `CapabilityManifest` provides, plus a checkpointer check, a `handle_tool_errors` check, and a `thread_id` check. Any gap raises `CapabilityMismatchError` naming the field and the fix.

Layout: `agentcontrol/core/` (types, config, control plane, otel, vendored semconv), `agentcontrol/providers/policy/` (OPA client), `agentcontrol/providers/tracing/` (OTLP wiring), `agentcontrol/adapters/langgraph/` (adapter, middleware, intent, review), `policies/` (Rego bundle), `tests/{unit,integration,conformance}/`.

Contracts are specified, not left to implementation: [python-api.md](./specs/001-agentcontrol-runtime-governance/contracts/python-api.md), [opa-authz.md](./specs/001-agentcontrol-runtime-governance/contracts/opa-authz.md), [otel-attributes.md](./specs/001-agentcontrol-runtime-governance/contracts/otel-attributes.md), [capability-manifest.md](./specs/001-agentcontrol-runtime-governance/contracts/capability-manifest.md).

## Commands

```bash
uv sync --all-extras                     # or: pip install -e ".[dev]"

pytest tests/unit -q                     # offline, no network — 100+ tests
pytest tests/integration -q              # real create_agent graphs, OPA mocked via respx
pytest tests/conformance -q              # proves every CapabilityManifest field against the real runtime

pytest tests/unit/test_types.py::TestControlResult::test_review_requires_timeout -q   # single test
pytest -m "not integration" -q           # skip network-dependent suites

opa test policies/                       # Rego unit tests — 6/6 pass (verified with opa 1.19.0)
pytest tests/integration/test_live_opa.py -m live_opa    # against a real `opa run --server`; auto-skips without one
ruff check . && mypy agentcontrol         # both clean as of last implementation pass
```

Services the integration and quickstart scenarios need:

```bash
docker compose up -d    # OPA on :8181 with decision logging, Phoenix on :6006/:4318
```

`--set decision_logs.console=true` on OPA is not cosmetic — without a decision-log plugin OPA omits `decision_id` entirely, and the telemetry assertions fail.

Full validation procedure: [quickstart.md](./specs/001-agentcontrol-runtime-governance/quickstart.md).

## Workflow

**Phase gates are sequential.** Do not start a milestone before the previous one's acceptance passes. The mapping between [tasks.md](./specs/001-agentcontrol-runtime-governance/tasks.md) phases and [plan.md](./plan.md) §11 milestones is at the top of `tasks.md`.

- Tests precede implementation for any behavior with a stated acceptance criterion, and must fail first.
- Every task cites the requirement, research decision, or contract that produced it. A task with no anchor is scope creep.
- When verified reality contradicts the specification, **amend the specification**. Implementing the plan while quietly failing a literal reading of the spec is prohibited by the constitution.

This repository uses [Spec Kit](https://github.com/github/spec-kit). Artifacts live under `specs/<NNN>-<slug>/`; the active feature directory is recorded in `.specify/feature.json`.

## Open questions

Do not silently resolve these — they are tracked deliberately:

- **`create_agent()` cannot resume a rebuilt graph object at all** (research R9a, langchain 1.3.14) — a genuine process restart fails loudly with `KeyError('model')`, independent of AgentControl. AgentControl's own state-loss recovery (a lost in-memory record without a process restart) is fixed and tested. Either pin a langchain version where this is confirmed fixed, or document the limitation prominently for `create_agent` users.
- **`intercept_mcp_tools` has no conformance proof in this environment** — no MCP server available. Test is written and explicitly skipped (`tests/conformance/test_tool_classes.py`), not silently omitted.
- **Live Langfuse/Phoenix round-trip (quickstart Scenario 5) has not been run** — no Docker in this environment. `docker-compose.yml` and `examples/governed_agent.py --export otlp` are ready for it.

## Conventions

- Python ≥3.10. Type hints and return types on all public functions; Google-style docstrings.
- Conventional Commits.
- Never commit anything under `refs/` — it is gitignored on purpose and holds hundreds of megabytes of upstream source.
