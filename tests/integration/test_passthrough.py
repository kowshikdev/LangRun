"""Task T024: `ControlPlane()` with no providers is behavior-neutral.

Proves FR-031 / SC-007: a governed agent with no policy provider configured produces
identical output to an ungoverned one, and emits no governance spans.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain.agents import create_agent

from agentcontrol import ControlPlane
from tests.integration.conftest import TracedAgent
from tests.support.fake_model import ScriptedToolCallingModel, tool_call


def _run(model: ScriptedToolCallingModel, *, governed: bool) -> list[str]:
    tools = [_tool()]
    control = ControlPlane() if governed else None
    middleware = [control.middleware] if control else []
    agent = create_agent(model=model, tools=tools, middleware=middleware)
    if control is not None:
        agent = control.attach(agent)
    result = agent.invoke({"messages": [{"role": "user", "content": "go"}]})
    return [type(m).__name__ + ":" + str(getattr(m, "content", "")) for m in result["messages"]]


def _tool():
    from langchain_core.tools import tool as tool_decorator

    @tool_decorator
    def search(q: str) -> str:
        """Search for something."""
        return f"result for {q}"

    return search


class TestZeroConfigPassthrough:
    def test_output_identical_governed_vs_ungoverned(
        self, make_scripted_model: Callable[..., ScriptedToolCallingModel]
    ) -> None:
        script = [[tool_call("search", {"q": "x"}, "c1")]]
        ungoverned = _run(make_scripted_model(script), governed=False)
        governed = _run(make_scripted_model(script), governed=True)
        assert governed == ungoverned

    def test_no_spans_emitted_with_no_policy_configured(
        self,
        traced: TracedAgent,
        make_scripted_model: Callable[..., ScriptedToolCallingModel],
    ) -> None:
        control = ControlPlane(tracer_provider=traced.provider)
        agent = create_agent(
            model=make_scripted_model([[tool_call("search", {"q": "x"}, "c1")]]),
            tools=[_tool()],
            middleware=[control.middleware],
        )
        agent = control.attach(agent)
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        assert traced.exporter.get_finished_spans() == ()

    def test_attach_returns_the_agent_unmodified_in_identity(self) -> None:
        control = ControlPlane()
        agent = create_agent(model=ScriptedToolCallingModel(script=[]), tools=[_tool()])
        assert control.attach(agent) is agent
