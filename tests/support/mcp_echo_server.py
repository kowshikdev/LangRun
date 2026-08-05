"""A minimal real MCP server, run as a subprocess over stdio.

Exists solely so `tests/conformance/test_tool_classes.py` can prove
`intercept_mcp_tools` against an actual MCP-adapted tool rather than a stand-in
`BaseTool` — the manifest claim is specifically that MCP tools enter the same
`ToolNode` as any other client-side tool, and a fake `BaseTool` cannot prove that.

Run directly (`python -m tests.support.mcp_echo_server`) to serve one tool,
`echo(text: str) -> str`, over stdio.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("agentcontrol-conformance-echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the input back, unchanged."""
    return text


if __name__ == "__main__":
    server.run(transport="stdio")
