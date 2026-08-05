"""Shared fixtures for the conformance suite.

Every test here proves a `CapabilityManifest` field against the real LangGraph
runtime by executing it, per FR-030 / SC-008 — never by asserting the declared
constant against itself.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.integration.conftest import TracedAgent


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
