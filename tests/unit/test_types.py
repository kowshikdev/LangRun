"""Invariant tests for core types.

See specs/001-agentcontrol-runtime-governance/data-model.md for the invariants these
enforce. Task T011.
"""

from __future__ import annotations

import pytest

from agentcontrol.core.types import (
    ActionIntent,
    ContextTrust,
    ControlResult,
    Evidence,
    RequiredCapabilities,
    ReviewState,
    Verdict,
)


class TestVerdict:
    def test_abstain_is_not_enforceable(self) -> None:
        assert Verdict.ABSTAIN not in Verdict.enforceable()

    def test_allow_deny_review_are_enforceable(self) -> None:
        assert Verdict.enforceable() == {Verdict.ALLOW, Verdict.DENY, Verdict.REVIEW}


class TestControlResult:
    def test_abstain_rejected(self) -> None:
        with pytest.raises(ValueError, match="not an authorization outcome"):
            ControlResult(verdict=Verdict.ABSTAIN, provider="opa", reason="no opinion")

    def test_reason_required_even_for_allow(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="")

    def test_unavailable_forces_no_policy_id(self) -> None:
        with pytest.raises(ValueError, match="policy_id must be None"):
            ControlResult(
                verdict=Verdict.DENY,
                provider="opa",
                reason="down",
                unavailable=True,
                fail_mode_applied="closed",
                policy_id="should-not-be-set",
            )

    def test_unavailable_requires_fail_mode_applied(self) -> None:
        with pytest.raises(ValueError, match="fail_mode_applied"):
            ControlResult(
                verdict=Verdict.DENY, provider="opa", reason="down", unavailable=True
            )

    def test_unavailable_closed_is_valid(self) -> None:
        result = ControlResult(
            verdict=Verdict.DENY,
            provider="opa",
            reason="down",
            unavailable=True,
            fail_mode_applied="closed",
        )
        assert result.policy_id is None
        assert result.blocks_execution is True

    def test_review_requires_timeout(self) -> None:
        with pytest.raises(ValueError, match="review_timeout_seconds"):
            ControlResult(verdict=Verdict.REVIEW, provider="opa", reason="hold")

    def test_review_with_timeout_is_valid(self) -> None:
        result = ControlResult(
            verdict=Verdict.REVIEW,
            provider="opa",
            reason="hold",
            review_timeout_seconds=900,
        )
        assert result.blocks_execution is True

    def test_allow_does_not_block(self) -> None:
        result = ControlResult(verdict=Verdict.ALLOW, provider="opa", reason="fine")
        assert result.blocks_execution is False


class TestEvidence:
    def test_requires_collector_and_signal(self) -> None:
        with pytest.raises(ValueError, match="collector"):
            Evidence(collector="", signal="x", value=1)
        with pytest.raises(ValueError, match="signal"):
            Evidence(collector="x", signal="", value=1)

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_out_of_range_rejected(self, confidence: float) -> None:
        with pytest.raises(ValueError, match="confidence"):
            Evidence(collector="x", signal="y", value=1, confidence=confidence)

    def test_confidence_boundaries_accepted(self) -> None:
        Evidence(collector="x", signal="y", value=1, confidence=0.0)
        Evidence(collector="x", signal="y", value=1, confidence=1.0)


class TestActionIntent:
    def _build(self, **overrides: object) -> ActionIntent:
        defaults: dict[str, object] = {
            "agent_id": "agent-1",
            "tool": "search",
            "arguments": {},
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
        }
        defaults.update(overrides)
        return ActionIntent(**defaults)  # type: ignore[arg-type]

    def test_empty_tool_rejected(self) -> None:
        with pytest.raises(ValueError, match="tool"):
            self._build(tool="")

    def test_empty_agent_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="agent_id"):
            self._build(agent_id="")

    def test_default_trust_is_unknown(self) -> None:
        intent = self._build()
        assert intent.context_trust is ContextTrust.UNKNOWN

    def test_orphaned_when_trace_context_is_all_zero(self) -> None:
        intent = self._build(trace_id="0" * 32, span_id="0" * 16)
        assert intent.is_orphaned is True

    def test_not_orphaned_with_real_trace_context(self) -> None:
        intent = self._build()
        assert intent.is_orphaned is False

    def test_hold_id_combines_thread_and_call(self) -> None:
        intent = self._build(thread_id="t-1", tool_call_id="call-1")
        assert intent.hold_id == "t-1:call-1"

    def test_hold_id_has_placeholder_for_missing_fields(self) -> None:
        intent = self._build()
        assert intent.hold_id == "-:-"

    def test_policy_input_groups_evidence_by_collector(self) -> None:
        intent = self._build(context_trust=ContextTrust.UNTRUSTED)
        evidence = [
            Evidence(collector="nemo_injection", signal="score", value=0.9),
            Evidence(collector="presidio", signal="pii_detected", value=False),
        ]
        payload = intent.to_policy_input(evidence)
        assert payload["evidence"] == {
            "nemo_injection": {"score": 0.9},
            "presidio": {"pii_detected": False},
        }
        assert payload["context"]["trust"] == "untrusted"

    def test_policy_input_empty_evidence_is_empty_object(self) -> None:
        intent = self._build()
        assert intent.to_policy_input() == intent.to_policy_input([])
        assert intent.to_policy_input()["evidence"] == {}


class TestContextTrustCoerce:
    def test_none_defaults_to_unknown(self) -> None:
        assert ContextTrust.coerce(None) is ContextTrust.UNKNOWN

    def test_unrecognized_string_defaults_to_unknown_not_trusted(self) -> None:
        assert ContextTrust.coerce("definitely-safe") is ContextTrust.UNKNOWN

    def test_recognized_values_pass_through(self) -> None:
        assert ContextTrust.coerce("trusted") is ContextTrust.TRUSTED
        assert ContextTrust.coerce("untrusted") is ContextTrust.UNTRUSTED

    def test_case_and_whitespace_insensitive(self) -> None:
        assert ContextTrust.coerce("  Trusted  ") is ContextTrust.TRUSTED

    def test_enum_value_passes_through_unchanged(self) -> None:
        assert ContextTrust.coerce(ContextTrust.TRUSTED) is ContextTrust.TRUSTED


class TestReviewState:
    def test_pending_is_not_terminal(self) -> None:
        assert ReviewState.PENDING.is_terminal is False

    @pytest.mark.parametrize(
        "state", [ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.TIMED_OUT]
    )
    def test_resolved_states_are_terminal(self, state: ReviewState) -> None:
        assert state.is_terminal is True


class TestRequiredCapabilities:
    def test_block_before_tool_defaults_true(self) -> None:
        assert RequiredCapabilities().block_before_tool is True

    def test_required_names_reflects_true_fields_only(self) -> None:
        required = RequiredCapabilities(human_approval=True, modify_tool_arguments=False)
        assert set(required.required_names()) == {"block_before_tool", "human_approval"}

    def test_requires_for_deny_is_block_before_tool(self) -> None:
        assert RequiredCapabilities().requires_for(Verdict.DENY) == "block_before_tool"

    def test_requires_for_review_is_human_approval(self) -> None:
        assert RequiredCapabilities().requires_for(Verdict.REVIEW) == "human_approval"

    def test_requires_for_allow_is_none(self) -> None:
        assert RequiredCapabilities().requires_for(Verdict.ALLOW) is None
