"""Task T070: a capability mismatch fails startup loudly, naming the exact gap.

This is the test that matters most in the whole suite. Passing quietly here would be
the worst possible outcome for this project — it is the false-assurance failure the
product exists to prevent (Constitution Principle II).
"""

from __future__ import annotations

import re

import pytest
from langchain.agents import create_agent
from langchain_core.tools import tool

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.adapters.langgraph import LangGraphAdapter
from agentcontrol.core.errors import CapabilityMismatchError
from agentcontrol.core.types import RequiredCapabilities
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.support.fake_model import ScriptedToolCallingModel


@tool
def write_report(resource: str) -> str:
    """Write to a resource."""
    return f"wrote to {resource}"


def _control(required: RequiredCapabilities) -> ControlPlane:
    config = ControlPlaneConfig(
        policy=PolicyConfig(url="http://opa.test:8181"), required_capabilities=required
    )
    return ControlPlane(
        config=config, policy=OPAPolicyProvider(config.policy), adapter=LangGraphAdapter()
    )


class TestMissingCheckpointer:
    def test_human_approval_required_without_checkpointer_fails_startup(self) -> None:
        control = _control(RequiredCapabilities(human_approval=True))
        model = ScriptedToolCallingModel(script=[[]])
        # No checkpointer configured: interrupt() cannot work without one.
        agent = create_agent(model=model, tools=[write_report], middleware=[control.middleware])

        with pytest.raises(CapabilityMismatchError) as exc_info:
            control.attach(agent)

        message = str(exc_info.value)
        assert "human_approval" in message
        assert "checkpointer" in message.lower()
        assert "fix:" in message.lower()

    def test_error_names_the_adapter_and_the_requirement_source(self) -> None:
        control = _control(RequiredCapabilities(human_approval=True))
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[write_report], middleware=[control.middleware])

        with pytest.raises(CapabilityMismatchError) as exc_info:
            control.attach(agent)

        error = exc_info.value
        assert error.capability == "human_approval"
        assert error.adapter == "langgraph"
        assert "required_capabilities.human_approval" in error.required_by


class TestSucceedsWithCheckpointer:
    def test_same_requirement_with_a_checkpointer_starts_cleanly(self) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        control = _control(RequiredCapabilities(human_approval=True))
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(
            model=model,
            tools=[write_report],
            middleware=[control.middleware],
            checkpointer=InMemorySaver(),
        )
        # Must not raise.
        control.attach(agent)


class TestNoAdapterMeansNoValidation:
    def test_without_an_adapter_startup_does_not_validate_capabilities(self) -> None:
        """Documents the actual boundary: capability validation is the adapter's job.
        A ControlPlane with no adapter configured has nothing to validate against and
        must not silently claim a mismatch was checked when it wasn't.
        """
        config = ControlPlaneConfig(
            policy=PolicyConfig(url="http://opa.test:8181"),
            required_capabilities=RequiredCapabilities(human_approval=True),
        )
        control = ControlPlane(config=config, policy=OPAPolicyProvider(config.policy))
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[write_report], middleware=[control.middleware])
        control.attach(agent)  # does not raise — no adapter means no static check


class TestErrorMessageShape:
    def test_matches_the_documented_shape(self) -> None:
        """Loose structural check against the shape in
        contracts/capability-manifest.md's Startup validation section.
        """
        control = _control(RequiredCapabilities(human_approval=True))
        model = ScriptedToolCallingModel(script=[[]])
        agent = create_agent(model=model, tools=[write_report], middleware=[control.middleware])

        with pytest.raises(CapabilityMismatchError) as exc_info:
            control.attach(agent)

        message = str(exc_info.value)
        assert re.search(r"required by:", message)
        assert re.search(r"adapter manifest:", message)
        assert re.search(r"reason:", message)
        assert re.search(r"fix:", message)
