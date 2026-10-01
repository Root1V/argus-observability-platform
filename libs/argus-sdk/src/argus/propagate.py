"""Propagacion de contexto fuera de HTTP.

En HTTP la propagacion W3C funciona sola. Fuera de HTTP no, y ahi es donde las
trazas se parten: hacer que `traceparent` viaje por las cabeceras de un mensaje
de Kafka, por los headers de una tarea Celery o por el payload de una cola
Redis requiere trabajo explicito en cada frontera.

Es un fallo que no da ningun error. Simplemente el consumidor arranca una traza
nueva en vez de unirse a la del productor, y acabas con dos trazas donde
deberia haber una.

Y la frontera no tiene por que ser una maquina ni un proceso: **un hilo basta**.
El contexto de OTel vive en un `contextvars.ContextVar`, y `ThreadPoolExecutor`
NO copia el contexto al hilo trabajador —`asyncio` si, por eso `await` no da
problemas y `pool.submit` si—. Lo que se pierde ahi no suele ser la traza
entera sino los registros que emite la libreria que corre dentro, que salen con
`trace_id = none` mientras los de al lado salen bien (D-095).
"""

from __future__ import annotations

import contextvars
import functools
import os
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, TypeVar

from argus_semconv import attributes as A
from opentelemetry import baggage, context, propagate, trace
from opentelemetry.trace import SpanKind

# Clave con la que viaja el contexto cuando no hay cabeceras y hay que meterlo
# en el propio payload (colas Redis artesanales, por ejemplo).
PAYLOAD_KEY = "_argus_ctx"

T = TypeVar("T")


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

    Lo que si va: `argus.app` y `argus.run.id`.

    `argus.tenant` estuvo en esta lista y se retiro: un inquilino puede ser
    una persona. Donde un principal se autentica con contrasena o con secreto
    de maquina el identificador es el mismo, asi que el campo no puede
    prometer que no lleva PII — y esta funcion lo listaba como seguro dos
    parrafos despues de declarar la regla dura (D-108).

    Si necesitas atribuir coste por inquilino, el atributo sigue existiendo y
    el gateway lo seudonimiza; lo que no hace es cruzar maquinas en una
    cabecera.
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


# ---------------------------------------------------------------------------
# La frontera mas barata de cruzar mal: otro hilo.
# ---------------------------------------------------------------------------


def with_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Ata una funcion al contexto ACTIVO AHORA, para ejecutarla en otro hilo.

        ctx_fn = argus.propagate.with_context(transcribir)
        futuro = pool.submit(ctx_fn, audio)

    Se captura en el momento de envolver, no en el de ejecutar, que es lo
    correcto: el contexto que importa es el de quien encarga el trabajo.

    Para el caso normal es preferible `Executor`, que no obliga a acordarse en
    cada llamada.
    """
    ctx = contextvars.copy_context()

    @functools.wraps(fn)
    def dentro_del_contexto(*args: Any, **kwargs: Any) -> T:
        return ctx.run(fn, *args, **kwargs)

    return dentro_del_contexto


class Executor(ThreadPoolExecutor):
    """`ThreadPoolExecutor` que se lleva el contexto al hilo trabajador.

    Cambiar la clase es todo lo que hay que hacer: `submit` y `map` propagan
    solos. Es deliberadamente un reemplazo y no un ayudante, porque un ayudante
    hay que recordarlo en cada sitio y el olvido no da error — solo un log
    huerfano que nadie mira hasta que lo necesita.

    Lo que NO cubre: un subproceso. Ahi no hay contexto que copiar y hace falta
    pasar el `traceparent` explicitamente (`inject_env` / `extract_env`).
    """

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        return super().submit(with_context(fn), *args, **kwargs)


# ---------------------------------------------------------------------------
# Subprocesos: no hay contexto que copiar, solo una cadena que pasar.
# ---------------------------------------------------------------------------


def inject_env(env: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Mete el contexto en variables de entorno, para lanzar un subproceso.

        hijo = subprocess.run(cmd, env=argus.propagate.inject_env(dict(os.environ)))

    Va en MAYUSCULAS porque es lo que espera un entorno de proceso, y la
    contraparte acepta las dos grafias: la cabecera HTTP llega en minusculas y
    mas de una vez alguien copia una en el sitio de la otra.
    """
    destino: MutableMapping[str, str] = {} if env is None else env
    for clave, valor in inject_headers().items():
        destino[clave.upper().replace("-", "_")] = valor
    return dict(destino)


@contextmanager
def extract_env(
    env: Mapping[str, str] | None = None,
    *,
    name: str,
    kind: SpanKind = SpanKind.CONSUMER,
) -> Iterator[trace.Span]:
    """Contraparte de `inject_env`, para el arranque del subproceso.

        with argus.propagate.extract_env(name="diarization.run"):
            ...
    """
    origen = os.environ if env is None else env
    carrier = {
        clave.lower().replace("_", "-"): valor
        for clave, valor in origen.items()
        if clave.upper() in ("TRACEPARENT", "TRACESTATE", "BAGGAGE")
    }
    with extract_headers(carrier, name=name, kind=kind) as span:
        yield span


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
