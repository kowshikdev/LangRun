"""Errors raised by AgentControl.

None of these are raised from the hot path. A provider failure becomes a
`ControlResult`, never an exception — an exception escaping the interception layer can
be reinterpreted by the host framework as an ordinary tool error, silently converting a
governance failure into a retryable one.
"""

from __future__ import annotations

__all__ = [
    "AgentControlError",
    "CapabilityMismatchError",
    "ConfigurationError",
    "PolicyUnavailableError",
]


class AgentControlError(Exception):
    """Base class for every AgentControl error."""


class ConfigurationError(AgentControlError):
    """Configuration is invalid and cannot be repaired at runtime."""


class PolicyUnavailableError(AgentControlError):
    """The policy provider could not be reached.

    Raised only from an explicit health probe. During authorization, provider failure
    is expressed as a `ControlResult` with `unavailable=True`.
    """


class CapabilityMismatchError(AgentControlError):
    """The policy requires enforcement the active adapter cannot provide.

    There is deliberately no downgrade path. The message names the specific gap
    because an operator who cannot see which capability is missing cannot fix it, and
    an unfixable warning becomes a warning everyone ignores.
    """

    def __init__(
        self,
        capability: str,
        adapter: str,
        *,
        required_by: str,
        provided: bool,
        reason: str,
        fix: str,
    ) -> None:
        """Build a mismatch error naming the gap and the fix."""
        self.capability = capability
        self.adapter = adapter
        self.required_by = required_by
        self.provided = provided
        self.reason = reason
        self.fix = fix
        message = (
            f"policy requires {capability!r} but adapter {adapter!r} does not provide it.\n"
            f"  required by: {required_by}\n"
            f"  adapter manifest: {capability}={provided}\n"
            f"  reason: {reason}\n"
            f"  fix: {fix}"
        )
        super().__init__(message)
