"""Task T053: a review hold resolved by a human — approve executes, reject denies."""

from __future__ import annotations

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
    return f"wrote to {resource}"


def _review(timeout: int = 900) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "result": {
                "decision": "review",
                "reason": "write against production requires approval",
                "policy_id": "agentcontrol.authz.review_production_resource",
                "review_timeout_seconds": timeout,
            }
        },
    )


def _build(traced: TracedAgent):
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
    return agent


@pytest.mark.respx(base_url=_URL)
class TestApprove:
    async def test_approval_executes_the_tool(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=60))
        agent = _build(traced)
        thread = {"configurable": {"thread_id": "approve-1"}}

        first = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        assert "__interrupt__" in first
        assert not any(type(m).__name__ == "ToolMessage" for m in first["messages"])

        second = await agent.ainvoke(
            Command(resume={"decision": "approve", "actor": "kowshik"}), thread
        )
        tool_messages = [m for m in second["messages"] if type(m).__name__ == "ToolMessage"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status != "error"
        assert "wrote to company/production" in tool_messages[0].content

    async def test_approval_span_recorded_as_approved(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=60))
        agent = _build(traced)
        thread = {"configurable": {"thread_id": "approve-2"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        await agent.ainvoke(Command(resume={"decision": "approve"}), thread)

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        states = [s.attributes.get(semconv.AC_REVIEW_STATE) for s in spans]
        assert states == ["pending", "approved"]
        assert spans[1].attributes[semconv.AC_POLICY_DECISION] == "allow"


@pytest.mark.respx(base_url=_URL)
class TestReject:
    async def test_rejection_denies_and_does_not_execute(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=60))
        agent = _build(traced)
        thread = {"configurable": {"thread_id": "reject-1"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)

        result = await agent.ainvoke(
            Command(resume={"decision": "reject", "reason": "not authorized for prod"}), thread
        )
        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status == "error"
        assert "not authorized for prod" in tool_messages[0].content

    async def test_rejection_span_recorded_as_rejected(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(return_value=_review(timeout=60))
        agent = _build(traced)
        thread = {"configurable": {"thread_id": "reject-2"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        await agent.ainvoke(Command(resume={"decision": "reject"}), thread)

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        states = [s.attributes.get(semconv.AC_REVIEW_STATE) for s in spans]
        assert states == ["pending", "rejected"]
        assert spans[1].attributes[semconv.ERROR_TYPE] == "agentcontrol.review_rejected"
