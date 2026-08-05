"""Task T027: a destructive tool call from untrusted context is denied end-to-end.

Drives a real `create_agent` graph through `AgentControlMiddleware` and
`OPAPolicyProvider`, with the OPA transport mocked via respx so the test exercises the
full interception path without a live server. Live-server behavior is covered by
`live_opa`-marked tests.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import BaseTool, tool

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.integration.conftest import TracedAgent
from tests.support.fake_model import ScriptedToolCallingModel, tool_call

_URL = "http://opa.test:8181"


@tool
def delete_repository(repo: str) -> str:
    """Delete a repository."""
    return f"deleted {repo}"  # pragma: no cover - must never actually run


def _deny_response(policy_id: str = "agentcontrol.authz.deny_destructive_tool") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "decision_id": "d-deny",
            "result": {
                "decision": "deny",
                "reason": "destructive tool is blocked unconditionally",
                "policy_id": policy_id,
            },
        },
    )


def _allow_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "decision_id": "d-allow",
            "result": {"decision": "allow", "reason": "fine", "policy_id": "p.allow"},
        },
    )


def _build(
    *, respx_mock: respx.MockRouter, traced: TracedAgent, tools: list[BaseTool]
) -> ControlPlane:
    config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
    control = ControlPlane(
        config=config,
        policy=OPAPolicyProvider(config.policy),
        tracer_provider=traced.provider,
    )
    del respx_mock  # mocked at call site via the respx_mock fixture's router
    del tools
    return control


@pytest.mark.respx(base_url=_URL)
class TestDeny:
    async def test_destructive_tool_never_executes(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_deny_response())
        control = _build(respx_mock=respx_mock, traced=traced, tools=[delete_repository])
        model = ScriptedToolCallingModel(
            script=[[tool_call("delete_repository", {"repo": "company/production"}, "c1")]]
        )
        agent = create_agent(
            model=model, tools=[delete_repository], middleware=[control.middleware]
        )
        agent = control.attach(agent)

        result = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        assert len(tool_messages) == 1
        assert tool_messages[0].status == "error"
        assert "denied" in tool_messages[0].content.lower()
        assert "deny_destructive_tool" in tool_messages[0].content

    async def test_agent_keeps_reasoning_after_denial(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_deny_response())
        control = _build(respx_mock=respx_mock, traced=traced, tools=[delete_repository])
        model = ScriptedToolCallingModel(
            script=[[tool_call("delete_repository", {"repo": "x"}, "c1")]]
        )
        agent = create_agent(
            model=model, tools=[delete_repository], middleware=[control.middleware]
        )
        agent = control.attach(agent)

        result = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

        # The run did not crash: it continued to a final AIMessage after the denial.
        assert type(result["messages"][-1]).__name__ == "AIMessage"

    async def test_denial_span_carries_reason_and_rule(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        from agentcontrol.core import semconv

        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(return_value=_deny_response())
        control = _build(respx_mock=respx_mock, traced=traced, tools=[delete_repository])
        model = ScriptedToolCallingModel(
            script=[[tool_call("delete_repository", {"repo": "x"}, "c1")]]
        )
        agent = create_agent(
            model=model, tools=[delete_repository], middleware=[control.middleware]
        )
        agent = control.attach(agent)
        await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]})

        spans = [
            s
            for s in traced.exporter.get_finished_spans()
            if s.name.startswith("execute_tool ")
        ]
        assert len(spans) == 1
        span = spans[0]
        assert span.attributes[semconv.AC_POLICY_DECISION] == "deny"
        assert span.attributes[semconv.AC_POLICY_ID] == "agentcontrol.authz.deny_destructive_tool"
        assert span.attributes[semconv.AC_POLICY_DECISION_ID] == "d-deny"


def _trust_dependent_response(request: httpx.Request) -> httpx.Response:
    """Stand in for a Rego rule keyed on context.trust: deny only when untrusted.

    Reading the trust value back out of the request body (rather than replaying
    responses in call order) is what actually proves the resolved
    `ActionIntent.context_trust` reached the policy input, not just that two calls
    happened to get two different mocked answers.
    """
    import json as _json

    body = _json.loads(request.content)
    trust = body["input"]["context"]["trust"]
    if trust == "untrusted":
        return _deny_response("agentcontrol.authz.deny_injection_untrusted")
    return _allow_response()


@pytest.mark.respx(base_url=_URL)
class TestTrustDimension:
    """Task T028: the same tool, allowed trusted / denied untrusted."""

    async def test_same_tool_trusted_allowed_untrusted_denied(
        self, respx_mock: respx.MockRouter, traced: TracedAgent
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(
            side_effect=_trust_dependent_response
        )
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))

        trusted_control = ControlPlane(
            config=ControlPlaneConfig(
                policy=config.policy,
                resolvers=type(config.resolvers)(context_trust=lambda _ctx: "trusted"),
            ),
            policy=OPAPolicyProvider(config.policy),
            tracer_provider=traced.provider,
        )
        trusted_result = await control_agent(trusted_control, "trusted-call").ainvoke(
            {"messages": [{"role": "user", "content": "go"}]}
        )
        assert _only_tool_message(trusted_result).status != "error"

        untrusted_control = ControlPlane(
            config=ControlPlaneConfig(
                policy=config.policy,
                resolvers=type(config.resolvers)(context_trust=lambda _ctx: "untrusted"),
            ),
            policy=OPAPolicyProvider(config.policy),
            tracer_provider=traced.provider,
        )
        untrusted_result = await control_agent(untrusted_control, "untrusted-call").ainvoke(
            {"messages": [{"role": "user", "content": "go"}]}
        )
        assert _only_tool_message(untrusted_result).status == "error"


def control_agent(control: ControlPlane, call_id: str):
    model = ScriptedToolCallingModel(
        script=[[tool_call("delete_repository", {"repo": "x"}, call_id)]]
    )
    agent = create_agent(model=model, tools=[delete_repository], middleware=[control.middleware])
    return control.attach(agent)


def _only_tool_message(result: dict):
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    return tool_messages[0]
