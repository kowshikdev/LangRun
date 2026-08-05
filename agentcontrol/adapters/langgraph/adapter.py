"""LangGraph framework adapter and its capability manifest.

Every value in `LANGGRAPH_CAPABILITIES` is a claim that must be proven by an executable
conformance test against the real runtime. The citations record where the claim came
from; `tests/conformance/` is what makes it evidence.
"""

from __future__ import annotations

import logging
from typing import Any

from agentcontrol.core.errors import CapabilityMismatchError
from agentcontrol.core.types import CapabilityManifest, RequiredCapabilities

__all__ = ["LANGGRAPH_CAPABILITIES", "LangGraphAdapter"]

_LOG = logging.getLogger(__name__)

LANGGRAPH_CAPABILITIES = CapabilityManifest(
    # wrap_model_call / awrap_model_call hooks exist — middleware/types.py:1821-1840
    observe_model_calls=True,
    # every dispatched call reaches the wrapper — prebuilt/tool_node.py:1030-1055
    observe_tool_calls=True,
    # ModelRequest is overridable inside wrap_model_call — middleware/types.py:85-269
    intercept_model_input=True,
    intercept_model_output=True,
    # client-side BaseTools are exactly what ToolNode executes — factory.py:1055-1067
    intercept_function_tools=True,
    # MCP tools adapt to BaseTool and enter the same ToolNode; no MCP branch exists in
    # tool_node.py. Must be proven with a real MCP-backed tool, not a stand-in.
    intercept_mcp_tools=True,
    # provider-executed tools never enter ToolNode, which receives only
    # `middleware_tools + regular_tools` — factory.py:1055-1067. Structural, not a
    # version artifact.
    intercept_hosted_tools=False,
    # skipping `execute` is the documented short-circuit — tool_node.py:1044-1055
    block_before_tool=True,
    # request.override(tool_call=...) — tool_node.py:170-199
    modify_tool_arguments=True,
    # interrupt() propagates through the wrapper under the default handle_tool_errors,
    # which re-raises anything that is not a ToolInvocationError — tool_node.py:383-392.
    # Conditional on that default, hence the startup check below.
    human_approval=True,
    # no per-token hook on the tool path; wrap_model_call sees a completed response
    streaming_interception=False,
)


class LangGraphAdapter:
    """Binds AgentControl to a LangGraph agent built with `create_agent`."""

    name = "langgraph"
    capabilities = LANGGRAPH_CAPABILITIES

    def wrap(self, agent: Any) -> Any:
        """Return the agent unchanged.

        The middleware is composed into the graph by the host at build time, because a
        library that rewrites a compiled graph behind the caller's back is worse than
        one that asks to be registered. Validation happens in `validate`.
        """
        return agent

    # ------------------------------------------------------------- startup checks

    def validate(self, agent: Any, required: RequiredCapabilities) -> None:
        """Fail loudly on any gap between what the policy needs and what we provide.

        There is deliberately no downgrade path: no argument, flag, or config key turns
        a mismatch into a warning.
        """
        for capability in required.required_names():
            if not self.capabilities.supports(capability):
                raise CapabilityMismatchError(
                    capability,
                    self.name,
                    required_by=f"ControlPlaneConfig.required_capabilities.{capability}=True",
                    provided=False,
                    reason="the adapter's capability manifest declares this unsupported",
                    fix=(
                        f"set required_capabilities.{capability}=False if no policy in "
                        "the bundle needs it, or use an adapter that provides it"
                    ),
                )

        if required.human_approval:
            self._require_checkpointer(agent)
            self._require_reraising_error_handler(agent)

    def _require_checkpointer(self, agent: Any) -> None:
        if agent is None or _has_checkpointer(agent):
            return
        raise CapabilityMismatchError(
            "human_approval",
            self.name,
            required_by="ControlPlaneConfig.required_capabilities.human_approval=True",
            provided=False,
            reason="no checkpointer configured on the compiled graph; interrupt() requires one",
            fix=(
                "compile the graph with a checkpointer, or set "
                "required_capabilities.human_approval=False if no policy returns 'review'"
            ),
        )

    def _require_reraising_error_handler(self, agent: Any) -> None:
        """Reject a ToolNode whose error handling would swallow a review hold.

        `ToolNode` wraps the middleware call in a bare `except Exception` with no
        `GraphBubbleUp` guard (tool_node.py:1054-1067), unlike the tool-execution path
        (:973-983). `interrupt()` survives only because the default handler re-raises
        anything that is not a `ToolInvocationError` (:383-392). A custom handler that
        returns a string instead would silently turn a review hold into a tool error.
        """
        handler = _tool_error_handler(agent)
        if handler is _UNKNOWN or _is_default_handler(handler):
            return
        raise CapabilityMismatchError(
            "human_approval",
            self.name,
            required_by="ControlPlaneConfig.required_capabilities.human_approval=True",
            provided=False,
            reason=(
                "the ToolNode is configured with a custom handle_tool_errors that does "
                "not re-raise, so GraphInterrupt would be swallowed and a review hold "
                "would silently become a tool error"
            ),
            fix=(
                "leave handle_tool_errors at its default, or make the custom handler "
                "re-raise langgraph.errors.GraphBubbleUp"
            ),
        )


_UNKNOWN = object()


def _has_checkpointer(agent: Any) -> bool:
    """Return whether a compiled graph has a checkpointer attached."""
    for attribute in ("checkpointer", "_checkpointer"):
        value = getattr(agent, attribute, None)
        if value:
            return True
    builder = getattr(agent, "builder", None)
    return bool(getattr(builder, "checkpointer", None))


def _tool_node(agent: Any) -> Any:
    """Locate the ToolNode in a compiled `create_agent` graph, if reachable."""
    nodes = getattr(agent, "nodes", None)
    if not isinstance(nodes, dict):
        return None
    node = nodes.get("tools")
    if node is None:
        return None
    for attribute in ("bound", "runnable", "node", "func"):
        candidate = getattr(node, attribute, None)
        if candidate is not None and hasattr(candidate, "_handle_tool_errors"):
            return candidate
    return node if hasattr(node, "_handle_tool_errors") else None


def _tool_error_handler(agent: Any) -> Any:
    """Return the resolved `handle_tool_errors`, or a sentinel when unreachable."""
    if agent is None:
        return _UNKNOWN
    node = _tool_node(agent)
    if node is None:
        _LOG.debug(
            "could not locate the ToolNode to inspect handle_tool_errors; "
            "skipping that startup check"
        )
        return _UNKNOWN
    return getattr(node, "_handle_tool_errors", _UNKNOWN)


def _is_default_handler(handler: Any) -> bool:
    """Return whether the handler is LangGraph's default re-raising handler."""
    if handler is _UNKNOWN:
        return True
    try:
        from langgraph.prebuilt.tool_node import (
            _default_handle_tool_errors,
        )
    except ImportError:  # pragma: no cover - upstream rename
        _LOG.warning(
            "could not import langgraph's default tool-error handler; "
            "the handle_tool_errors startup check is degraded to a name comparison"
        )
        return getattr(handler, "__name__", "") == "_default_handle_tool_errors"
    return handler is _default_handle_tool_errors
