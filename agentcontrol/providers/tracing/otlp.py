"""Tracer acquisition and optional OTLP export wiring.

A library that hijacks the host application's `TracerProvider` is a library nobody
adopts twice. By default AgentControl acquires a tracer from whatever provider is
already installed and touches nothing else. A dedicated provider is created only when
explicitly asked for.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agentcontrol.core.config import TelemetryConfig

__all__ = ["TRACER_NAME", "build_tracer"]

_LOG = logging.getLogger(__name__)

TRACER_NAME = "agentcontrol"


def build_tracer(
    telemetry: TelemetryConfig, *, own_provider: bool = False
) -> tuple[trace.Tracer, TracerProvider | None]:
    """Return a tracer for governance records, and the provider owning it if any.

    Governance spans use an `ALWAYS_ON` sampler when AgentControl owns the provider.
    When riding the host's provider, its sampler applies — hosts that sample should
    either exempt this instrumentation scope or let AgentControl own a provider, since
    a sampled-away denial cannot be audited.

    Args:
        telemetry: Telemetry configuration.
        own_provider: Create and return a dedicated provider instead of using the
            ambient one. Never installs itself globally.

    Returns:
        A `(tracer, provider)` pair. `provider` is `None` when the ambient provider is
        being used, and is the caller's to shut down otherwise.
    """
    if not own_provider:
        return trace.get_tracer(TRACER_NAME), None

    resource = Resource.create({"service.name": telemetry.service_name})
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)

    endpoint = telemetry.resolved_endpoint()
    if endpoint:
        exporter = _build_exporter(endpoint)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        _LOG.warning(
            "AgentControl owns a TracerProvider but no OTLP endpoint is configured; "
            "governance records will not leave the process. Set telemetry.endpoint or "
            "OTEL_EXPORTER_OTLP_ENDPOINT."
        )

    return provider.get_tracer(TRACER_NAME), provider


def _build_exporter(endpoint: str) -> SpanExporter | None:
    """Build an OTLP/HTTP span exporter, or None when the exporter is unavailable."""
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:  # pragma: no cover - optional dependency path
        _LOG.warning(
            "opentelemetry-exporter-otlp-proto-http is not installed; "
            "governance records will not be exported."
        )
        return None
    return OTLPSpanExporter(endpoint=_traces_endpoint(endpoint))


def _traces_endpoint(endpoint: str) -> str:
    """Append the standard traces path when a bare collector root is configured."""
    trimmed = endpoint.rstrip("/")
    if trimmed.endswith("/v1/traces"):
        return trimmed
    return f"{trimmed}/v1/traces"
