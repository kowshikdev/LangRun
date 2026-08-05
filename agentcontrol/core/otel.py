"""Span construction and attribute mapping.

Exactly one governance record per authorization decision (FR-019). For a call that
proceeds to execution, that means the decision attributes and the execution outcome
share **one** span — `governed_execution` wraps the tool call inside the same span
`record_decision` would have opened, rather than opening a second one. Records are
never sampled away: a sampled-away denial is an unauditable denial.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from agentcontrol.core import semconv
from agentcontrol.core.types import ActionIntent, ControlResult, Evidence, ReviewState, Verdict

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentcontrol.core.config import TelemetryConfig

__all__ = ["GovernanceRecorder", "current_trace_context"]

_INVALID_TRACE_ID = "0" * 32
_INVALID_SPAN_ID = "0" * 16


def current_trace_context() -> tuple[str, str]:
    """Return the ambient (trace_id, span_id) as lowercase hex.

    Returns all-zero identifiers when nothing is recording. A decision made without
    ambient context is still recorded and still attributable — it simply starts its own
    trace, and is flagged so a reviewer can tell the difference.
    """
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return _INVALID_TRACE_ID, _INVALID_SPAN_ID
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


def _json_safe(value: Any) -> str:
    """Render a value as a JSON string, falling back to repr for exotic objects."""
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return repr(value)


def _attribute_value(value: Any) -> Any:
    """Coerce a value into something the OTel SDK accepts as an attribute."""
    if isinstance(value, bool | str | int | float):
        return value
    return _json_safe(value)


class GovernanceRecorder:
    """Builds and emits governance spans.

    Holds no per-call state, so one instance is safe to share across concurrent tool
    calls.
    """

    def __init__(
        self,
        tracer: trace.Tracer,
        telemetry: TelemetryConfig,
        *,
        enforcement_capable: bool = True,
    ) -> None:
        """Store the tracer and the telemetry policy for this control plane."""
        self._tracer = tracer
        self._telemetry = telemetry
        self._enforcement_capable = enforcement_capable

    # ------------------------------------------------------------- terminal decisions

    def record_decision(
        self,
        intent: ActionIntent,
        result: ControlResult,
        *,
        latency_ms: int | None = None,
        tool_description: str | None = None,
        review_state: ReviewState | None = None,
        review_deadline: str | None = None,
        replay: bool = False,
    ) -> None:
        """Emit one governance record for a decision that does not proceed to execution.

        Use for `DENY`, a pending review, and a review resolved to reject/timeout. A
        decision that *does* proceed to execution (`ALLOW`, or a review resolved to
        approve) goes through `governed_execution` instead, so the decision and the
        outcome share one span rather than two.
        """
        attributes = self._decision_span_attributes(
            intent,
            result,
            latency_ms=latency_ms,
            tool_description=tool_description,
            review_state=review_state,
            review_deadline=review_deadline,
            replay=replay,
        )
        with self._tracer.start_as_current_span(
            semconv.span_name(intent.tool),
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        ) as span:
            error_type = self._decision_error_type(result, review_state)
            if error_type is not None:
                span.set_attribute(semconv.ERROR_TYPE, error_type)
                span.set_status(Status(StatusCode.ERROR, result.reason))
            else:
                span.set_status(Status(StatusCode.OK))

    # ---------------------------------------------------------- decision + execution

    @contextlib.contextmanager
    def governed_execution(
        self,
        intent: ActionIntent,
        result: ControlResult,
        *,
        latency_ms: int | None = None,
        tool_description: str | None = None,
        review_state: ReviewState | None = None,
        review_deadline: str | None = None,
        replay: bool = False,
    ) -> Iterator[trace.Span]:
        """Open the one span for a decision that proceeds to execution.

        Yields the span so the caller can attach the tool's outcome before it closes.
        A tool exception is recorded on this same span and re-raised; the caller does
        not need its own try/except around the handler call.
        """
        attributes = self._decision_span_attributes(
            intent,
            result,
            latency_ms=latency_ms,
            tool_description=tool_description,
            review_state=review_state,
            review_deadline=review_deadline,
            replay=replay,
        )
        with self._tracer.start_as_current_span(
            semconv.span_name(intent.tool),
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as exc:
                span.set_attribute(semconv.ERROR_TYPE, type(exc).__qualname__)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise
            else:
                span.set_status(Status(StatusCode.OK))

    def attach_result(self, span: trace.Span, result: Any) -> None:
        """Record a successful tool result on an open execution span.

        Opt-In upstream and off by default; a decision stays fully explainable without
        it (see `contracts/otel-attributes.md`).
        """
        if self._telemetry.record_tool_results:
            span.set_attribute(semconv.GEN_AI_TOOL_CALL_RESULT, _json_safe(result))

    # ----------------------------------------------------------------- internals

    def _decision_span_attributes(
        self,
        intent: ActionIntent,
        result: ControlResult,
        *,
        latency_ms: int | None,
        tool_description: str | None,
        review_state: ReviewState | None,
        review_deadline: str | None,
        replay: bool,
    ) -> dict[str, Any]:
        attributes = self._base_attributes(intent, tool_description)
        attributes.update(self._decision_attributes(result, latency_ms))
        attributes.update(self._evidence_attributes(result.evidence))
        if review_state is not None:
            attributes[semconv.AC_REVIEW_STATE] = review_state.value
            attributes[semconv.AC_REVIEW_HOLD_ID] = intent.hold_id
            if review_deadline is not None:
                attributes[semconv.AC_REVIEW_DEADLINE] = review_deadline
            if replay:
                attributes[semconv.AC_REVIEW_REPLAY] = True
        return attributes

    def _base_attributes(
        self, intent: ActionIntent, tool_description: str | None
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: semconv.OPERATION_EXECUTE_TOOL,
            semconv.GEN_AI_TOOL_NAME: intent.tool,
            semconv.GEN_AI_AGENT_ID: intent.agent_id,
            semconv.AC_CONTEXT_TRUST: intent.context_trust.value,
            semconv.AC_CAPABILITY_ENFORCEMENT: self._enforcement_capable,
        }
        if intent.tool_call_id:
            attributes[semconv.GEN_AI_TOOL_CALL_ID] = intent.tool_call_id
        if intent.tool_type:
            attributes[semconv.GEN_AI_TOOL_TYPE] = intent.tool_type
        if tool_description:
            attributes[semconv.GEN_AI_TOOL_DESCRIPTION] = tool_description
        if intent.thread_id:
            attributes[semconv.GEN_AI_CONVERSATION_ID] = intent.thread_id
        if intent.context_source:
            attributes[semconv.AC_CONTEXT_SOURCE] = intent.context_source
        if intent.resource:
            attributes[semconv.AC_ACTION_RESOURCE] = intent.resource
        if intent.is_orphaned:
            attributes[semconv.AC_CORRELATION_ORPHAN] = True
        # Opt-In upstream, and flagged as possibly sensitive. A decision stays fully
        # explainable without them, which is what lets them default off.
        if self._telemetry.record_tool_arguments:
            attributes[semconv.GEN_AI_TOOL_CALL_ARGUMENTS] = _json_safe(intent.arguments)
        return attributes

    @staticmethod
    def _decision_attributes(
        result: ControlResult, latency_ms: int | None
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            semconv.AC_POLICY_DECISION: result.verdict.value,
            semconv.AC_POLICY_PROVIDER: result.provider,
            semconv.AC_POLICY_REASON: result.reason,
            semconv.AC_POLICY_UNAVAILABLE: result.unavailable,
        }
        if result.policy_id:
            attributes[semconv.AC_POLICY_ID] = result.policy_id
        if result.decision_id:
            attributes[semconv.AC_POLICY_DECISION_ID] = result.decision_id
        if result.fail_mode_applied:
            attributes[semconv.AC_POLICY_FAIL_MODE] = result.fail_mode_applied
        if latency_ms is not None:
            attributes[semconv.AC_POLICY_LATENCY_MS] = latency_ms
        return attributes

    @staticmethod
    def _evidence_attributes(evidence: Iterable[Evidence]) -> Mapping[str, Any]:
        return {
            semconv.evidence_attribute(item.collector, item.signal): _attribute_value(
                item.value
            )
            for item in evidence
        }

    @staticmethod
    def _decision_error_type(
        result: ControlResult, review_state: ReviewState | None
    ) -> str | None:
        if review_state is ReviewState.TIMED_OUT:
            return semconv.ERROR_TYPE_REVIEW_TIMEOUT
        if review_state is ReviewState.REJECTED:
            return semconv.ERROR_TYPE_REVIEW_REJECTED
        if result.unavailable and result.verdict is Verdict.DENY:
            return semconv.ERROR_TYPE_POLICY_UNAVAILABLE
        if result.verdict is Verdict.DENY:
            return semconv.ERROR_TYPE_DENIED
        return None
