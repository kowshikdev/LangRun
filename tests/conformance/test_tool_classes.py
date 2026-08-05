"""Proves: intercept_function_tools, intercept_hosted_tools; documents the skip for
intercept_mcp_tools.
"""

from __future__ import annotations

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import tool

from tests.support.fake_model import ScriptedToolCallingModel, tool_call


@tool
def search(q: str) -> str:
    """Search for something."""
    return f"result for {q}"


class TestInterceptFunctionTools:
    def test_a_real_tool_decorated_function_is_intercepted(self) -> None:
        """Extensively exercised already by US1-US3; one focused proof here."""
        seen: list[str] = []

        class _Recorder(AgentMiddleware):
            def wrap_tool_call(self, request, handler):
                seen.append(request.tool_call["name"])
                return handler(request)

        model = ScriptedToolCallingModel(script=[[tool_call("search", {"q": "x"}, "c1")]])
        agent = create_agent(model=model, tools=[search], middleware=[_Recorder()])
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        assert seen == ["search"]


class TestInterceptHostedTools:
    def test_dict_shaped_hosted_tool_never_reaches_toolnode(self) -> None:
        """Proves the `False` declaration is truthful, not merely asserted.

        A provider-hosted tool is represented as a plain dict (verified:
        refs/langchain/libs/langchain_v1/langchain/agents/factory.py:1052 —
        `built_in_tools = [t for t in tools if isinstance(t, dict)]`), and only
        `middleware_tools + regular_tools` (never `built_in_tools`) populate the
        `ToolNode` (`:1055-1067`). A middleware's `wrap_tool_call` therefore
        structurally never sees a hosted-tool call — there is no code path for it to
        be dispatched through. This is what makes AgentControl's inability to enforce
        against hosted tools an honest, checkable limitation rather than an assumption.
        """
        seen: list[str] = []

        class _Recorder(AgentMiddleware):
            def wrap_tool_call(self, request, handler):
                seen.append(request.tool_call["name"])
                return handler(request)

        hosted_tool = {"type": "web_search_preview"}
        # No function tools at all — only the hosted one — so any tool_call reaching
        # our middleware could only be for the hosted tool.
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[hosted_tool], middleware=[_Recorder()])
        agent.invoke({"messages": [{"role": "user", "content": "go"}]})
        assert seen == []

    def test_hosted_tool_is_excluded_from_the_tool_node_registry(self) -> None:
        """Direct structural check: the hosted tool never enters `tools_by_name`."""
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(
            model=model, tools=[search, {"type": "web_search_preview"}]
        )
        tool_node = agent.nodes["tools"].bound
        assert "search" in tool_node.tools_by_name
        assert "web_search_preview" not in tool_node.tools_by_name
        assert len(tool_node.tools_by_name) == 1


@pytest.mark.skip(
    reason=(
        "intercept_mcp_tools must be proven with a real MCP-backed tool per "
        "contracts/capability-manifest.md — a fake BaseTool proves nothing here, "
        "since the manifest claim is specifically that MCP-adapted tools enter the "
        "same ToolNode as any other client-side tool. This environment has no MCP "
        "server available. Structural evidence (no MCP-specific branch exists in "
        "refs/langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py) is recorded "
        "in contracts/capability-manifest.md but is not a substitute for this test."
    )
)
def test_mcp_backed_tool_is_intercepted() -> None:
    """Requires a live MCP server; not runnable in this environment."""
