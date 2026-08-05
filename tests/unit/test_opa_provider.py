"""OPAPolicyProvider unit tests against a mocked transport.

Covers the failure-mapping table in contracts/opa-authz.md (T029) and the fail-open
override (T030). Live-server behavior (decision_id presence, real Rego evaluation) is
covered separately by the `live_opa`-marked integration suite.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from agentcontrol.core.config import PolicyConfig
from agentcontrol.core.types import ActionIntent, Verdict
from agentcontrol.providers.policy.opa import OPAPolicyProvider

_URL = "http://opa.test:8181"


def _intent() -> ActionIntent:
    return ActionIntent(
        agent_id="a",
        tool="github.delete_repository",
        arguments={},
        trace_id="a" * 32,
        span_id="b" * 16,
    )


def _provider(*, fail_mode: str = "closed") -> OPAPolicyProvider:
    return OPAPolicyProvider(PolicyConfig(url=_URL, fail_mode=fail_mode, timeout_ms=100))


@pytest.mark.respx(base_url=_URL)
class TestOPAAuthorize:
    async def test_allow_maps_to_control_result(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            return_value=httpx.Response(
                200,
                json={
                    "decision_id": "d-1",
                    "result": {
                        "decision": "allow",
                        "reason": "fine",
                        "policy_id": "p.allow",
                    },
                },
            )
        )
        provider = _provider()
        result = await provider.authorize(_intent())
        assert result.verdict is Verdict.ALLOW
        assert result.policy_id == "p.allow"
        assert result.decision_id == "d-1"
        assert result.unavailable is False
        await provider.aclose()

    async def test_review_requires_timeout_in_response(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"decision": "review", "reason": "hold", "policy_id": "p.r"}},
            )
        )
        provider = _provider()
        result = await provider.authorize(_intent())
        # Missing review_timeout_seconds is a provider failure, not a valid review.
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY
        await provider.aclose()


@pytest.mark.respx(base_url=_URL)
class TestFailClosedMatrix:
    """Every row is a provider failure: unavailable=True, fail-mode verdict, no policy_id."""

    async def test_connect_error(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = await _provider().authorize(_intent())
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY
        assert result.policy_id is None
        assert result.fail_mode_applied == "closed"

    async def test_timeout(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            side_effect=httpx.TimeoutException("slow")
        )
        result = await _provider().authorize(_intent())
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY

    async def test_non_2xx_status(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            return_value=httpx.Response(500, text="internal error")
        )
        result = await _provider().authorize(_intent())
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY

    async def test_non_json_body(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            return_value=httpx.Response(200, text="not json")
        )
        result = await _provider().authorize(_intent())
        assert result.unavailable is True

    async def test_undefined_rego_document_returns_200_with_no_result(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """The dangerous row: OPA's HTTP status is 200 but the document is undefined."""
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            return_value=httpx.Response(200, json={})
        )
        result = await _provider().authorize(_intent())
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY

    async def test_decision_outside_enforceable_set(self, respx_mock: respx.MockRouter) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"decision": "maybe", "reason": "?", "policy_id": "p"}},
            )
        )
        result = await _provider().authorize(_intent())
        assert result.unavailable is True
        assert result.verdict is Verdict.DENY

    async def test_unavailable_never_carries_a_policy_id(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = await _provider().authorize(_intent())
        assert result.policy_id is None


@pytest.mark.respx(base_url=_URL)
class TestFailOpenOverride:
    async def test_fail_open_allows_and_marks_unavailable(
        self, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            side_effect=httpx.ConnectError("refused")
        )
        provider = _provider(fail_mode="open")
        with caplog.at_level("WARNING"):
            result = await provider.authorize(_intent())
        assert result.verdict is Verdict.ALLOW
        assert result.unavailable is True
        assert result.fail_mode_applied == "open"
        assert any("FAIL-OPEN" in record.message for record in caplog.records)

    async def test_fail_open_warns_on_every_occurrence(
        self, respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz").mock(
            side_effect=httpx.ConnectError("refused")
        )
        provider = _provider(fail_mode="open")
        with caplog.at_level("WARNING"):
            await provider.authorize(_intent())
            await provider.authorize(_intent())
        warnings = [r for r in caplog.records if "FAIL-OPEN" in r.message]
        assert len(warnings) == 2
