"""Span-shape tests: name, kind, and required gen_ai.* attributes. Task T015.

Full attribute-set coverage (allow/deny/review/unavailable, opt-in privacy,
correlation) lives in tests/unit/test_governance_attributes.py and
tests/unit/test_attribute_optin.py (US2, T042-T043) — this file only proves the
Foundational-phase contract: every tool call produces a correctly shaped span before
any policy exists.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from agentcontrol.core import semconv
from agentcontrol.core.types import ActionIntent, ControlResult, Verdict
from tests.unit.conftest import RecordedSpans


def _intent(**overrides: object) -> ActionIntent:
    defaults: dict[str, object] = {
        "agent_id": "agent-1",
        "tool": "search",
        "arguments": {"q": "hello"},
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "tool_call_id": "call-1",
    }
    defaults.update(overrides)
    return ActionIntent(**defaults)  # type: ignore[arg-type]


def _only_span(exporter: InMemorySpanExporter):
    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"expected exactly one span, got {len(spans)}"
    return spans[0]


class TestSpanShape:
    def test_span_name_is_execute_tool_plus_tool_name(self, recorded: RecordedSpans) -> None:
        intent = _intent(tool="github.create_issue")
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        recorded.recorder.record_decision(intent, result)
        span = _only_span(recorded.exporter)
        assert span.name == "execute_tool github.create_issue"

    def test_span_kind_is_internal(self, recorded: RecordedSpans) -> None:
        intent = _intent()
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        recorded.recorder.record_decision(intent, result)
        span = _only_span(recorded.exporter)
        assert span.kind is SpanKind.INTERNAL

    def test_required_gen_ai_attributes_present(self, recorded: RecordedSpans) -> None:
        intent = _intent()
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        recorded.recorder.record_decision(intent, result)
        span = _only_span(recorded.exporter)
        assert span.attributes[semconv.GEN_AI_OPERATION_NAME] == "execute_tool"
        assert span.attributes[semconv.GEN_AI_TOOL_NAME] == "search"
        assert span.attributes[semconv.GEN_AI_TOOL_CALL_ID] == "call-1"

    def test_allow_status_is_ok(self, recorded: RecordedSpans) -> None:
        intent = _intent()
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        recorded.recorder.record_decision(intent, result)
        span = _only_span(recorded.exporter)
        assert span.status.status_code is StatusCode.OK

    def test_deny_status_is_error_with_error_type(self, recorded: RecordedSpans) -> None:
        intent = _intent()
        result = ControlResult(
            verdict=Verdict.DENY, provider="opa", reason="blocked", policy_id="rule-1"
        )
        recorded.recorder.record_decision(intent, result)
        span = _only_span(recorded.exporter)
        assert span.status.status_code is StatusCode.ERROR
        assert span.attributes[semconv.ERROR_TYPE] == "agentcontrol.denied"
