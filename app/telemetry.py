"""OpenTelemetry spans (OBS-03, ADR-13): no-op unless OTEL_ENABLED / an exporter is configured."""

from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

_provider: Any = None


def setup_tracing(enabled: bool, endpoint: str = "", exporter: Any = None) -> None:
    """Install an SDK provider when tracing is enabled (OTLP/HTTP when an endpoint is given) or a test exporter."""
    global _provider
    if not (enabled or exporter):
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "getnet-multi-agent-support"}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    _provider = provider


@contextmanager
def span(name: str, **attributes: Any):
    tracer = trace.get_tracer("getnet-support", tracer_provider=_provider)
    with tracer.start_as_current_span(name) as s:
        for key, value in attributes.items():
            if value is not None:
                s.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))
        yield s
