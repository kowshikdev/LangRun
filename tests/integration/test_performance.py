"""Task T083: governance overhead budgets.

SC-007 (≤5ms with no providers configured) is directly measurable here. SC-002 (≤400ms
p95 / ≤500ms p99 with a *reachable* OPA) depends on real network RTT to a real OPA
instance, which this environment cannot provide (no Docker, no `opa` binary — see
README's Known open questions). What *is* measurable and meaningful without a network
is AgentControl's own added overhead on top of the policy call — evidence collection,
intent construction, span building — which is what actually has to fit inside the
400ms/500ms budget alongside the 300ms default OPA timeout. That is what
`TestMiddlewareOverhead` measures, with the OPA call itself mocked to return near-
instantly via respx so the number reflects AgentControl's code, not network jitter.
"""

from __future__ import annotations

import statistics
import time

import httpx
import pytest
import respx
from langchain.agents import create_agent
from langchain_core.tools import tool

from agentcontrol import ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.support.fake_model import ScriptedToolCallingModel, tool_call

_URL = "http://opa.test:8181"
_ITERATIONS = 50


@tool
def search(q: str) -> str:
    """Search for something."""
    return f"result for {q}"


def _script() -> list[list[dict]]:
    return [[tool_call("search", {"q": "x"}, "c1")]]


def _p95(samples_ms: list[float]) -> float:
    return statistics.quantiles(samples_ms, n=100)[94]


class TestPassthroughOverhead:
    """SC-007: with no providers configured, added latency is under 5ms per call."""

    def test_passthrough_adds_under_5ms_per_tool_call(self) -> None:
        control = ControlPlane()
        samples: list[float] = []
        for _ in range(_ITERATIONS):
            model = ScriptedToolCallingModel(script=_script())
            agent = control.attach(
                create_agent(model=model, tools=[search], middleware=[control.middleware])
            )
            started = time.perf_counter()
            agent.invoke({"messages": [{"role": "user", "content": "go"}]})
            samples.append((time.perf_counter() - started) * 1000)

        baseline_samples: list[float] = []
        for _ in range(_ITERATIONS):
            model = ScriptedToolCallingModel(script=_script())
            agent = create_agent(model=model, tools=[search])
            started = time.perf_counter()
            agent.invoke({"messages": [{"role": "user", "content": "go"}]})
            baseline_samples.append((time.perf_counter() - started) * 1000)

        added = statistics.median(samples) - statistics.median(baseline_samples)
        assert added < 5.0, (
            f"passthrough median overhead {added:.3f}ms exceeds the 5ms SC-007 budget "
            f"(governed median {statistics.median(samples):.3f}ms, "
            f"ungoverned median {statistics.median(baseline_samples):.3f}ms)"
        )


@pytest.mark.respx(base_url=_URL)
class TestMiddlewareOverhead:
    """AgentControl's own added latency with a policy configured, network excluded.

    Not a substitute for measuring SC-002 against a real OPA instance over a real
    network — that verification still needs to happen (see README). This measures
    the budget AgentControl's own code consumes, which has to leave headroom under
    the 400ms/500ms SC-002 ceiling alongside real network RTT and the 300ms OPA
    timeout.
    """

    def test_middleware_overhead_leaves_headroom_under_the_sc002_budget(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.post("/v1/data/agentcontrol/authz/result").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"decision": "allow", "reason": "fine", "policy_id": "p.allow"}},
            )
        )
        config = ControlPlaneConfig(policy=PolicyConfig(url=_URL))
        samples: list[float] = []
        for _ in range(_ITERATIONS):
            control = ControlPlane(config=config, policy=OPAPolicyProvider(config.policy))
            model = ScriptedToolCallingModel(script=_script())
            agent = control.attach(
                create_agent(model=model, tools=[search], middleware=[control.middleware])
            )
            started = time.perf_counter()
            agent.invoke({"messages": [{"role": "user", "content": "go"}]})
            samples.append((time.perf_counter() - started) * 1000)

        p95 = _p95(samples)
        # Generous local ceiling: real network RTT to OPA is excluded here, so this
        # asserts AgentControl's own machinery doesn't eat the whole SC-002 budget on
        # its own — it must leave room for actual network latency plus the 300ms
        # policy timeout.
        assert p95 < 100.0, (
            f"AgentControl's own overhead (OPA call mocked, no network) has p95="
            f"{p95:.1f}ms, leaving too little of the 400ms SC-002 budget for real "
            f"network RTT and the 300ms policy timeout"
        )
