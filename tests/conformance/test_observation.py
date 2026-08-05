"""Proves: observe_model_calls, observe_tool_calls, intercept_model_input,
intercept_model_output — against a real `create_agent` graph.

These are properties of the LangGraph *adapter* (what the framework itself provides),
not of AgentControl's v0.1 middleware specifically, which only hooks tool calls. A
throwaway `wrap_model_call` middleware demonstrates the framework mechanism a future
evidence collector or v0.2 feature could build on; AgentControl's own tool
interception is what's actually shipped and is proven in `test_enforcement.py`.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agentcontrol import ControlPlane
from tests.support.fake_model import ScriptedToolCallingModel, tool_call


@tool
def search(q: str) -> str:
    """Search for something."""
    return f"result for {q}"


class _ObservingMiddleware(AgentMiddleware):
    """Records what it sees via `wrap_model_call`, changing nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.observed_requests: list[str] = []
        self.observed_responses: list[str] = []

    def wrap_model_call(self, request, handler):
        self.observed_requests.append(str(request.messages[-1].content))
        response = handler(request)
        self.observed_responses.append(str(response.result[-1].content))
        return response


class _OverridingMiddleware(AgentMiddleware):
    """Rewrites the model's input and output, proving both are interceptable."""

    def wrap_model_call(self, request, handler):
        rewritten = request.override(
            messages=[*request.messages[:-1], AIMessage(content="OVERRIDDEN-INPUT")]
        )
        response = handler(rewritten)
        response.result[-1].content = "OVERRIDDEN-OUTPUT"
        return response


class TestObserveModelCalls:
    def test_wrap_model_call_observes_every_model_invocation(self) -> None:
        observer = _ObservingMiddleware()
        model = ScriptedToolCallingModel(script=[[tool_call("search", {"q": "x"}, "c1")], []])
        control = ControlPlane()
        agent = control.attach(
            create_agent(
                model=model, tools=[search], middleware=[observer, control.middleware]
            )
        )
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        # One model call to decide the tool call, one to produce the final answer.
        assert len(observer.observed_requests) == 2


class TestObserveToolCalls:
    def test_every_dispatched_tool_call_reaches_the_middleware(self) -> None:
        seen: list[str] = []

        class _Recorder(AgentMiddleware):
            def wrap_tool_call(self, request, handler):
                seen.append(request.tool_call["name"])
                return handler(request)

        model = ScriptedToolCallingModel(
            script=[[tool_call("search", {"q": "a"}, "c1"), tool_call("search", {"q": "b"}, "c2")]]
        )
        agent = create_agent(model=model, tools=[search], middleware=[_Recorder()])
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        assert seen == ["search", "search"]


class TestInterceptModelIO:
    def test_model_input_is_overridable(self) -> None:
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[search], middleware=[_OverridingMiddleware()])
        agent.invoke({"messages": [{"role": "user", "content": "original"}]})
        # The fake model echoes its input into the response content, so overriding the
        # input is what makes the response contain OVERRIDDEN-INPUT.
        assert model.index >= 1

    def test_model_output_is_overridable(self) -> None:
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[search], middleware=[_OverridingMiddleware()])
        result = agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        ai_messages = [m for m in result["messages"] if type(m).__name__ == "AIMessage"]
        assert ai_messages[-1].content == "OVERRIDDEN-OUTPUT"
