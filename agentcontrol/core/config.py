"""Configuration for AgentControl.

No value in this module may change an authorization outcome. Configuration controls how
a signal is produced; the versioned policy bundle controls the consequence of that
signal. `ReviewConfig.default_timeout_seconds` is the boundary case and is deliberately
a fallback for a policy that is *silent*, never an override of one that is not.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentcontrol.core.errors import ConfigurationError
from agentcontrol.core.types import ContextTrust, RequiredCapabilities

__all__ = [
    "ContextResolvers",
    "ControlPlaneConfig",
    "PolicyConfig",
    "ReviewConfig",
    "TelemetryConfig",
]

FailMode = str  # "closed" | "open"

_VALID_FAIL_MODES = frozenset({"closed", "open"})

#: Resolvers receive the framework-agnostic resolution context assembled by the adapter:
#: ``{"tool_call": ..., "state": ..., "runtime": ..., "config": ...}``.
Resolver = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class PolicyConfig:
    """How to reach the policy provider, and what to do when it cannot be reached."""

    url: str = ""
    #: Path under `/v1/data/` to the specific *rule*, not the package. Querying the
    #: bare package path (`agentcontrol/authz`) returns every public rule and var in
    #: the package as siblings — `result`, `injection_block_threshold`,
    #: `review_window_seconds` — not the `result` rule's value directly. Verified
    #: against a real `opa run --server` instance, not just the mocked test transport;
    #: `/v1/data/agentcontrol/authz` returned `{"result": {"result": {...}, ...}}` in
    #: practice, one level deeper than `OPAPolicyProvider` expects.
    path: str = "agentcontrol/authz/result"
    timeout_ms: int = 300
    fail_mode: FailMode = "closed"

    def __post_init__(self) -> None:
        """Validate the timeout and the fail mode."""
        if self.timeout_ms <= 0:
            msg = f"policy.timeout_ms must be positive, got {self.timeout_ms}"
            raise ConfigurationError(msg)
        if self.fail_mode not in _VALID_FAIL_MODES:
            msg = (
                f"policy.fail_mode must be one of {sorted(_VALID_FAIL_MODES)}, "
                f"got {self.fail_mode!r}"
            )
            raise ConfigurationError(msg)

    @property
    def timeout_seconds(self) -> float:
        """Return the request deadline in seconds."""
        return self.timeout_ms / 1000.0

    @property
    def decision_url(self) -> str:
        """Return the full data-API URL for the configured policy path."""
        if not self.url:
            msg = "policy.url is required when a policy provider is configured"
            raise ConfigurationError(msg)
        return f"{self.url.rstrip('/')}/v1/data/{self.path.strip('/')}"


@dataclass(frozen=True)
class ReviewConfig:
    """Human-review behavior.

    `default_timeout_seconds` applies only when the policy returns no window of its own.
    """

    default_timeout_seconds: int = 900
    watchdog_enabled: bool = True
    watchdog_poll_seconds: float = 5.0

    def __post_init__(self) -> None:
        """Validate the fallback window."""
        if self.default_timeout_seconds <= 0:
            msg = (
                "review.default_timeout_seconds must be positive, "
                f"got {self.default_timeout_seconds}"
            )
            raise ConfigurationError(msg)


@dataclass(frozen=True)
class TelemetryConfig:
    """Where governance records go, and how much of the payload they carry."""

    endpoint: str | None = None
    service_name: str = "agentcontrol"
    #: Upstream marks tool arguments and results Opt-In and flags them as possibly
    #: sensitive. A decision stays fully explainable without them, so they default off.
    record_tool_arguments: bool = False
    record_tool_results: bool = False
    enabled: bool = True

    def resolved_endpoint(self) -> str | None:
        """Return the configured endpoint, falling back to the standard OTLP env vars."""
        return (
            self.endpoint
            or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        )


def _default_trust(_: Mapping[str, Any]) -> ContextTrust:
    return ContextTrust.UNKNOWN


@dataclass(frozen=True)
class ContextResolvers:
    """How the integrating application supplies what the policy authorizes against.

    Satisfies FR-032. Every field the policy sees has an explicit producer and an
    explicit fallback, and no fallback is more permissive than "unknown". Without this,
    `resource`-keyed and trust-keyed rules silently never fire — a policy that cannot
    match is a policy that does not enforce.

    Each resolver receives the adapter's resolution context and returns a value or
    `None`. A resolver that raises is treated as returning `None`: a broken resolver
    must not be able to fabricate an authorization-relevant value.
    """

    agent_id: Resolver | None = None
    user_id: Resolver | None = None
    task: Resolver | None = None
    resource: Resolver | None = None
    context_trust: Resolver = field(default=_default_trust)
    context_source: Resolver | None = None

    #: Used when `agent_id` resolves to nothing. Unlike the other fields, an intent
    #: with no agent is not authorizable at all, so this has a concrete default.
    default_agent_id: str = "unknown-agent"

    #: State keys consulted before the resolvers, so a host can simply put these in
    #: graph state instead of writing callables.
    state_keys: Mapping[str, str] = field(
        default_factory=lambda: {
            "agent_id": "agentcontrol_agent_id",
            "user_id": "agentcontrol_user_id",
            "task": "agentcontrol_task",
            "resource": "agentcontrol_resource",
            "context_trust": "agentcontrol_context_trust",
            "context_source": "agentcontrol_context_source",
        }
    )


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Top-level configuration."""

    policy: PolicyConfig = field(default_factory=PolicyConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    resolvers: ContextResolvers = field(default_factory=ContextResolvers)
    required_capabilities: RequiredCapabilities = field(default_factory=RequiredCapabilities)
    #: Budget for the concurrent inline-evidence sweep. A collector that exceeds it
    #: drops its signal; the signal is never defaulted to a passing value.
    inline_control_budget_ms: int = 50
    #: Optional: enumerate reachable policy decisions with `opa eval` and cross-check
    #: `required_capabilities`. Off by default because it needs the `opa` binary.
    strict_policy_scan: bool = False

    def __post_init__(self) -> None:
        """Validate cross-cutting settings."""
        if self.inline_control_budget_ms <= 0:
            msg = (
                "inline_control_budget_ms must be positive, "
                f"got {self.inline_control_budget_ms}"
            )
            raise ConfigurationError(msg)
