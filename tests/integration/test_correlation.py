"""Task T044: governance spans correlate via the standard trace context, never a
separate product-specific run id, and stay recorded even without ambient context.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import tool

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.core import semconv
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.integration.conftest import TracedAgent
from tests.support.fake_model import ScriptedToolCallingModel, tool_call

_URL = "http://opa.test:8181"


@tool
def search(q: str) -> str:
    """Search for something."""
    return f"result for {q}"


def _allow() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "decision_id": "d-1",
            "result": {"decision": "allow", "reason": "fine", "policy_id": "p.allow"},
        },
    )


@pytest.mark.respx(base_url=_URL)
class TestCorrelation:
    async def test_governance_span_shares_trace_with_surrounding_agent_activity(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_allow())
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(script=[[tool_call("search", {"q": "x"}, "c1")]])
        agent = create_agent(model=model, tools=[search], middleware=[control.middleware])
        agent = control.attach(agent)

        outer_tracer = traced.provider.get_tracer("agent-runtime")
        with outer_tracer.start_as_current_span("agent-run") as outer_span:
            expected_trace_id = f"{outer_span.get_span_context().trace_id:032x}"
            await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

        gov_spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        assert len(gov_spans) == 1
        assert f"{gov_spans[0].context.trace_id:032x}" == expected_trace_id
        # No separate product-specific run identifier anywhere on the span.
        assert not any("run_id" in key or "run.id" in key for key in gov_spans[0].attributes)

    async def test_decision_with_no_ambient_context_is_still_recorded_and_flagged(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_allow())
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(
            config=config, policy=OPAPolicyProvider(config.policy), tracer_provider=traced.provider
        )
        model = ScriptedToolCallingModel(script=[[tool_call("search", {"q": "x"}, "c1")]])
        agent = create_agent(model=model, tools=[search], middleware=[control.middleware])
        agent = control.attach(agent)

        # No outer span started: this call has no ambient trace context.
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

        gov_spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        assert len(gov_spans) == 1
        assert gov_spans[0].attributes.get(semconv.AC_CORRELATION_ORPHAN) is True
