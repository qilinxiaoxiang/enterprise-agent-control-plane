from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

from control_plane.config import Settings

tracer = trace.get_tracer("enterprise-agent-control-plane")
_configured = False
_provider: TracerProvider | None = None


def configure_telemetry(settings: Settings) -> None:
    global _configured, _provider
    if _configured or settings.app_env == "test":
        return
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "agent-control-api", "deployment.environment": settings.app_env}
        )
    )
    if settings.app_env == "production":
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        exporter: SpanExporter = CloudTraceSpanExporter(  # type: ignore[no-untyped-call]
            project_id=settings.google_cloud_project
        )
    elif settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        )
    else:
        return
    if settings.app_env == "production":
        # Cloud Run uses request-based CPU. Export completed spans while the request
        # still owns CPU instead of relying on a background batch timer.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider
    _configured = True


def flush_telemetry(timeout_millis: int = 10_000) -> bool:
    """Flush completed spans before request-based Cloud Run CPU is throttled."""
    if _provider is None:
        return True
    return _provider.force_flush(timeout_millis=timeout_millis)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    clean = {key: value for key, value in attributes.items() if value is not None}
    with tracer.start_as_current_span(name, attributes=clean) as current:
        yield current
