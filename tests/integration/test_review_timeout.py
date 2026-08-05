"""Task T054: an unanswered or late-answered hold denies once its window expires,
recorded distinctly from an explicit rejection (FR-017, FR-018).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.core import semconv
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


def _build(traced: TracedAgent, *, thread_id: str):
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
    return agent, {"configurable": {"thread_id": thread_id}}


@pytest.mark.respx(base_url=_URL)
class TestExpiry:
    async def test_late_approval_is_denied_anyway(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_review(timeout=1))
        agent, thread = _build(traced, thread_id="expire-1")
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)

        await asyncio.sleep(1.2)  # past the 1-second window

        result = await agent.ainvoke(Command(resume={"decision": "approve"}), thread)
        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status == "error"
        assert "expired" in tool_messages[0].content.lower()

    async def test_expiry_recorded_as_timed_out_not_rejected(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_review(timeout=1))
        agent, thread = _build(traced, thread_id="expire-2")
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        await asyncio.sleep(1.2)
        await agent.ainvoke(Command(resume={"decision": "approve"}), thread)

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        states = [s.attributes.get(semconv.AC_REVIEW_STATE) for s in spans]
        assert states == ["pending", "timed_out"]
        assert spans[1].attributes[semconv.ERROR_TYPE] == "agentcontrol.review_timeout"

    async def test_still_pending_within_window_is_not_expired(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        """Negative control: a generous window does not spuriously expire."""
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_review(timeout=100))
        agent, thread = _build(traced, thread_id="expire-3")
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)

        result = await agent.ainvoke(Command(resume={"decision": "approve"}), thread)
        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        assert tool_messages[0].status != "error"


@pytest.mark.respx(base_url=_URL)
class TestPolicyDrivenWindow:
    """Task T057: the effective window comes from policy, not application code."""

    async def test_changing_the_policy_window_changes_behavior_with_no_code_change(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        route = respx_mock.post("/v1/data/agentcontrol/authz/result")

        route.mock(return_value=_review(timeout=1))
        short_agent, short_thread = _build(traced, thread_id="window-short")
        await short_agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, short_thread)
        await asyncio.sleep(1.2)
        short_result = await short_agent.ainvoke(
            Command(resume={"decision": "approve"}), short_thread
        )
        assert _tool_message(short_result).status == "error"

        route.mock(return_value=_review(timeout=100))
        long_agent, long_thread = _build(traced, thread_id="window-long")
        await long_agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, long_thread)
        # Same 1.2s wait; this time it is well inside a 100-second window.
        await asyncio.sleep(1.2)
        long_result = await long_agent.ainvoke(
            Command(resume={"decision": "approve"}), long_thread
        )
        assert _tool_message(long_result).status != "error"


def _tool_message(result: dict):
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    return tool_messages[0]
