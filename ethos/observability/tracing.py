from __future__ import annotations

import contextvars
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_provider: TracerProvider | None = None


def setup_tracing(service_name: str = "ethos-core") -> trace.TracerProvider:
    global _provider
    if _provider is not None:
        return _provider
    resource = Resource.create({"service.name": service_name})
    _provider = TracerProvider(resource=resource)
    if os.environ.get("ETHOS_TRACE_CONSOLE"):
        _provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(_provider)
    return _provider


def get_tracer(name: str = "ethos") -> trace.Tracer:
    setup_tracing()
    return trace.get_tracer(name)


_current_span_context: contextvars.ContextVar[str] = contextvars.ContextVar("ethos_span", default="")


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as sp:
        if attributes:
            for key, value in attributes.items():
                sp.set_attribute(key, value)
        yield sp


def current_trace_id() -> str:
    sp = trace.get_current_span()
    ctx = sp.get_span_context()
    if not ctx.is_valid:
        return ""
    return f"{ctx.trace_id:032x}"
