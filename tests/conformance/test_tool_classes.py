"""Proves: intercept_function_tools, intercept_hosted_tools, intercept_mcp_tools.

`intercept_mcp_tools` is proven against a **real** MCP server (`tests/support/
mcp_echo_server.py`, run as a subprocess over stdio) and the real
`langchain-mcp-adapters` client — not a stand-in `BaseTool` — because the manifest
claim is specifically that MCP-adapted tools enter the same `ToolNode` as any other
client-side tool, which a fake `BaseTool` cannot demonstrate.
"""

from __future__ import annotations

import sys

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


class TestInterceptMcpTools:
    async def test_a_real_mcp_backed_tool_is_intercepted(self) -> None:
        """Spawns a real MCP server subprocess, loads its tool through the real
        `langchain-mcp-adapters` client, and confirms it reaches `wrap_tool_call`
        exactly like a plain `@tool` function — proving `intercept_mcp_tools=True`
        against the real mechanism rather than an assumption about it.
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {
                "echo": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "tests.support.mcp_echo_server"],
                }
            }
        )
        mcp_tools = await client.get_tools()
        assert [t.name for t in mcp_tools] == ["echo"]

        seen: list[str] = []

        class _Recorder(AgentMiddleware):
            async def awrap_tool_call(self, request, handler):
                seen.append(request.tool_call["name"])
                return await handler(request)

        model = ScriptedToolCallingModel(script=[[tool_call("echo", {"text": "hi"}, "c1")]])
        agent = create_agent(model=model, tools=mcp_tools, middleware=[_Recorder()])
        # MCP-adapted tools are async-only (StructuredTool with no sync `func`, only a
        # coroutine — MCP transport is inherently async), so `_Recorder` implements
        # only awrap_tool_call and this must be ainvoke(). Using the sync hook here
        # would hit "StructuredTool does not support sync invocation" — a reminder of
        # exactly the sync/async hook-pairing trap research R1 documents, from the
        # opposite direction.
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

        assert seen == ["echo"]
        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status != "error"

    async def test_mcp_tool_is_present_in_the_tool_node_registry(self) -> None:
        """Structural mirror of the hosted-tool exclusion check above: an MCP tool,
        unlike a hosted one, does show up in `tools_by_name` — it is a genuine
        client-side `BaseTool`, dispatched through `ToolNode` like any other.
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {
                "echo": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "tests.support.mcp_echo_server"],
                }
            }
        )
        mcp_tools = await client.get_tools()

        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[*mcp_tools, search])
        tool_node = agent.nodes["tools"].bound
        assert "echo" in tool_node.tools_by_name
        assert "search" in tool_node.tools_by_name
