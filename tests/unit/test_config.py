"""Configuration validation tests. Task T012."""

from __future__ import annotations

import pytest

from agentcontrol.core.config import ControlPlaneConfig, PolicyConfig, ReviewConfig
from agentcontrol.core.errors import ConfigurationError
from agentcontrol.core.types import ContextTrust


class TestPolicyConfig:
    def test_invalid_fail_mode_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="fail_mode"):
            PolicyConfig(url="http://x", fail_mode="sideways")

    def test_default_fail_mode_is_closed(self) -> None:
        assert PolicyConfig().fail_mode == "closed"

    def test_nonpositive_timeout_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="timeout_ms"):
            PolicyConfig(timeout_ms=0)

    def test_default_timeout_is_300ms(self) -> None:
        assert PolicyConfig().timeout_ms == 300
        assert PolicyConfig().timeout_seconds == pytest.approx(0.3)

    def test_decision_url_requires_url(self) -> None:
        with pytest.raises(ConfigurationError, match="policy\\.url"):
            _ = PolicyConfig().decision_url

    def test_decision_url_joins_path(self) -> None:
        config = PolicyConfig(url="http://localhost:8181/", path="agentcontrol/authz")
        assert config.decision_url == "http://localhost:8181/v1/data/agentcontrol/authz"


class TestReviewConfig:
    def test_default_window_is_900_seconds(self) -> None:
        assert ReviewConfig().default_timeout_seconds == 900

    def test_nonpositive_window_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="default_timeout_seconds"):
            ReviewConfig(default_timeout_seconds=0)


class TestContextTrustDefault:
    def test_default_resolver_returns_unknown_never_trusted(self) -> None:
        resolvers = ControlPlaneConfig().resolvers
        assert resolvers.context_trust({}) is ContextTrust.UNKNOWN

    def test_default_agent_id_is_a_safe_placeholder(self) -> None:
        resolvers = ControlPlaneConfig().resolvers
        assert resolvers.default_agent_id == "unknown-agent"


class TestControlPlaneConfig:
    def test_nonpositive_inline_budget_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="inline_control_budget_ms"):
            ControlPlaneConfig(inline_control_budget_ms=0)

    def test_block_before_tool_required_by_default(self) -> None:
        # A policy provider that cannot deny is not a policy provider.
        assert ControlPlaneConfig().required_capabilities.block_before_tool is True

    def test_strict_policy_scan_off_by_default(self) -> None:
        assert ControlPlaneConfig().strict_policy_scan is False
