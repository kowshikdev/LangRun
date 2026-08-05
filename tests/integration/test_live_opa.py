"""Task added post-implementation: `OPAPolicyProvider` against a **real** `opa run
--server` instance, not a `respx` mock.

This is the test that should have existed from the start. Every other integration
test mocks the transport with `respx`, which validates that the client parses the
response shape it *expects* — it cannot prove the client asks the right question of
the server. That gap is exactly what let a real bug ship silently: the default
`policy.path` pointed at the Rego *package* instead of the `result` *rule*, so every
real OPA response was nested one level deeper than the client read, and the client
would fail-closed on every single request while its mocked tests stayed green
throughout (research.md R10).

Opt-in via the `live_opa` marker — requires `opa run --server` reachable at
`AGENTCONTROL_TEST_OPA_URL` (default `http://127.0.0.1:8181`) serving the bundle in
`policies/`. Skipped automatically when that server isn't reachable, so it never
blocks a normal `pytest` run or CI without a live OPA step.
"""

from __future__ import annotations

import os

import httpx
import pytest

from agentcontrol.core.config import PolicyConfig
from agentcontrol.core.types import ActionIntent, ContextTrust, Verdict
from agentcontrol.providers.policy.opa import OPAPolicyProvider

_URL = os.environ.get("AGENTCONTROL_TEST_OPA_URL", "http://127.0.0.1:8181")


def _opa_reachable() -> bool:
    try:
        return httpx.get(f"{_URL}/health", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.live_opa,
    pytest.mark.skipif(
        not _opa_reachable(),
        reason=(
            f"no live OPA reachable at {_URL} — run "
            f"`opa run --server --set decision_logs.console=true policies/` first"
        ),
    ),
]


def _intent(tool: str, resource: str | None, trust: ContextTrust) -> ActionIntent:
    return ActionIntent(
        agent_id="live-test-agent",
        tool=tool,
        arguments={},
        trace_id="a" * 32,
        span_id="b" * 16,
        resource=resource,
        context_trust=trust,
    )


class TestRealServerContract:
    """Proves the client reads the response shape a real OPA server actually sends —
    not the shape a mock was told to send.
    """

    async def test_default_allow_against_the_real_server(self) -> None:
        provider = OPAPolicyProvider(PolicyConfig(url=_URL))
        try:
            result = await provider.authorize(
                _intent("harmless_tool", "public/docs", ContextTrust.TRUSTED)
            )
        finally:
            await provider.aclose()
        assert result.verdict is Verdict.ALLOW
        assert result.policy_id == "agentcontrol.authz.default_allow"
        assert not result.unavailable

    async def test_destructive_tool_denied_against_the_real_server(self) -> None:
        provider = OPAPolicyProvider(PolicyConfig(url=_URL))
        try:
            result = await provider.authorize(
                _intent("github.delete_repository", "x/y", ContextTrust.TRUSTED)
            )
        finally:
            await provider.aclose()
        assert result.verdict is Verdict.DENY
        assert result.policy_id == "agentcontrol.authz.deny_destructive_tool"

    async def test_production_resource_review_against_the_real_server(self) -> None:
        provider = OPAPolicyProvider(PolicyConfig(url=_URL))
        try:
            result = await provider.authorize(
                _intent("github.create_issue", "company/production", ContextTrust.TRUSTED)
            )
        finally:
            await provider.aclose()
        assert result.verdict is Verdict.REVIEW
        assert result.review_timeout_seconds == 900

    async def test_decision_id_is_present_when_decision_logging_is_configured(self) -> None:
        """Requires the server started with --set decision_logs.console=true, per
        the docker-compose.yml and quickstart.md convention.
        """
        provider = OPAPolicyProvider(PolicyConfig(url=_URL))
        try:
            result = await provider.authorize(
                _intent("harmless_tool", "public/docs", ContextTrust.TRUSTED)
            )
        finally:
            await provider.aclose()
        assert result.decision_id, (
            "decision_id was empty — is the server running with "
            "--set decision_logs.console=true?"
        )

    async def test_the_bare_package_path_would_have_failed_this_way(self) -> None:
        """Regression guard for research R10: querying the package instead of the
        rule must fail closed with a decision-shape error, not silently succeed.
        Directly demonstrates the bug this file exists to prevent recurring.
        """
        broken = OPAPolicyProvider(PolicyConfig(url=_URL, path="agentcontrol/authz"))
        try:
            result = await broken.authorize(
                _intent("harmless_tool", "public/docs", ContextTrust.TRUSTED)
            )
        finally:
            await broken.aclose()
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY  # fail-closed, not a crash
        assert "decision" in result.reason.lower()
