"""Task T042: the full agentcontrol.* attribute set, for every verdict shape.

Matches the worked examples in
specs/001-agentcontrol-runtime-governance/contracts/otel-attributes.md.
"""

from __future__ import annotations

from agentcontrol.core import semconv
from agentcontrol.core.types import ActionIntent, ContextTrust, ControlResult, Evidence, Verdict
from tests.unit.conftest import RecordedSpans


def _intent(**overrides: object) -> ActionIntent:
    defaults: dict[str, object] = {
        "agent_id": "finance-agent",
        "tool": "github.delete_repository",
        "arguments": {"repo": "company/production"},
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "tool_call_id": "call_a1b2",
        "resource": "company/production",
        "context_trust": ContextTrust.UNTRUSTED,
        "context_source": "sharepoint_document",
        "thread_id": "t-42",
        "tool_type": "function",
    }
    defaults.update(overrides)
    return ActionIntent(**defaults)  # type: ignore[arg-type]


class TestAllowAttributes:
    def test_full_attribute_set_present(self, recorded: RecordedSpans) -> None:
        intent = _intent(context_trust=ContextTrust.TRUSTED)
        result = ControlResult(
            verdict=Verdict.ALLOW,
            provider="opa",
            reason="fine",
            policy_id="agentcontrol.authz.default_allow",
            decision_id="d-1",
        )
        with recorded.recorder.governed_execution(intent, result, latency_ms=11) as span:
            recorded.recorder.attach_result(span, "unused-by-default")

        span = recorded.exporter.get_finished_spans()[0]
        attrs = span.attributes
        assert attrs[semconv.AC_POLICY_DECISION] == "allow"
        assert attrs[semconv.AC_POLICY_PROVIDER] == "opa"
        assert attrs[semconv.AC_POLICY_ID] == "agentcontrol.authz.default_allow"
        assert attrs[semconv.AC_POLICY_DECISION_ID] == "d-1"
        assert attrs[semconv.AC_POLICY_UNAVAILABLE] is False
        assert attrs[semconv.AC_POLICY_LATENCY_MS] == 11
        assert attrs[semconv.AC_CONTEXT_TRUST] == "trusted"
        assert attrs[semconv.AC_CONTEXT_SOURCE] == "sharepoint_document"
        assert attrs[semconv.AC_ACTION_RESOURCE] == "company/production"
        assert attrs[semconv.AC_CAPABILITY_ENFORCEMENT] is True


class TestDenyAttributes:
    def test_worked_example_denied_action(self, recorded: RecordedSpans) -> None:
        intent = _intent()
        result = ControlResult(
            verdict=Verdict.DENY,
            provider="opa",
            reason="destructive tool is blocked unconditionally",
            policy_id="agentcontrol.authz.deny_destructive_tool",
            decision_id="b1f2c3d4",
        )
        recorded.recorder.record_decision(intent, result, latency_ms=11)

        span = recorded.exporter.get_finished_spans()[0]
        attrs = span.attributes
        assert attrs[semconv.AC_POLICY_DECISION] == "deny"
        assert attrs[semconv.AC_POLICY_ID] == "agentcontrol.authz.deny_destructive_tool"
        assert attrs[semconv.AC_POLICY_DECISION_ID] == "b1f2c3d4"
        assert attrs[semconv.AC_POLICY_UNAVAILABLE] is False
        assert attrs[semconv.AC_CONTEXT_TRUST] == "untrusted"
        assert attrs[semconv.ERROR_TYPE] == "agentcontrol.denied"


class TestUnavailableAttributes:
    def test_worked_example_opa_unreachable(self, recorded: RecordedSpans) -> None:
        intent = _intent()
        result = ControlResult(
            verdict=Verdict.DENY,
            provider="opa",
            reason="policy provider unreachable: ConnectError",
            unavailable=True,
            fail_mode_applied="closed",
        )
        recorded.recorder.record_decision(intent, result)

        span = recorded.exporter.get_finished_spans()[0]
        attrs = span.attributes
        assert attrs[semconv.AC_POLICY_DECISION] == "deny"
        assert attrs[semconv.AC_POLICY_UNAVAILABLE] is True
        assert attrs[semconv.AC_POLICY_FAIL_MODE] == "closed"
        assert semconv.AC_POLICY_ID not in attrs
        assert attrs[semconv.ERROR_TYPE] == "agentcontrol.policy_unavailable"


class TestReviewAttributes:
    def test_pending_review_carries_state_and_deadline(self, recorded: RecordedSpans) -> None:
        from agentcontrol.core.types import ReviewState

        intent = _intent(thread_id="t-42", tool_call_id="call-9")
        result = ControlResult(
            verdict=Verdict.REVIEW, provider="opa", reason="hold", review_timeout_seconds=900
        )
        recorded.recorder.record_decision(
            intent,
            result,
            review_state=ReviewState.PENDING,
            review_deadline="2026-08-04T12:15:00+00:00",
        )
        span = recorded.exporter.get_finished_spans()[0]
        attrs = span.attributes
        assert attrs[semconv.AC_REVIEW_STATE] == "pending"
        assert attrs[semconv.AC_REVIEW_HOLD_ID] == "t-42:call-9"
        assert attrs[semconv.AC_REVIEW_DEADLINE] == "2026-08-04T12:15:00+00:00"


class TestEvidenceAttributes:
    def test_each_signal_gets_its_own_namespaced_attribute(
        self, recorded: RecordedSpans
    ) -> None:
        intent = _intent()
        result = ControlResult(
            verdict=Verdict.DENY,
            provider="opa",
            reason="blocked",
            evidence=(
                Evidence(collector="nemo_injection", signal="score", value=0.91),
                Evidence(collector="presidio", signal="pii_detected", value=False),
            ),
        )
        recorded.recorder.record_decision(intent, result)
        attrs = recorded.exporter.get_finished_spans()[0].attributes
        assert attrs[semconv.evidence_attribute("nemo_injection", "score")] == 0.91
        assert attrs[semconv.evidence_attribute("presidio", "pii_detected")] is False

    def test_empty_evidence_produces_no_evidence_attributes(
        self, recorded: RecordedSpans
    ) -> None:
        intent = _intent()
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        recorded.recorder.record_decision(intent, result)
        attrs = recorded.exporter.get_finished_spans()[0].attributes
        assert not any(key.startswith(semconv.AC_EVIDENCE_PREFIX) for key in attrs)
