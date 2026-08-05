#!/usr/bin/env python
"""Runnable driver for the quickstart validation scenarios.

See specs/001-agentcontrol-runtime-governance/quickstart.md. Requires OPA reachable at
--opa-url (default http://localhost:8181; `docker compose up -d` starts one with
decision logging enabled) with the bundle in policies/ loaded.

Usage:
    python examples/governed_agent.py --mode passthrough
    python examples/governed_agent.py --mode deny
    python examples/governed_agent.py --mode deny --export otlp
    python examples/governed_agent.py --mode review
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from langchain.agents import create_agent
from langchain_core.tools import tool

from agentcontrol import ContextResolvers, ControlPlane, ControlPlaneConfig, PolicyConfig
from agentcontrol.adapters.langgraph import LangGraphAdapter
from agentcontrol.providers.policy.opa import OPAPolicyProvider
from tests.support.fake_model import ScriptedToolCallingModel, tool_call


@tool
def search(q: str) -> str:
    """Search for something benign."""
    return f"result for {q}"


@tool
def delete_repository(repo: str) -> str:
    """Delete a repository. Destructive; the policy blocks this unconditionally."""
    return f"deleted {repo}"  # pragma: no cover - only reachable if governance failed


@tool
def write_report(resource: str) -> str:
    """Write to a resource. Reviewed when the resource is company/production."""
    return f"wrote to {resource}"


def _resolvers_for(mode: str) -> ContextResolvers:
    """Build resolvers matching the scenario, so the Rego rules in policies/ fire."""
    if mode == "deny":
        return ContextResolvers(context_trust=lambda _ctx: "untrusted")
    if mode == "review":
        return ContextResolvers(resource=lambda _ctx: "company/production")
    return ContextResolvers()


def _script_for(mode: str) -> list[list[dict]]:
    if mode == "deny":
        return [[tool_call("delete_repository", {"repo": "company/production"}, "c1")]]
    if mode == "review":
        return [[tool_call("write_report", {"resource": "company/production"}, "c1")]]
    return [[tool_call("search", {"q": "hello"}, "c1")]]


async def _run(mode: str, opa_url: str, *, export: str) -> int:
    if mode == "passthrough":
        control = ControlPlane()
    else:
        config = ControlPlaneConfig(
            policy=PolicyConfig(url=opa_url), resolvers=_resolvers_for(mode)
        )
        control = ControlPlane(
            config=config,
            policy=OPAPolicyProvider(config.policy),
            adapter=LangGraphAdapter(),
            own_tracer_provider=(export == "otlp"),
        )

    model = ScriptedToolCallingModel(script=_script_for(mode))
    agent = create_agent(
        model=model,
        tools=[search, delete_repository, write_report],
        middleware=[control.middleware],
        checkpointer=_checkpointer() if mode == "review" else None,
    )
    agent = control.attach(agent)

    thread_config = {"configurable": {"thread_id": "example-thread"}}
    result = agent.invoke({"messages": [{"role": "user", "content": "go"}]}, thread_config)

    for message in result.get("messages", result.get("__interrupt__", [])):
        name = type(message).__name__
        content = getattr(message, "content", message)
        print(f"{name}: {content}")

    await control.aclose()
    return 0


def _checkpointer():  # noqa: ANN202
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def main() -> int:
    """Parse args and run the requested quickstart scenario."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["passthrough", "deny", "review"], default="passthrough"
    )
    parser.add_argument("--opa-url", default="http://localhost:8181")
    parser.add_argument("--export", choices=["none", "otlp"], default="none")
    args = parser.parse_args()
    return asyncio.run(_run(args.mode, args.opa_url, export=args.export))


if __name__ == "__main__":
    sys.exit(main())
