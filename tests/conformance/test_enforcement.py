"""Proves: block_before_tool, modify_tool_arguments, human_approval,
streaming_interception.

`human_approval` is exhaustively proven already by the US3 integration suite
(approve/reject/timeout/restart/watchdog); one focused smoke test lives here so the
conformance suite is a complete, self-contained manifest-field checklist without
duplicating that work.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.support.fake_model import ScriptedToolCallingModel, tool_call

_URL = "http://opa.test:8181"

_executed: list[str] = []


@tool
def side_effecting_tool(q: str) -> str:
    """A tool whose execution is observable."""
    _executed.append(q)
    return f"result for {q}"


class TestBlockBeforeTool:
    def test_skipping_execute_prevents_the_side_effect(self) -> None:
        _executed.clear()

        class _Blocker(AgentMiddleware):
            def wrap_tool_call(self, request, handler):
                del handler
                from langchain_core.messages import ToolMessage

                return ToolMessage(
                    content="blocked", tool_call_id=request.tool_call["id"], status="error"
                )

        model = ScriptedToolCallingModel(
            script=[[tool_call("side_effecting_tool", {"q": "x"}, "c1")]]
        )
        agent = create_agent(
            model=model, tools=[side_effecting_tool], middleware=[_Blocker()]
        )
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        assert _executed == []


class TestModifyToolArguments:
    def test_overridden_arguments_reach_the_tool(self) -> None:
        _executed.clear()

        class _Rewriter(AgentMiddleware):
            def wrap_tool_call(self, request, handler):
                modified = {**request.tool_call, "args": {"q": "rewritten"}}
                return handler(request.override(tool_call=modified))

        model = ScriptedToolCallingModel(
            script=[[tool_call("side_effecting_tool", {"q": "original"}, "c1")]]
        )
        agent = create_agent(
            model=model, tools=[side_effecting_tool], middleware=[_Rewriter()]
        )
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        assert _executed == ["rewritten"]


def _review(timeout: int = 60) -> httpx.Response:
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


@pytest.mark.respx(base_url=_URL)
class TestHumanApproval:
    """Focused smoke test; full coverage is tests/integration/test_review_*.py."""

    async def test_interrupt_pauses_and_resume_completes(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_review())
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        control = ControlPlane(config=config, policy=OPAPolicyProvider(config.policy))
        model = ScriptedToolCallingModel(
            script=[[tool_call("side_effecting_tool", {"q": "x"}, "c1")]]
        )
        agent = control.attach(
            create_agent(
                model=model,
                tools=[side_effecting_tool],
                middleware=[control.middleware],
                checkpointer=InMemorySaver(),
            )
        )
        thread = {"configurable": {"thread_id": "conformance-review"}}
        first = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, thread)
        assert "__interrupt__" in first
        second = await agent.ainvoke(Command(resume={"decision": "approve"}), thread)
        assert any(type(m).__name__ == "ToolMessage" for m in second["messages"])


class TestStreamingInterception:
    def test_middleware_sees_the_completed_response_not_individual_tokens(self) -> None:
        """`False` is truthful: interception granularity is per-call regardless of
        whether the host streams. `wrap_model_call` fires once per model turn, never
        once per token, whether invoked via `.invoke()` or `.stream()`.
        """
        call_count = 0

        class _Counter(AgentMiddleware):
            def wrap_model_call(self, request, handler):
                nonlocal call_count
                call_count += 1
                return handler(request)

        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[side_effecting_tool], middleware=[_Counter()])
        list(agent.stream({"messages": [{"role": "user", "content": "go"}]}, stream_mode="values"))
        assert call_count == 1
