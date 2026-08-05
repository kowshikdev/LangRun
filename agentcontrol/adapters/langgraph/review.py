"""Human-review holds.

`interrupt()` raises `GraphInterrupt`, and on resume LangGraph re-executes the node from
the start (verified: refs/langgraph/libs/langgraph/langgraph/types.py:811-899, note at
:824). Two consequences shape this module:

1. The deadline travels *inside* the interrupt payload, so it is persisted in the host's
   checkpointer. A restart therefore cannot extend a window, and an expired hold can
   never resolve to approval no matter how long the process was down.
2. The middleware body runs twice per held call, so everything here is idempotent and
   keyed by `hold_id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from agentcontrol.core.types import (
    ActionIntent,
    ControlResult,
    ReviewDecision,
    ReviewState,
    Verdict,
)

__all__ = [
    "TIMEOUT_SENTINEL",
    "ReviewHold",
    "ReviewOutcome",
    "build_hold",
    "resolve",
]

#: Value the watchdog resumes an expired hold with.
TIMEOUT_SENTINEL = "__agentcontrol_timeout__"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReviewHold:
    """The persisted state of a paused decision."""

    hold_id: str
    tool: str
    resource: str | None
    reason: str
    policy_id: str | None
    deadline: str
    requested_at: str

    def to_payload(self) -> dict[str, Any]:
        """Render the value handed to `interrupt()` and stored in the checkpoint."""
        return {
            "agentcontrol": {
                "hold_id": self.hold_id,
                "tool": self.tool,
                "resource": self.resource,
                "reason": self.reason,
                "policy_id": self.policy_id,
                "deadline": self.deadline,
                "requested_at": self.requested_at,
            }
        }

    @classmethod
    def from_payload(cls, payload: Any) -> ReviewHold | None:
        """Recover a hold from a persisted interrupt payload, if it is one of ours."""
        if not isinstance(payload, dict):
            return None
        data = payload.get("agentcontrol")
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                hold_id=str(data["hold_id"]),
                tool=str(data["tool"]),
                resource=data.get("resource"),
                reason=str(data.get("reason", "")),
                policy_id=data.get("policy_id"),
                deadline=str(data["deadline"]),
                requested_at=str(data.get("requested_at", "")),
            )
        except KeyError:
            return None

    @property
    def deadline_at(self) -> datetime:
        """Return the parsed absolute deadline."""
        return datetime.fromisoformat(self.deadline)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return whether the review window has closed."""
        return (now or _now()) > self.deadline_at


@dataclass(frozen=True)
class ReviewOutcome:
    """What a hold resolved to, and the result to act on."""

    state: ReviewState
    result: ControlResult


def build_hold(
    intent: ActionIntent,
    result: ControlResult,
    *,
    fallback_timeout_seconds: int,
    now: datetime | None = None,
) -> ReviewHold:
    """Create a hold whose deadline is absolute and therefore durable."""
    started = now or _now()
    window = result.review_timeout_seconds or fallback_timeout_seconds
    return ReviewHold(
        hold_id=intent.hold_id,
        tool=intent.tool,
        resource=intent.resource,
        reason=result.reason,
        policy_id=result.policy_id,
        deadline=(started + timedelta(seconds=window)).isoformat(),
        requested_at=started.isoformat(),
    )


def _parse_decision(response: Any) -> tuple[ReviewDecision | None, str | None, str | None]:
    """Extract (decision, actor, reason) from a resume payload."""
    if response == TIMEOUT_SENTINEL:
        return None, None, "review window expired"
    if isinstance(response, str):
        try:
            return ReviewDecision(response.strip().lower()), None, None
        except ValueError:
            return None, None, f"unrecognized resume value {response!r}"
    if isinstance(response, dict):
        raw = response.get("decision")
        actor = response.get("actor")
        reason = response.get("reason")
        try:
            return ReviewDecision(str(raw).strip().lower()), actor, reason
        except ValueError:
            return None, actor, reason or f"unrecognized resume decision {raw!r}"
    return None, None, f"unrecognized resume payload of type {type(response).__name__}"


def resolve(
    hold: ReviewHold,
    response: Any,
    result: ControlResult,
    *,
    now: datetime | None = None,
) -> ReviewOutcome:
    """Resolve a hold against a human response and the persisted deadline.

    Expiry is checked against the deadline read back from the hold, never a value held
    in memory, and it overrides an approval unconditionally. An unanswered or
    late-answered hold can never become an ALLOW.
    """
    decision, actor, reason = _parse_decision(response)

    if hold.is_expired(now=now):
        return ReviewOutcome(
            state=ReviewState.TIMED_OUT,
            result=ControlResult(
                verdict=Verdict.DENY,
                provider=result.provider,
                reason=(
                    f"review window expired at {hold.deadline}; "
                    "an unanswered hold resolves to deny"
                ),
                evidence=result.evidence,
                policy_id=result.policy_id,
                decision_id=result.decision_id,
            ),
        )

    if decision is ReviewDecision.APPROVE:
        who = f" by {actor}" if actor else ""
        return ReviewOutcome(
            state=ReviewState.APPROVED,
            result=ControlResult(
                verdict=Verdict.ALLOW,
                provider=result.provider,
                reason=f"approved{who} after review: {result.reason}",
                evidence=result.evidence,
                policy_id=result.policy_id,
                decision_id=result.decision_id,
            ),
        )

    who = f" by {actor}" if actor else ""
    detail = reason or "no reason given"
    return ReviewOutcome(
        state=ReviewState.REJECTED,
        result=ControlResult(
            verdict=Verdict.DENY,
            provider=result.provider,
            reason=f"rejected{who} after review: {detail}",
            evidence=result.evidence,
            policy_id=result.policy_id,
            decision_id=result.decision_id,
        ),
    )
