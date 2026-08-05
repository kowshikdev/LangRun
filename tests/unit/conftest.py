"""Shared fixtures for unit tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentcontrol.core.config import TelemetryConfig
from agentcontrol.core.otel import GovernanceRecorder


@dataclass
class RecordedSpans:
    """A GovernanceRecorder paired with the in-memory exporter it writes to."""

    recorder: GovernanceRecorder
    exporter: InMemorySpanExporter
    provider: TracerProvider


def _build(telemetry: TelemetryConfig) -> RecordedSpans:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agentcontrol-tests")
    return RecordedSpans(
        recorder=GovernanceRecorder(tracer, telemetry), exporter=exporter, provider=provider
    )


@pytest.fixture
def recorded_factory() -> Iterator[Callable[[TelemetryConfig], RecordedSpans]]:
    """Build a `RecordedSpans` for a given telemetry config; shuts down after the test."""
    built: list[RecordedSpans] = []

    def factory(telemetry: TelemetryConfig | None = None) -> RecordedSpans:
        recorded = _build(telemetry or TelemetryConfig())
        built.append(recorded)
        return recorded

    yield factory
    for recorded in built:
        recorded.provider.shutdown()


@pytest.fixture
def recorded(
    recorded_factory: Callable[[TelemetryConfig], RecordedSpans],
) -> RecordedSpans:
    """A `RecordedSpans` built with default telemetry config."""
    return recorded_factory(TelemetryConfig())
