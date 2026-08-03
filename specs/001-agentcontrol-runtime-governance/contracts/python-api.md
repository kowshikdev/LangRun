# Contract: public Python API

The surface a host application touches. Everything else in `agentcontrol/` is internal and may change without notice in 0.x.

```python
from agentcontrol import (
    ControlPlane, ControlPlaneConfig, PolicyConfig, ReviewConfig, TelemetryConfig,
    Verdict, ActionIntent, Evidence, ControlResult,
    CapabilityManifest, RequiredCapabilities,
    InlineControl, AsyncAnalyzer, PolicyProvider, FrameworkAdapter,
    CapabilityMismatchError, PolicyUnavailableError,
)
```

## ControlPlane

```python
class ControlPlane:
    def __init__(
        self,
        *,
        config: ControlPlaneConfig | None = None,
        policy: PolicyProvider | None = None,
        adapter: FrameworkAdapter | None = None,
        inline_controls: Sequence[InlineControl] = (),
        analyzers: Sequence[AsyncAnalyzer] = (),
    ) -> None: ...

    def attach(self, agent: Any) -> Any: ...
    async def aclose(self) -> None: ...

    @property
    def middleware(self) -> AgentMiddleware: ...
```

- `ControlPlane()` with no arguments is the Phase 0 acceptance case: it attaches, runs startup validation over an empty provider set, and changes nothing about the agent's behavior (FR-031, SC-007).
- `attach(agent)` runs every startup check in [capability-manifest.md](./capability-manifest.md) and returns the governed agent. Raises `CapabilityMismatchError` on any gap; never returns a degraded object.
- `middleware` exposes the `AgentMiddleware` for hosts that build their graph themselves and want to place it in their own middleware list. **Register it first** — middleware compose first-defined-outermost (`refs/langchain/…/agents/factory.py:626-670`), so anything ahead of it could re-enter the tool without authorization.
- `aclose()` stops the review watchdog and flushes spans. Idempotent.

## Two integration shapes

**Managed** — AgentControl builds the middleware and validates the graph:

```python
from langchain.agents import create_agent
from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from agentcontrol.adapters.langgraph import LangGraphAdapter

cp = ControlPlane(
    config=ControlPlaneConfig(policy=PolicyConfig(url="http://localhost:8181")),
    policy=OPAPolicyProvider(...),
    adapter=LangGraphAdapter(),
)

agent = create_agent(model, tools=[...], middleware=[cp.middleware], checkpointer=saver)
cp.attach(agent)   # startup validation; raises on capability gaps
```

**Bring-your-own-graph** — same object, host owns composition. `attach()` still runs every check, including the `handle_tool_errors` inspection that a hand-built `ToolNode` makes relevant.

## Protocols

Unchanged from root `plan.md` §5:

```python
class InlineControl(Protocol):
    async def collect(self, event: ActionIntent) -> Evidence: ...

class AsyncAnalyzer(Protocol):
    async def analyze(self, trajectory_ref: str) -> None: ...

class PolicyProvider(Protocol):
    async def authorize(self, event: ActionIntent, evidence: list[Evidence]) -> ControlResult: ...

class FrameworkAdapter(Protocol):
    capabilities: CapabilityManifest
    def wrap(self, agent: Any) -> Any: ...
```

Contracts on implementers:
- `InlineControl.collect` runs in the hot path with a 50 ms budget. Raising or timing out drops that signal — it is never defaulted to a passing value. Collectors run concurrently via `asyncio.gather(..., return_exceptions=True)` (FR-003).
- `AsyncAnalyzer.analyze` runs after export and must never block a live call. No analyzer ships in v0.1; the interface exists so v0.2 plugins need no core change (FR-004).
- `PolicyProvider.authorize` must not raise. Transport and parse failures are converted into a `ControlResult` with `unavailable=True` and the fail-mode verdict, so the hot path has exactly one exit shape (FR-009).

## Review resolution

No UI ships in v0.1 (spec Assumptions), so a human resolves a hold through the host's own resume path:

```python
from langgraph.types import Command

# The hold surfaces in the stream as {'__interrupt__': (Interrupt(value=<ReviewHold>, id=...),)}
agent.invoke(Command(resume={"decision": "approve", "actor": "kowshik@example.com"}), config)
agent.invoke(Command(resume={"decision": "reject", "reason": "not authorized for prod"}), config)
```

Resume payload: `{"decision": "approve" | "reject", "actor": str | None, "reason": str | None}`.

An expired deadline overrides an `approve` (FR-018). Notification and approval routing are the integrator's responsibility in v0.1.

## Errors

| Error | Raised when | Recoverable |
|---|---|---|
| `CapabilityMismatchError` | startup validation gap, or a runtime verdict the manifest cannot enforce | no — fix config or graph |
| `PolicyUnavailableError` | only from an explicit `ControlPlane.check_policy_health()` probe | yes |
| `ConfigurationError` | invalid config (e.g. `fail_mode` not in `{closed, open}`) | no |

The hot path raises none of these. Provider failure becomes a `ControlResult`, not an exception — an exception escaping the middleware would be caught by `ToolNode` and converted into a tool error under some `handle_tool_errors` settings, turning a governance failure into a tool failure (research R2).

## Stability

0.x: `ControlPlane`, `Verdict`, `ActionIntent`, `Evidence`, `ControlResult`, `CapabilityManifest`, and the four protocols are the supported surface. `agentcontrol.core.semconv` string constants are stable within 0.x even if upstream renames — that is the entire point of the `agentcontrol.*` namespace (FR-021).
