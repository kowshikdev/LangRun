"""Task T045: exactly one governance record per decision — widened per the
/speckit-analyze finding (I3) to also prove the review-hold cardinality FR-019 was
amended to state: one pending record plus one resolution record per hold, where an
approved resolution *is* the execution record rather than a third span.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.integration.conftest import TracedAgent
from tests.support.fake_model import ScriptedToolCallingModel, tool_call

_URL = "http://opa.test:8181"


@tool
def search(q: str) -> str:
    """Search for something."""
    return f"result for {q}"


@tool
def write_report(resource: str) -> str:
    """Write to a resource."""
    return f"wrote to {resource}"


def _allow() -> httpx.Response:
    return httpx.Response(
        200,
        json={"result": {"decision": "allow", "reason": "fine", "policy_id": "p.allow"}},
    )


def _review(timeout: int = 900) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "result": {
                "decision": "review",
                "reason": "hold",
                "policy_id": "p.review",
                "review_timeout_seconds": timeout,
            }
        },
    )


def _execute_tool_spans(exporter):
    return [s for s in exporter.get_finished_spans() if s.name.startswith("execute_tool ")]


@pytest.mark.respx(base_url=_URL)
class TestAllowDenyCardinality:
    async def test_one_span_per_allow(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_allow())
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(script=[[tool_call("search", {"q": "x"}, "c1")]])
        agent = control.attach(
            create_agent(model=model, tools=[search], middleware=[control.middleware])
        )
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
        assert len(_execute_tool_spans(traced.exporter)) == 1

    async def test_concurrent_tool_calls_each_get_their_own_span(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_allow())
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(
            script=[
                [
                    tool_call("search", {"q": "a"}, "c1"),
                    tool_call("search", {"q": "b"}, "c2"),
                ]
            ]
        )
        agent = control.attach(
            create_agent(model=model, tools=[search], middleware=[control.middleware])
        )
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})
        assert len(_execute_tool_spans(traced.exporter)) == 2


@pytest.mark.respx(base_url=_URL)
class TestReviewCardinality:
    async def test_approved_hold_produces_pending_plus_one_resolution_span(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_review(timeout=60))
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
        thread = {"configurable": {"thread_id": "cardinality-1"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        await agent.ainvoke(Command(resume={"decision": "approve"}), thread)

        spans = _execute_tool_spans(traced.exporter)
        # pending + (resolution == execution span); never three.
        assert len(spans) == 2

    async def test_rejected_hold_produces_pending_plus_one_resolution_span(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_review(timeout=60))
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
        thread = {"configurable": {"thread_id": "cardinality-2"}}
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        await agent.ainvoke(
            Command(resume={"decision": "reject", "reason": "no"}), thread
        )

        spans = _execute_tool_spans(traced.exporter)
        assert len(spans) == 2
