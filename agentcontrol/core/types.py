"""Core domain types for AgentControl.

Field names follow `specs/001-agentcontrol-runtime-governance/data-model.md`. Invariants
stated there are enforced in `__post_init__` rather than left to callers: a malformed
`ControlResult` is a governance failure, and governance failures must be loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

__all__ = [
    "ABSTAIN_NOT_A_VERDICT",
    "ActionIntent",
    "AsyncAnalyzer",
    "CapabilityManifest",
    "ContextTrust",
    "ControlResult",
    "Evidence",
    "FrameworkAdapter",
    "InlineControl",
    "PolicyProvider",
    "RequiredCapabilities",
    "ReviewDecision",
    "ReviewState",
    "Verdict",
]

ABSTAIN_NOT_A_VERDICT = (
    "ABSTAIN is an evidence-collector opinion, not an authorization outcome. "
    "Only allow, deny, and review may appear in a ControlResult."
)


class Verdict(str, Enum):
    """Outcome of an authorization decision.

    `ABSTAIN` exists so an evidence collector can say "no opinion". It is never an
    authorization outcome and is rejected by `ControlResult`.
    """

    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"
    ABSTAIN = "abstain"

    @classmethod
    def enforceable(cls) -> frozenset[Verdict]:
        """Return the verdicts a policy provider is allowed to return."""
        return frozenset({cls.ALLOW, cls.DENY, cls.REVIEW})


class ContextTrust(str, Enum):
    """Trust level of the context that influenced an action.

    The default is `UNKNOWN`, never `TRUSTED`. An application that supplies nothing
    must not thereby be treated as having supplied an assurance.
    """

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, value: str | ContextTrust | None) -> ContextTrust:
        """Map an arbitrary value onto a trust level, defaulting to `UNKNOWN`.

        An unrecognized string is not an error — the policy decides what `unknown`
        means — but it must never resolve upward to `TRUSTED`.
        """
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.UNKNOWN
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.UNKNOWN


class ReviewState(str, Enum):
    """Terminal and non-terminal states of a human-review hold."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        """Return whether no further transition is possible from this state."""
        return self is not ReviewState.PENDING


class ReviewDecision(str, Enum):
    """What a human said about a held action."""

    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True)
class Evidence:
    """A structured observation about an action intent.

    Never a verdict. A collector that returns something verdict-shaped is still just
    producing data; only the policy provider authorizes.
    """

    collector: str
    signal: str
    value: Any
    confidence: float | None = None

    def __post_init__(self) -> None:
        """Validate collector, signal, and confidence range."""
        if not self.collector:
            msg = "Evidence.collector must be non-empty"
            raise ValueError(msg)
        if not self.signal:
            msg = "Evidence.signal must be non-empty"
            raise ValueError(msg)
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            msg = f"Evidence.confidence must be in [0.0, 1.0], got {self.confidence}"
            raise ValueError(msg)


@dataclass(frozen=True)
class ActionIntent:
    """One proposed tool call, captured before execution."""

    agent_id: str
    tool: str
    arguments: dict[str, Any]
    trace_id: str
    span_id: str
    user_id: str | None = None
    task: str | None = None
    resource: str | None = None
    context_trust: ContextTrust = ContextTrust.UNKNOWN
    context_source: str | None = None
    tool_call_id: str | None = None
    thread_id: str | None = None
    tool_type: str | None = None

    def __post_init__(self) -> None:
        """Reject a half-built intent rather than sending it to a policy engine."""
        if not self.agent_id:
            msg = "ActionIntent.agent_id must be non-empty"
            raise ValueError(msg)
        if not self.tool:
            msg = "ActionIntent.tool must be non-empty"
            raise ValueError(msg)

    @property
    def is_orphaned(self) -> bool:
        """Return whether this intent was built with no ambient trace context."""
        return set(self.trace_id) <= {"0"} or set(self.span_id) <= {"0"}

    @property
    def hold_id(self) -> str:
        """Return the idempotency key for a review hold on this intent."""
        return f"{self.thread_id or '-'}:{self.tool_call_id or '-'}"

    def to_policy_input(self, evidence: Sequence[Evidence] = ()) -> dict[str, Any]:
        """Render the OPA input document.

        Shape is fixed by `contracts/opa-input.schema.json`; changing it is a contract
        change, not an implementation detail.
        """
        grouped: dict[str, dict[str, Any]] = {}
        for item in evidence:
            grouped.setdefault(item.collector, {})[item.signal] = item.value
        return {
            "agent": {"id": self.agent_id},
            "user": {"id": self.user_id},
            "task": self.task,
            "action": {
                "tool": self.tool,
                "arguments": self.arguments,
                "resource": self.resource,
                "tool_type": self.tool_type,
            },
            "context": {
                "trust": self.context_trust.value,
                "source": self.context_source,
            },
            "evidence": grouped,
            "trace": {
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "thread_id": self.thread_id,
            },
        }


@dataclass(frozen=True)
class ControlResult:
    """The authorization outcome for one action intent."""

    verdict: Verdict
    provider: str
    reason: str
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    policy_id: str | None = None
    decision_id: str | None = None
    unavailable: bool = False
    fail_mode_applied: str | None = None
    review_timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        """Enforce the data-model invariants that keep the audit trail honest."""
        if self.verdict is Verdict.ABSTAIN:
            raise ValueError(ABSTAIN_NOT_A_VERDICT)
        if not self.reason:
            msg = "ControlResult.reason must be non-empty, including for allow"
            raise ValueError(msg)
        if self.unavailable:
            if self.policy_id is not None:
                msg = (
                    "ControlResult.policy_id must be None when unavailable=True: "
                    "a fallback did not fire a rule"
                )
                raise ValueError(msg)
            if self.fail_mode_applied not in {"closed", "open"}:
                msg = "ControlResult.fail_mode_applied must be 'closed' or 'open' when unavailable"
                raise ValueError(msg)
        if self.verdict is Verdict.REVIEW and self.review_timeout_seconds is None:
            msg = "ControlResult.review_timeout_seconds is required for a review verdict"
            raise ValueError(msg)

    @property
    def blocks_execution(self) -> bool:
        """Return whether this result prevents the tool from running now."""
        return self.verdict in {Verdict.DENY, Verdict.REVIEW}


@dataclass(frozen=True)
class CapabilityManifest:
    """What a framework adapter can actually observe, intercept, and block.

    Every field is a claim that must be proven by an executable conformance test
    against the real runtime — see `contracts/capability-manifest.md`.
    """

    observe_model_calls: bool
    observe_tool_calls: bool
    intercept_model_input: bool
    intercept_model_output: bool
    intercept_function_tools: bool
    intercept_mcp_tools: bool
    intercept_hosted_tools: bool
    block_before_tool: bool
    modify_tool_arguments: bool
    human_approval: bool
    streaming_interception: bool

    def supports(self, capability: str) -> bool:
        """Return whether a named capability is declared as provided."""
        if capability not in self.__dataclass_fields__:
            msg = f"unknown capability {capability!r}"
            raise ValueError(msg)
        return bool(getattr(self, capability))


@dataclass(frozen=True)
class RequiredCapabilities:
    """What the loaded policy demands of the adapter.

    `block_before_tool` defaults to True because a policy provider that cannot deny
    is not a policy provider.
    """

    block_before_tool: bool = True
    human_approval: bool = False
    modify_tool_arguments: bool = False

    def required_names(self) -> tuple[str, ...]:
        """Return the capability names this declaration requires."""
        return tuple(name for name in self.__dataclass_fields__ if getattr(self, name))

    def requires_for(self, verdict: Verdict) -> str | None:
        """Return the capability name a given verdict needs, if any."""
        if verdict is Verdict.DENY:
            return "block_before_tool"
        if verdict is Verdict.REVIEW:
            return "human_approval"
        return None


@runtime_checkable
class InlineControl(Protocol):
    """Runs in the hot path. Must be fast (target: <50ms) or it blocks execution."""

    name: str

    async def collect(self, event: ActionIntent) -> Evidence:
        """Produce one piece of evidence about an intent."""
        ...


@runtime_checkable
class AsyncAnalyzer(Protocol):
    """Runs after the span is exported. Never blocks the live tool call."""

    async def analyze(self, trajectory_ref: str) -> None:
        """Analyze an exported trajectory out of band."""
        ...


@runtime_checkable
class PolicyProvider(Protocol):
    """The single authority on whether an action is permitted."""

    name: str

    async def authorize(
        self, event: ActionIntent, evidence: Sequence[Evidence]
    ) -> ControlResult:
        """Authorize an intent. Must not raise: failures become results."""
        ...

    async def aclose(self) -> None:
        """Release any transport resources."""
        ...


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Binds AgentControl to one agent framework."""

    name: str
    capabilities: CapabilityManifest

    def wrap(self, agent: Any) -> Any:
        """Return the governed form of an agent, validating it in the process."""
        ...
