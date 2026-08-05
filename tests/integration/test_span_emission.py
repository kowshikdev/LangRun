"""Task T025: every tool call produces exactly one correctly attributed span.

Root plan.md Phase 1 gate. Uses an explicit ALLOW-only fake policy provider rather
than OPA (that dependency belongs to US1) — the point here is span *shape*, not
authorization behavior.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain.agents import create_agent

from agentcontrol import ControlPlane, ControlPlaneConfig
from agentcontrol.core.types import ActionIntent, ControlResult, Evidence, Verdict
from tests.integration.conftest import TracedAgent
from tests.support.fake_model import ScriptedToolCallingModel, tool_call


class _AllowAllProvider:
    """Every call is allowed. Used to isolate span shape from OPA behavior."""

    name = "fake-allow-all"

    async def authorize(
        self, event: ActionIntent, evidence: Sequence[Evidence] = ()
    ) -> ControlResult:
        del evidence
        return ControlResult(
            verdict=Verdict.ALLOW,
            provider=self.name,
            reason="test fixture allows everything",
            policy_id="fixture.allow_all",
        )

    async def aclose(self) -> None:
        return None


def _build_control(traced: TracedAgent) -> ControlPlane:
    return ControlPlane(
        config=ControlPlaneConfig(),
        policy=_AllowAllProvider(),
        tracer_provider=traced.provider,
    )


def _agent_with(model: ScriptedToolCallingModel, control: ControlPlane):
    from langchain_core.tools import tool

    @tool
    def search(q: str) -> str:
        """Search for something."""
        return f"result for {q}"

    agent = create_agent(model=model, tools=[search], middleware=[control.middleware])
    return control.attach(agent)


class TestSpanEmission:
    def test_every_tool_call_produces_exactly_one_span(
        self, traced: TracedAgent
    ) -> None:
        control = _build_control(traced)
        model = ScriptedToolCallingModel(
            script=[
                [tool_call("search", {"q": "a"}, "c1")],
                [tool_call("search", {"q": "b"}, "c2")],
            ]
        )
        agent = _agent_with(model, control)
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})

        spans = traced.exporter.get_finished_spans()
        execute_tool_spans = [s for s in spans if s.name.startswith("execute_tool ")]
        assert len(execute_tool_spans) == 2

    def test_span_carries_gen_ai_and_agentcontrol_attributes(
        self, traced: TracedAgent
    ) -> None:
        from agentcontrol.core import semconv

        control = _build_control(traced)
        model = ScriptedToolCallingModel(script=[[tool_call("search", {"q": "x"}, "c1")]])
        agent = _agent_with(model, control)
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes[semconv.GEN_AI_TOOL_NAME] == "search"
        assert span.attributes[semconv.AC_POLICY_DECISION] == "allow"
        assert span.attributes[semconv.AC_POLICY_PROVIDER] == "fake-allow-all"
        assert span.attributes[semconv.AC_POLICY_ID] == "fixture.allow_all"
