# AgentControl

A runtime governance layer for AI agents.

AgentControl intercepts an agent's tool-call intent, enriches it with identity, context, and provenance evidence, gets a deterministic authorization decision, and preserves the entire decision as interoperable OpenTelemetry telemetry — regardless of which agent framework, policy engine, or observability backend you already use.

It is not a new agent framework, tracer, guardrail library, or eval engine. It is the layer that makes the ones you already have reason about the same execution and govern it together.

## Status

**v0.1 implemented.** All four user stories (block unauthorized actions, explain decisions from telemetry, human review with durable timeouts, fail-loud capability validation) are built and tested — 154 tests passing, 1 honestly skipped (needs a live MCP server this environment doesn't have).

| Artifact | State |
|---|---|
| Root build plan — [plan.md](./plan.md) | Locked architecture decisions |
| Constitution — [.specify/memory/constitution.md](./.specify/memory/constitution.md) | Ratified v1.0.0 |
| Specification — [specs/001-agentcontrol-runtime-governance/spec.md](./specs/001-agentcontrol-runtime-governance/spec.md) | 4 user stories, 32 requirements, 11 success criteria |
| Implementation plan + design — [plan.md](./specs/001-agentcontrol-runtime-governance/plan.md) | Constitution-checked, all deviations verified against source |
| Task breakdown — [tasks.md](./specs/001-agentcontrol-runtime-governance/tasks.md) | 87 tasks across 7 phases |
| Library code — `agentcontrol/` | Implemented: types, config, OTel recorder, OPA provider, LangGraph adapter/middleware/review, `ControlPlane` |
| Tests — `tests/{unit,integration,conformance}/` | 154 passing, 1 skipped (MCP conformance needs a live server) |

Two real bugs were found and fixed during implementation, not just planned around — see [research.md §R9](./specs/001-agentcontrol-runtime-governance/research.md) for both, with reproductions.

## What v0.1 does

A tool call from a governed LangGraph agent takes this path:

1. The middleware intercepts the call **before** the tool executes.
2. An `ActionIntent` is built from the request, graph state, and trace context — who is acting, on whose behalf, against what resource, from context of what trust level and provenance.
3. Registered inline evidence collectors run concurrently (none ship in v0.1; the interface does).
4. The intent and evidence go to Open Policy Agent, which is the single authority on the outcome.
5. The full decision is recorded as an OpenTelemetry span using `gen_ai.*` plus `agentcontrol.*` attributes and exported to any OTLP backend.
6. **Allow** proceeds to the tool. **Deny** short-circuits and returns a structured reason the agent can keep reasoning about. **Review** pauses the run via the checkpointer until a human answers or the window expires.

If the policy engine is unreachable, the answer is deny — and that denial is recorded distinctly from a policy-authored one. If the runtime cannot enforce what the policy demands, startup fails loudly naming the gap, rather than degrading into a mode where everyone believes actions are governed and none are.

### In scope for v0.1

Python SDK with a `ControlPlane` entrypoint · one framework adapter (LangGraph, via `wrap_tool_call`) · one policy provider (OPA, called synchronously) · OTLP export of the full decision · capability manifests validated at startup · fail-closed behavior.

### Out of scope until v0.2+

Additional framework adapters (OpenAI Agents SDK, Google ADK, CrewAI, AutoGen) · additional policy providers (Cedar) · evidence collector plugins (Presidio, NeMo Guardrails, Guardrails AI, LLM Guard) · async analyzer plugins (DeepEval, Ragas) · the incident → regression-test → policy-proposal automation loop.

Evidence collectors and async analyzers exist as **interfaces** in v0.1. The plugins themselves do not.

## Repository layout

```
agentcontrol/                Library
  core/                       types, config, errors, semconv (vendored gen_ai.*), otel, control_plane
  providers/policy/opa.py     OPA REST client
  providers/tracing/otlp.py   Tracer acquisition / OTLP export
  adapters/langgraph/         Middleware, intent construction, review holds, capability manifest
policies/                    Example Rego bundle + its opa test suite
examples/governed_agent.py   Runnable quickstart driver (--mode passthrough|deny|review)
tests/
  unit/                       Offline: types, config, semconv drift, span shape, OPA contract/provider
  integration/                Real create_agent graphs, mocked OPA transport (respx)
  conformance/                Proves every CapabilityManifest field against the real runtime
plan.md                      Root build plan — locked architecture decisions
specs/001-.../               Spec Kit artifacts for the v0.1 feature
  spec.md / plan.md / research.md / data-model.md / contracts/ / quickstart.md / tasks.md
.specify/memory/             Project constitution
refs/                        Vendored read-only upstream clones (gitignored)
```

## Reading order

New to the project, in this order:

1. [Constitution](./.specify/memory/constitution.md) — six non-negotiables. Everything else defers to these.
2. [Specification](./specs/001-agentcontrol-runtime-governance/spec.md) — what the thing must do.
3. [Research](./specs/001-agentcontrol-runtime-governance/research.md) — what the upstream libraries actually do, as opposed to what the design assumed. Five of the root plan's assumptions did not survive contact with the source.
4. [Implementation plan](./specs/001-agentcontrol-runtime-governance/plan.md) and [contracts](./specs/001-agentcontrol-runtime-governance/contracts/).
5. [Tasks](./specs/001-agentcontrol-runtime-governance/tasks.md) — where to start.

## Getting started

```bash
uv sync --all-extras                      # or: pip install -e ".[dev]"

pytest tests/unit -q                      # offline, no network
pytest tests/integration -q               # real create_agent graphs, OPA mocked
pytest tests/conformance -q               # proves the LangGraph adapter's capabilities

ruff check . && mypy agentcontrol

docker compose up -d                      # OPA on :8181 (decision logging on), Phoenix on :6006/:4318
python examples/governed_agent.py --mode deny    # needs the compose stack up
```

The end-to-end validation procedure, including prerequisites and expected outcomes for each scenario, is in [quickstart.md](./specs/001-agentcontrol-runtime-governance/quickstart.md). Live round-trip into Langfuse/Phoenix (quickstart Scenario 5) has not been executed in this environment — no Docker available — and remains to be run before a v0.1 release.

## The `refs/` directory

`refs/` holds read-only clones of the upstream projects this library builds on — LangChain, LangGraph, OPA, OpenTelemetry Python, the GenAI semantic conventions, and several v0.2 candidates. It is gitignored; the pinned commits are recorded in [research.md](./specs/001-agentcontrol-runtime-governance/research.md#pinned-sources).

It exists because this project composes four fast-moving upstream projects whose pre-stable APIs change between releases. Constitution Principle III requires that every third-party signature, attribute name, and behavioral assumption be verified there before use, and cited. Code written from memory against these libraries compiles, runs, and is wrong.

## Known open questions

Carried forward deliberately rather than resolved by assumption:

- **`create_agent()` (langchain 1.3.14) cannot resume a freshly rebuilt graph object.** A real process restart necessarily rebuilds the compiled graph and raises `KeyError('model')` on resume — verified independent of AgentControl (research R9a). Deadline durability across AgentControl's *own* state loss (a lost in-memory record, without a full process restart) is fixed and tested; a full `create_agent` process restart is not resumable at all in this pinned version, and fails loudly rather than silently.
- **`intercept_mcp_tools` is not conformance-tested in this environment.** No MCP server was available; the test is written and skipped with a clear reason (`tests/conformance/test_tool_classes.py`), not silently omitted.
- **Live Langfuse/Phoenix round-trip has not been executed.** No Docker in this environment. `docker-compose.yml` and the example driver are ready; running quickstart Scenario 5 is the remaining verification step.

## License

Not yet chosen.
