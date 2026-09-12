"""Fixtures compartidas por toda la suite.

Por que estan aqui y no en cada paquete: OpenTelemetry solo permite fijar el
TracerProvider GLOBAL una vez por proceso. Un segundo `set_tracer_provider()`
se ignora con un aviso, asi que si cada modulo de tests montara el suyo, solo
el primero recibiria spans y el resto fallaria de forma desconcertante.

Un unico proveedor de sesion, un unico exportador en memoria, y cada test lo
vacia antes de usarlo.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


@pytest.fixture(scope="session")
def _exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # El propagador compuesto tiene que ser identico en todos los servicios y
    # lenguajes. Si uno usa W3C y otro no, la traza se parte en silencio.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )
    return exporter


@pytest.fixture
def spans(_exporter: InMemorySpanExporter) -> InMemorySpanExporter:
    """Exportador en memoria, vaciado antes de cada test."""
    _exporter.clear()
    return _exporter


@pytest.fixture
def exporter(spans: InMemorySpanExporter) -> InMemorySpanExporter:
    """Alias, para tests que prefieren ese nombre."""
    return spans
