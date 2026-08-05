# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read AGENTS.md first

[AGENTS.md](./AGENTS.md) is the canonical working guide: non-negotiables, architecture, commands, workflow, and open questions. It is kept agent-agnostic so it does not drift from this file. Everything below is either a summary or Claude-Code-specific.

**Before writing any code that touches LangChain, LangGraph, OPA, or OpenTelemetry**, read [research.md](./specs/001-agentcontrol-runtime-governance/research.md). Five assumptions in the root [plan.md](./plan.md) are contradicted by the actual upstream sources, and reproducing them from memory yields code that runs and is wrong. The table in [AGENTS.md](./AGENTS.md#verify-before-you-write) lists them.

## State of the repository

v0.1 implemented. `agentcontrol/` has all four user stories built; 154 tests passing, 1 honestly skipped (MCP conformance — needs a live server this environment lacks). Two real bugs were found and fixed during implementation by running things, not by inspection — see [research.md §R9](./specs/001-agentcontrol-runtime-governance/research.md) before touching the review-hold or ALLOW-path span code.

Two things not yet done: live Langfuse/Phoenix round-trip (quickstart Scenario 5 — no Docker in this environment) and `opa test policies/` (no `opa` binary here; CI runs it).

## Commands

```bash
uv sync --all-extras                     # or: pip install -e ".[dev]"
pytest tests/unit -q                     # offline
pytest tests/integration -q              # real create_agent graphs, OPA mocked via respx
pytest tests/conformance -q              # proves every CapabilityManifest field against the real runtime
pytest tests/unit/test_types.py::TestControlResult::test_review_requires_timeout -q    # single test
pytest -m "not integration" -q           # skip network suites
opa test policies/                       # Rego unit tests
ruff check . && mypy agentcontrol         # both clean
docker compose up -d                     # OPA :8181 (decision logging on), Phoenix :6006/:4318
```

## Architecture in one paragraph

One authorization decision, made in one place, wrapped in evidence and telemetry. `AgentControlMiddleware` subclasses `AgentMiddleware` and implements **both** `wrap_tool_call` and `awrap_tool_call` (the decorator form installs only one and breaks sync agents). It runs inside LangGraph's `ToolNode`, builds an `ActionIntent` from the request plus graph state plus trace context, gathers evidence concurrently, and hands both to a single `PolicyProvider` — OPA is the only authority, and evidence collectors never vote. `ALLOW` calls the handler; `DENY` simply does not, returning a structured `ToolMessage` the agent can keep reasoning about; `REVIEW` calls `interrupt()` with an absolute deadline in the payload, which is what makes the timeout survive a restart. Every decision becomes one never-sampled OTel span carrying `gen_ai.*` plus `agentcontrol.*`. `ControlPlane.attach()` validates the adapter's capability manifest against what the policy demands and fails hard on any gap. Full detail, including the module layout and the contract files, is in [AGENTS.md](./AGENTS.md#architecture).

## Claude Code specifics

**Spec Kit slash commands** are installed as skills in `.claude/skills/`: `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-analyze`, `/speckit-implement`, `/speckit-clarify`, `/speckit-checklist`, `/speckit-constitution`, `/speckit-converge`, `/speckit-taskstoissues`. The active feature directory is recorded in `.specify/feature.json`; helper scripts are PowerShell (`.specify/scripts/powershell/`), matching the `"script": "ps"` setting in `.specify/init-options.json`.

**MCP servers.** `.mcp.json` configures `docs-langchain` for published LangChain and LangGraph documentation. A `reference-langchain` server providing exact symbol signatures is also available. Prefer these over recalled API knowledge — Constitution Principle III requires it.

**`refs/` is gitignored and large.** Never `git add` anything under it. Use Grep and Read against it freely; it is the primary source of truth for upstream behavior. Pinned commits are in [research.md](./specs/001-agentcontrol-runtime-governance/research.md#pinned-sources).

**Platform.** Development is on Windows with PowerShell as the primary shell; a Bash tool is also available. The OPA container path in the integration tests is Linux-only, so CI must run Linux for those suites.

## Things that will bite

- The `@wrap_tool_call` decorator is the obvious API and the wrong one here — it installs `awrap_tool_call` or `wrap_tool_call`, never both.
- `interrupt()` re-executes the node from the start on resume. Anything in the middleware body must be idempotent, and spans must not double-emit — this bit for real once already (fixed via `GovernanceRecorder.governed_execution`, one span for decision+outcome instead of two).
- OPA returns HTTP 200 with **no `result` key** for an undefined document. Reading that as allow would silently disable enforcement; it is a provider failure.
- Trust defaults to `unknown`, never `trusted`. Missing evidence is missing, never a passing value.
- No config key may change an authorization outcome. If a threshold needs tuning, it belongs in the Rego bundle.
- `request.runtime.config` inside a `ToolNode` push-task carries a task-scoped `checkpoint_ns`. Passing it straight to `agent.aget_state()` silently returns the wrong (usually empty) snapshot — strip to `{"configurable": {"thread_id": ...}}` first.
- `create_agent()` cannot resume a freshly rebuilt graph object (langchain 1.3.14) — verified independent of AgentControl. Don't assume a real process restart can resume a held review; it currently can't, and fails loudly rather than silently.
