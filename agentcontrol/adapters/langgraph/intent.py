"""Build an `ActionIntent` from a LangGraph `ToolCallRequest`.

`ToolCallRequest` carries `tool_call` (name/args/id), `tool`, `state`, and `runtime`
(verified: refs/langgraph/libs/prebuilt/langgraph/prebuilt/tool_node.py:132-149). Trace
context comes from the ambient OpenTelemetry span rather than a minted identifier.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from agentcontrol.core.config import ContextResolvers
from agentcontrol.core.otel import current_trace_context
from agentcontrol.core.types import ActionIntent, ContextTrust

__all__ = ["build_action_intent", "resolution_context"]

_LOG = logging.getLogger(__name__)


def resolution_context(request: Any) -> dict[str, Any]:
    """Assemble the framework-agnostic context handed to configured resolvers."""
    runtime = getattr(request, "runtime", None)
    return {
        "tool_call": getattr(request, "tool_call", {}) or {},
        "tool": getattr(request, "tool", None),
        "state": getattr(request, "state", None),
        "runtime": runtime,
        "config": getattr(runtime, "config", None) or {},
    }


def _state_lookup(state: Any, key: str) -> Any:
    """Read a key from agent state, which may be a mapping or a model."""
    if isinstance(state, Mapping):
        return state.get(key)
    return getattr(state, key, None)


def _resolve(
    field: str,
    resolvers: ContextResolvers,
    context: Mapping[str, Any],
) -> Any:
    """Resolve one field from state first, then the configured resolver.

    A resolver that raises is treated as returning nothing. A broken resolver must not
    be able to fabricate a value the policy will authorize against.
    """
    state_key = resolvers.state_keys.get(field)
    if state_key:
        value = _state_lookup(context.get("state"), state_key)
        if value is not None:
            return value

    resolver = getattr(resolvers, field, None)
    if resolver is None:
        return None
    try:
        return resolver(context)
    except Exception:
        _LOG.exception(
            "resolver for %r raised; treating the field as unsupplied rather than "
            "guessing a value the policy will authorize against",
            field,
        )
        return None


def _thread_id(context: Mapping[str, Any]) -> str | None:
    """Extract the LangGraph thread id from the runtime config, if present."""
    config = context.get("config") or {}
    if not isinstance(config, Mapping):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return str(thread_id) if thread_id is not None else None


def _tool_type(tool: Any) -> str | None:
    """Map a LangChain tool onto a `gen_ai.tool.type` value.

    Only client-side tools reach `ToolNode`, so anything that arrives here is a
    function tool by the upstream taxonomy.
    """
    return "function" if tool is not None else None


def build_action_intent(
    request: Any,
    resolvers: ContextResolvers,
) -> ActionIntent:
    """Construct the `ActionIntent` for one intercepted tool call."""
    context = resolution_context(request)
    tool_call = context["tool_call"]
    trace_id, span_id = current_trace_context()

    agent_id = _resolve("agent_id", resolvers, context) or resolvers.default_agent_id
    trust = ContextTrust.coerce(_resolve("context_trust", resolvers, context))

    arguments = tool_call.get("args") or {}
    if not isinstance(arguments, dict):
        arguments = {"value": arguments}

    return ActionIntent(
        agent_id=str(agent_id),
        tool=str(tool_call.get("name") or ""),
        arguments=arguments,
        trace_id=trace_id,
        span_id=span_id,
        user_id=_optional_str(_resolve("user_id", resolvers, context)),
        task=_optional_str(_resolve("task", resolvers, context)),
        resource=_optional_str(_resolve("resource", resolvers, context)),
        context_trust=trust,
        context_source=_optional_str(_resolve("context_source", resolvers, context)),
        tool_call_id=_optional_str(tool_call.get("id")),
        thread_id=_thread_id(context),
        tool_type=_tool_type(context.get("tool")),
    )


def _optional_str(value: Any) -> str | None:
    """Coerce a resolved value to a string, preserving None."""
    return None if value is None else str(value)
