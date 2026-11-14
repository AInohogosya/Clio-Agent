from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from clio.env import env

_provider: TracerProvider | None = None


def setup_tracing(service_name: str = "clio-core") -> trace.TracerProvider:
    global _provider
    if _provider is not None:
        return _provider
    resource = Resource.create({"service.name": service_name})
    _provider = TracerProvider(resource=resource)
    if env("TRACE_CONSOLE"):
        _provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(_provider)
    return _provider
