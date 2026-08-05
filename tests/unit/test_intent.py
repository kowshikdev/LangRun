"""Intent-construction tests. Task T018.

`build_action_intent` is the FR-032 implementation: every identity/context field the
policy authorizes against goes through an explicit resolver with a defined,
never-more-permissive-than-unknown fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentcontrol.adapters.langgraph.intent import build_action_intent
from agentcontrol.core.config import ContextResolvers
from agentcontrol.core.types import ContextTrust


def _request(
    *,
    tool_call: dict[str, Any] | None = None,
    tool: Any = "sentinel-tool",
    state: Any = None,
    thread_id: str | None = "thread-1",
) -> Any:
    configurable = {"thread_id": thread_id} if thread_id else {}
    runtime = SimpleNamespace(config={"configurable": configurable})
    return SimpleNamespace(
        tool_call=tool_call or {"name": "search", "args": {"q": "x"}, "id": "call-1"},
        tool=tool,
        state=state if state is not None else {},
        runtime=runtime,
    )


class TestTrustDefaults:
    def test_trust_defaults_to_unknown_with_no_resolver_input(self) -> None:
        intent = build_action_intent(_request(), ContextResolvers())
        assert intent.context_trust is ContextTrust.UNKNOWN

    def test_unrecognized_state_trust_coerces_to_unknown(self) -> None:
        state = {"agentcontrol_context_trust": "extremely-safe-i-promise"}
        intent = build_action_intent(_request(state=state), ContextResolvers())
        assert intent.context_trust is ContextTrust.UNKNOWN

    def test_recognized_state_trust_is_honored(self) -> None:
        state = {"agentcontrol_context_trust": "untrusted"}
        intent = build_action_intent(_request(state=state), ContextResolvers())
        assert intent.context_trust is ContextTrust.UNTRUSTED


class TestTraceContext:
    def test_no_ambient_span_yields_all_zero_ids_and_orphan_flag(self) -> None:
        intent = build_action_intent(_request(), ContextResolvers())
        assert set(intent.trace_id) <= {"0"}
        assert set(intent.span_id) <= {"0"}
        assert intent.is_orphaned is True

    def test_ambient_span_is_used_and_not_orphaned(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")
        try:
            with tracer.start_as_current_span("outer"):
                intent = build_action_intent(_request(), ContextResolvers())
            assert intent.is_orphaned is False
            assert intent.trace_id != "0" * 32
        finally:
            provider.shutdown()


class TestRequiredFieldValidation:
    def test_empty_tool_name_raises(self) -> None:
        request = _request(tool_call={"name": "", "args": {}, "id": "x"})
        try:
            build_action_intent(request, ContextResolvers())
        except ValueError as exc:
            assert "tool" in str(exc)
        else:
            raise AssertionError("expected ValueError for empty tool name")


class TestResolvers:
    def test_state_key_resolves_resource(self) -> None:
        state = {"agentcontrol_resource": "company/production"}
        intent = build_action_intent(_request(state=state), ContextResolvers())
        assert intent.resource == "company/production"

    def test_callable_resolver_used_when_state_key_absent(self) -> None:
        resolvers = ContextResolvers(resource=lambda ctx: "resolved-from-callable")
        intent = build_action_intent(_request(), resolvers)
        assert intent.resource == "resolved-from-callable"

    def test_state_key_takes_priority_over_callable_resolver(self) -> None:
        resolvers = ContextResolvers(resource=lambda ctx: "from-callable")
        state = {"agentcontrol_resource": "from-state"}
        intent = build_action_intent(_request(state=state), resolvers)
        assert intent.resource == "from-state"

    def test_raising_resolver_yields_none_not_a_crash(self) -> None:
        def broken(_: dict[str, Any]) -> str:
            raise RuntimeError("boom")

        resolvers = ContextResolvers(resource=broken)
        intent = build_action_intent(_request(), resolvers)
        assert intent.resource is None

    def test_default_agent_id_used_when_unresolved(self) -> None:
        intent = build_action_intent(_request(), ContextResolvers())
        assert intent.agent_id == ContextResolvers().default_agent_id

    def test_thread_id_read_from_runtime_config(self) -> None:
        intent = build_action_intent(_request(thread_id="thread-42"), ContextResolvers())
        assert intent.thread_id == "thread-42"

    def test_missing_thread_id_is_none(self) -> None:
        intent = build_action_intent(_request(thread_id=None), ContextResolvers())
        assert intent.thread_id is None


class TestToolType:
    def test_registered_tool_is_function_type(self) -> None:
        intent = build_action_intent(_request(tool="a-real-tool"), ContextResolvers())
        assert intent.tool_type == "function"

    def test_unregistered_tool_has_no_type(self) -> None:
        intent = build_action_intent(_request(tool=None), ContextResolvers())
        assert intent.tool_type is None
