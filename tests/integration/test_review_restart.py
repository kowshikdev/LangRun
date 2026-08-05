"""Task T055: a hold's deadline survives loss of AgentControl's in-memory state,
because it lives in the persisted interrupt payload rather than in the middleware.

**Verified upstream limitation, not an AgentControl bug** (research.md R9): resuming a
`create_agent()`-built graph from a **freshly rebuilt** `CompiledStateGraph` object —
i.e. a real process restart, which necessarily reconstructs the graph — raises
`KeyError('model')` inside `create_agent`'s own conditional-routing logic
(`refs/langchain/…/agents/factory.py`, the `model_to_tools` branch), independent of
AgentControl and reproducible with zero AgentControl code involved. A hand-built
`langgraph.graph.StateGraph` does not exhibit this: resuming from a fresh graph object
against a real `AsyncSqliteSaver` works exactly as the `interrupt()` docstring example
promises. Filed against langchain 1.3.14 / langgraph 1.2.10; not fixable inside a
middleware.

What **is** provable and tested here is the part AgentControl controls: the deadline
lives in the persisted payload, not in `AgentControlMiddleware._pending`, so recovering
from a loss of that in-memory dict (the middleware's own state, as opposed to the
compiled graph object) still denies correctly past the deadline — see
`ReviewHold.from_payload` / `resolve()` unit tests for the payload-level proof, and the
scenario below for the middleware-level one, simulated by discarding
`AgentControlMiddleware._pending` directly while reusing the same compiled graph (the
one part of "restart" this pinned `create_agent` version can actually survive).

`_pending` loss recovers correctly via `AgentControlMiddleware._recover_hold_from_state`,
which queries `agent.aget_state(config).interrupts` for the original persisted hold
before falling back to computing a fresh one — bound in via `ControlPlane.attach()`
calling `middleware.bind_agent(agent)`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.adapters.langgraph.review import ReviewHold, build_hold, resolve
from agentcontrol.core import semconv
from agentcontrol.core.types import ActionIntent, ControlResult, Verdict
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.integration.conftest import TracedAgent
from tests.support.fake_model import ScriptedToolCallingModel, tool_call

_URL = "http://opa.test:8181"


@tool
def write_report(resource: str) -> str:
    """Write to a resource."""
    return f"wrote to {resource}"  # pragma: no cover - must never run once expired


def _review(timeout: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "result": {
                "decision": "review",
                "reason": "hold",
                "policy_id": "agentcontrol.authz.review_production_resource",
                "review_timeout_seconds": timeout,
            }
        },
    )


class TestPayloadLevelDurability:
    """The mechanism itself: a hold built, persisted, and read back has no dependency
    on any in-memory state — this is what actually makes FR-018 true.
    """

    def test_deadline_survives_full_serialization_round_trip(self) -> None:
        intent = ActionIntent(
            agent_id="a", tool="t", arguments={}, trace_id="a" * 32, span_id="b" * 16,
            thread_id="t-1", tool_call_id="c-1",
        )
        original = ControlResult(
            verdict=Verdict.REVIEW, provider="opa", reason="hold", review_timeout_seconds=1
        )
        hold = build_hold(intent, original, fallback_timeout_seconds=900)

        # Simulate a checkpointer serializing the payload to disk and back.
        import json

        payload = hold.to_payload()
        round_tripped = json.loads(json.dumps(payload))
        recovered = ReviewHold.from_payload(round_tripped)

        assert recovered is not None
        assert recovered.deadline == hold.deadline
        assert recovered.hold_id == hold.hold_id

    def test_resolve_denies_past_deadline_with_zero_in_memory_state(self) -> None:
        """No middleware, no _pending dict — resolve() takes only what was persisted."""
        intent = ActionIntent(
            agent_id="a", tool="t", arguments={}, trace_id="a" * 32, span_id="b" * 16,
            thread_id="t-1", tool_call_id="c-1",
        )
        original = ControlResult(
            verdict=Verdict.REVIEW, provider="opa", reason="hold", review_timeout_seconds=1
        )
        hold = build_hold(
            intent, original, fallback_timeout_seconds=900,
            now=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        outcome = resolve(hold, {"decision": "approve"}, original)
        assert outcome.result.verdict is Verdict.DENY
        assert "expired" in outcome.result.reason.lower()

    def test_a_forged_far_future_deadline_in_the_response_is_ignored(self) -> None:
        """resolve() reads the deadline from the persisted hold, never from the
        resume response — a human (or a bug) cannot extend a window by lying in the
        resume payload.
        """
        intent = ActionIntent(
            agent_id="a", tool="t", arguments={}, trace_id="a" * 32, span_id="b" * 16,
        )
        original = ControlResult(
            verdict=Verdict.REVIEW, provider="opa", reason="hold", review_timeout_seconds=1
        )
        hold = build_hold(
            intent, original, fallback_timeout_seconds=900,
            now=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        forged_response = {
            "decision": "approve",
            "agentcontrol": {
                "hold_id": hold.hold_id,
                "tool": hold.tool,
                "resource": hold.resource,
                "reason": hold.reason,
                "policy_id": hold.policy_id,
                "deadline": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
                "requested_at": hold.requested_at,
            },
        }
        # ReviewHold.from_payload would recover the FORGED deadline from a
        # maliciously-shaped response if resolve() used it — it must not.
        outcome = resolve(hold, forged_response, original)
        assert outcome.result.verdict is Verdict.DENY


@pytest.mark.respx(base_url=_URL)
class TestMiddlewareStateLoss:
    """Discard AgentControlMiddleware._pending directly (the one part of a restart
    this pinned create_agent version can survive; see module docstring for the
    verified langchain limitation on rebuilding the graph object itself).
    """

    async def test_late_resume_after_pending_state_is_dropped_still_denies(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=1))
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(
            script=[[tool_call("write_report", {"resource": "company/production"}, "c1")]]
        )
        agent = control.attach(
            create_agent(
                model=model,
                tools=[write_report],
                middleware=[control.middleware],
                checkpointer=InMemorySaver(),
            )
        )
        thread = {"configurable": {"thread_id": "restart-1"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        assert control.middleware.pending_holds  # sanity: something is tracked

        # Drop the middleware's own memory of the hold — the checkpointer/graph object
        # are untouched, isolating exactly what the deadline persistence is meant to
        # survive.
        control.middleware._pending.clear()

        await asyncio.sleep(1.2)
        result = await agent.ainvoke(Command(resume={"decision": "approve"}), thread)
        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status == "error"
        assert "expired" in tool_messages[0].content.lower()

    async def test_resolution_after_state_loss_is_marked_replay(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=60))
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(
            script=[[tool_call("write_report", {"resource": "company/production"}, "c1")]]
        )
        agent = control.attach(
            create_agent(
                model=model,
                tools=[write_report],
                middleware=[control.middleware],
                checkpointer=InMemorySaver(),
            )
        )
        thread = {"configurable": {"thread_id": "restart-2"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        control.middleware._pending.clear()

        await agent.ainvoke(Command(resume={"decision": "approve"}), thread)

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        replay_flags = [bool(s.attributes.get(semconv.AC_REVIEW_REPLAY)) for s in spans]
        # original pending (not a replay); recovered pending + the execution it led to
        # are both marked replay=True, since the whole resolution happened only after
        # this process recovered from lost state — informative context, not noise.
        assert replay_flags == [False, True, True]
        assert [s.attributes.get(semconv.AC_REVIEW_STATE) for s in spans] == [
            "pending",
            "pending",
            "approved",
        ]
