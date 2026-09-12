"""Propagacion de contexto fuera de HTTP.

En HTTP la propagacion W3C funciona sola. Fuera de HTTP no, y ahi es donde las
trazas se parten: hacer que `traceparent` viaje por las cabeceras de un mensaje
de Kafka, por los headers de una tarea Celery o por el payload de una cola
Redis requiere trabajo explicito en cada frontera.

Es un fallo que no da ningun error. Simplemente el consumidor arranca una traza
nueva en vez de unirse a la del productor, y acabas con dos trazas donde
deberia haber una.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any

from argus_semconv import attributes as A
from opentelemetry import baggage, context, propagate, trace
from opentelemetry.trace import SpanKind

# Clave con la que viaja el contexto cuando no hay cabeceras y hay que meterlo
# en el propio payload (colas Redis artesanales, por ejemplo).
PAYLOAD_KEY = "_argus_ctx"


def inject_headers(carrier: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Inyecta el contexto actual en un diccionario de cabeceras.

    Para Kafka, Redpanda, o cualquier transporte con cabeceras de mensaje.

        headers = argus.propagate.inject_headers()
        await producer.send(topic, value=payload, headers=list(headers.items()))
    """
    target: MutableMapping[str, str] = carrier if carrier is not None else {}
    propagate.inject(target)
    return dict(target)


@contextmanager
def extract_headers(
    carrier: Mapping[str, str] | None,
    *,
    name: str,
    kind: SpanKind = SpanKind.CONSUMER,
) -> Iterator[trace.Span]:
    """Reanuda la traza del productor y abre un span hijo.

        with argus.propagate.extract_headers(dict(msg.headers), name="orders.consume"):
            process(msg)
    """
    parent = propagate.extract(carrier or {})
    token = context.attach(parent)
    tracer = trace.get_tracer("argus-sdk")
    try:
        with tracer.start_as_current_span(name, kind=kind) as span:
            yield span
    finally:
        context.detach(token)


def inject_payload(payload: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Mete el contexto DENTRO del payload.

    Para colas artesanales sobre Redis o Postgres, donde no hay cabeceras de
    mensaje y el unico canal disponible es el propio cuerpo.
    """
    payload[PAYLOAD_KEY] = inject_headers()
    return payload


@contextmanager
def extract_payload(
    payload: Mapping[str, Any] | None,
    *,
    name: str,
    kind: SpanKind = SpanKind.CONSUMER,
) -> Iterator[trace.Span]:
    """Contraparte de `inject_payload`."""
    carrier = (payload or {}).get(PAYLOAD_KEY) or {}
    with extract_headers(carrier, name=name, kind=kind) as span:
        yield span


def set_baggage(**values: str) -> object:
    """Anade valores al baggage, que viaja entre procesos.

    REGLA DURA: el baggage es pequeno, acotado y SIN PII. Viaja en cabeceras y
    cruza procesos y maquinas; todo lo que metas aqui acaba en sitios que no
    controlas.

    Lo que si va: `argus.app`, `argus.run.id`, `argus.tenant`.
    """
    ctx = context.get_current()
    for key, value in values.items():
        ctx = baggage.set_baggage(key, value, context=ctx)
    return context.attach(ctx)


def get_baggage(key: str) -> str | None:
    value = baggage.get_baggage(key)
    return str(value) if value is not None else None


@contextmanager
def run(name: str, *, run_id: str | None = None, app: str | None = None) -> Iterator[trace.Span]:
    """Abre una traza raiz para un CLI o un trabajo por lotes.

    Los procesos cortos necesitan ademas un `force_flush()` al salir, o la
    telemetria muere con el proceso antes de exportarse. `argus.init()` devuelve
    un handle con ese metodo, y `atexit` lo llama.
    """
    import uuid

    resolved_run_id = run_id or uuid.uuid4().hex
    token = set_baggage(**{k: v for k, v in {A.ARGUS_RUN_ID: resolved_run_id, A.ARGUS_APP: app}.items() if v})

    tracer = trace.get_tracer("argus-sdk")
    try:
        with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
            span.set_attribute(A.ARGUS_RUN_ID, resolved_run_id)
            if app:
                span.set_attribute(A.ARGUS_APP, app)
            yield span
    finally:
        context.detach(token)  # type: ignore[arg-type]


def instrument_celery() -> bool:
    """Activa la propagacion en Celery.

    El contexto viaja en los headers de la tarea, que es lo que hace que el
    worker se una a la traza de quien la encolo en vez de empezar una nueva.
    """
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
        return True
    except Exception:  # noqa: BLE001
        return False
