"""Task T056: a live process auto-resolves an expired hold without a human ever
resuming it (SC-006's liveness half — the deadline itself is the correctness half,
covered by test_review_timeout.py and test_review_restart.py).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig, ReviewConfig
from agentcontrol.core import semconv
from agentcontrol.core.types import RequiredCapabilities
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


@pytest.mark.respx(base_url=_URL)
class TestWatchdog:
    async def test_expired_hold_auto_resolves_without_a_human_resuming(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=1))
        config = ControlPlaneConfig(
            policy=PolicyConfig(url=_URL),
            review=ReviewConfig(watchdog_poll_seconds=0.2),
            required_capabilities=RequiredCapabilities(human_approval=True),
        )
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(
            script=[[tool_call("write_report", {"resource": "company/production"}, "c1")]]
        )
        checkpointer = InMemorySaver()
        agent = control.attach(
            create_agent(
                model=model,
                tools=[write_report],
                middleware=[control.middleware],
                checkpointer=checkpointer,
            )
        )
        thread = {"configurable": {"thread_id": "watchdog-1"}}
        try:
            first = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
            assert "__interrupt__" in first
            assert control.middleware.pending_holds  # sanity: watchdog has something to find

            # No Command(resume=...) call from the test — the watchdog must do it,
            # well within the 1s window + 0.2s poll interval.
            await asyncio.sleep(1.5)

            snapshot = await agent.aget_state(thread)
            assert not snapshot.next, "the graph should have resumed past the interrupt"
        finally:
            await control.aclose()

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        states = [s.attributes.get(semconv.AC_REVIEW_STATE) for s in spans]
        assert states == ["pending", "timed_out"]

    async def test_watchdog_stops_cleanly_on_aclose(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        """aclose() must not hang or raise even mid-poll-cycle."""
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=60))
        config = ControlPlaneConfig(
            policy=PolicyConfig(url=_URL),
            review=ReviewConfig(watchdog_poll_seconds=0.1),
            required_capabilities=RequiredCapabilities(human_approval=True),
        )
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
        thread = {"configurable": {"thread_id": "watchdog-2"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        await asyncio.sleep(0.15)  # let the watchdog poll at least once, hold not expired
        await control.aclose()  # must return promptly, not hang
