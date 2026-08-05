"""Shared policy-provider behavior: fail-mode resolution.

`authorize` must never raise. A provider failure that escapes into the middleware can
be caught by LangGraph's `ToolNode` and converted into an ordinary tool error, turning
a governance failure into a retryable one. Failures become results here instead.
"""

from __future__ import annotations

import logging

from agentcontrol.core.types import ControlResult, Verdict

__all__ = ["FAIL_OPEN_WARNING", "unavailable_result"]

_LOG = logging.getLogger(__name__)

FAIL_OPEN_WARNING = (
    "AgentControl FAIL-OPEN: policy provider %r was unavailable (%s) and "
    "policy.fail_mode='open' is set for this deployment, so the action was ALLOWED "
    "without authorization. This is logged on every use by design."
)


def unavailable_result(
    provider: str,
    reason: str,
    *,
    fail_mode: str,
) -> ControlResult:
    """Build the result for a provider that could not produce a decision.

    Fail-closed is the default: when the authorization path cannot produce a
    trustworthy answer, the answer is deny. The result is marked `unavailable` so audit
    can never confuse an outage with a rule.
    """
    if fail_mode == "open":
        _LOG.warning(FAIL_OPEN_WARNING, provider, reason)
        verdict = Verdict.ALLOW
    else:
        verdict = Verdict.DENY

    return ControlResult(
        verdict=verdict,
        provider=provider,
        reason=f"policy provider unavailable: {reason}",
        unavailable=True,
        fail_mode_applied=fail_mode,
    )
