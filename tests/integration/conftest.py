"""Fixtures shared across the integration suite."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.support.fake_model import ScriptedToolCallingModel, tool_call

__all__ = ["ScriptedToolCallingModel", "TracedAgent", "tool_call"]


@tool
def search(q: str) -> str:
    """Search for something."""
    return f"result for {q}"


@tool
def delete_repository(repo: str) -> str:
    """Delete a repository. Destructive; only ever called to prove it was blocked."""
    return f"deleted {repo}"


@tool
def write_report(resource: str) -> str:
    """Write to a resource. Used for review-hold scenarios."""
    return f"wrote to {resource}"


DEFAULT_TOOLS: list[BaseTool] = [search, delete_repository, write_report]


@dataclass
class TracedAgent:
    """A span exporter paired with a provider a test can inject into `ControlPlane`.

    OTel's process-global tracer provider can only be set once and refuses later
    overrides, so cross-test isolation cannot go through it. Pass `traced.provider` to
    `ControlPlane(tracer_provider=...)` instead — an explicit injection point added for
    exactly this, distinct from the ambient-global default and from
    `own_tracer_provider`.
    """

    exporter: InMemorySpanExporter
    provider: TracerProvider


@pytest.fixture
def traced() -> Iterator[TracedAgent]:
    """A fresh, isolated in-memory tracer provider for one test."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        yield TracedAgent(exporter=exporter, provider=provider)
    finally:
        provider.shutdown()


@pytest.fixture
def make_scripted_model() -> Callable[..., ScriptedToolCallingModel]:
    """Return a factory for a scripted model, so each test states its own script."""

    def factory(script: list[list[dict[str, Any]]]) -> ScriptedToolCallingModel:
        return ScriptedToolCallingModel(script=script)

    return factory
