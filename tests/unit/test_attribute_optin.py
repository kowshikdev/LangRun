"""Task T043: tool arguments and results are absent by default, present only when
the telemetry flags are set. Both are marked Opt-In and possibly sensitive upstream
(research R4); a decision must stay explainable without them.
"""

from __future__ import annotations

from collections.abc import Callable

from agentcontrol.core import semconv
from agentcontrol.core.config import TelemetryConfig
from agentcontrol.core.types import ActionIntent, ControlResult, Verdict
from tests.unit.conftest import RecordedSpans


def _intent() -> ActionIntent:
    return ActionIntent(
        agent_id="a",
        tool="search",
        arguments={"q": "sensitive query"},
        trace_id="a" * 32,
        span_id="b" * 16,
    )


class TestArgumentsOptIn:
    def test_arguments_absent_by_default(
        self, recorded_factory: Callable[..., RecordedSpans]
    ) -> None:
        built = recorded_factory(TelemetryConfig())
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        built.recorder.record_decision(_intent(), result)
        attrs = built.exporter.get_finished_spans()[0].attributes
        assert semconv.GEN_AI_TOOL_CALL_ARGUMENTS not in attrs

    def test_arguments_present_when_enabled(
        self, recorded_factory: Callable[..., RecordedSpans]
    ) -> None:
        built = recorded_factory(TelemetryConfig(record_tool_arguments=True))
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        built.recorder.record_decision(_intent(), result)
        attrs = built.exporter.get_finished_spans()[0].attributes
        assert "sensitive query" in attrs[semconv.GEN_AI_TOOL_CALL_ARGUMENTS]


class TestResultsOptIn:
    def test_result_absent_by_default(
        self, recorded_factory: Callable[..., RecordedSpans]
    ) -> None:
        built = recorded_factory(TelemetryConfig())
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        with built.recorder.governed_execution(_intent(), result) as span:
            built.recorder.attach_result(span, "some secret result")
        attrs = built.exporter.get_finished_spans()[0].attributes
        assert semconv.GEN_AI_TOOL_CALL_RESULT not in attrs

    def test_result_present_when_enabled(
        self, recorded_factory: Callable[..., RecordedSpans]
    ) -> None:
        built = recorded_factory(TelemetryConfig(record_tool_results=True))
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        with built.recorder.governed_execution(_intent(), result) as span:
            built.recorder.attach_result(span, "some result")
        attrs = built.exporter.get_finished_spans()[0].attributes
        assert "some result" in attrs[semconv.GEN_AI_TOOL_CALL_RESULT]

    def test_decision_stays_explainable_without_either(
        self, recorded_factory: Callable[..., RecordedSpans]
    ) -> None:
        """SC-004's premise: name, resource, trust, verdict, and rule id are enough."""
        built = recorded_factory(TelemetryConfig())
        result = ControlResult(
            verdict=Verdict.DENY, provider="opa", reason="blocked", policy_id="p.deny"
        )
        built.recorder.record_decision(_intent(), result)
        attrs = built.exporter.get_finished_spans()[0].attributes
        assert semconv.GEN_AI_TOOL_CALL_ARGUMENTS not in attrs
        assert semconv.GEN_AI_TOOL_CALL_RESULT not in attrs
        assert attrs[semconv.GEN_AI_TOOL_NAME] == "search"
        assert attrs[semconv.AC_POLICY_DECISION] == "deny"
        assert attrs[semconv.AC_POLICY_ID] == "p.deny"
