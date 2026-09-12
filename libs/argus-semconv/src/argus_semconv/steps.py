"""Unidades de trabajo de negocio: wide events y spans en una sola llamada.

Un `step` es una unidad de trabajo. Al cerrarse emite UN evento ancho con todo
el contexto acumulado, y ese mismo contexto va al span.

Esa union es deliberada. El patron de "canonical log line" dice que emitas una
linea estructurada y ancha por unidad de trabajo en vez de decenas sueltas. Si
ademas el evento ancho ES el span enriquecido, no hay dos canales que
correlacionar despues: son el mismo dato.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from . import attributes as A

F = TypeVar("F", bound=Callable[..., Any])

_tracer = trace.get_tracer("argus-semconv", A.SEMCONV_VERSION)

# Umbral por defecto para marcar un span como candidato a incidente. Lo usa el
# filtro del Collector agente para enrutar al camino caliente de deteccion.
# Se sobreescribe por paso o por componente.
_DEFAULT_SLO_MS = 0  # 0 = sin umbral; el SDK de aplicacion lo inyecta


class Step:
    """Unidad de trabajo con contexto acumulable.

    Nunca lanza al registrar. Un fallo de telemetria no puede propagarse.
    """

    __slots__ = ("_fields", "_slo_ms", "_span", "_started")

    def __init__(self, span: Span, *, slo_ms: int = _DEFAULT_SLO_MS) -> None:
        self._span = span
        self._fields: dict[str, Any] = {}
        self._started = time.perf_counter()
        self._slo_ms = slo_ms

    @property
    def span(self) -> Span:
        return self._span

    @property
    def fields(self) -> dict[str, Any]:
        """Contexto acumulado. Lo lee el SDK de aplicacion para el evento ancho."""
        return self._fields

    def set(self, **fields: Any) -> Step:
        """Acumula contexto. Va al span y al evento ancho final."""
        for key, value in fields.items():
            if value is None:
                continue
            self._fields[key] = value
            try:
                if self._span.is_recording():
                    self._span.set_attribute(key, value)
            except Exception:  # noqa: BLE001
                pass
        return self

    def outcome(self, value: str) -> Step:
        return self.set(**{A.ARGUS_OUTCOME: value})

    def error(self, error_type: str, *, retryable: bool | None = None) -> Step:
        self.set(**{A.ERROR_TYPE: error_type, A.ARGUS_OUTCOME: "error", A.ARGUS_HOT: True})
        if retryable is not None:
            self.set(**{A.ARGUS_ERROR_RETRYABLE: retryable})
        try:
            self._span.set_status(Status(StatusCode.ERROR, error_type))
        except Exception:  # noqa: BLE001
            pass
        return self

    def _finalize(self) -> None:
        elapsed_ms = int((time.perf_counter() - self._started) * 1000)
        self.set(**{A.ARGUS_DURATION_MS: elapsed_ms})
        self._fields.setdefault(A.ARGUS_OUTCOME, "ok")

        # Marca para el camino caliente: si la operacion supero el objetivo de
        # latencia de su componente, el Collector agente la enruta a deteccion
        # sin esperar al tail sampling.
        if self._slo_ms and elapsed_ms > self._slo_ms:
            self.set(**{
                A.ARGUS_SLO_BREACHED: True,
                A.ARGUS_SLO_THRESHOLD_MS: self._slo_ms,
                A.ARGUS_HOT: True,
            })


@contextmanager
def step(name: str, *, slo_ms: int = _DEFAULT_SLO_MS, kind: SpanKind = SpanKind.INTERNAL, **fields: Any) -> Iterator[Step]:
    """Abre una unidad de trabajo.

        with step("document.process", pages=42) as s:
            ...
            s.set(extracted_fields=17).outcome("ok")
    """
    with _tracer.start_as_current_span(name, kind=kind) as span:
        current = Step(span, slo_ms=slo_ms)
        current.set(**{A.ARGUS_EVENT: name})
        if fields:
            current.set(**fields)
        try:
            yield current
        except Exception as exc:
            current.error(type(exc).__name__)
            raise
        finally:
            current._finalize()


def instrument(name: str | None = None, *, slo_ms: int = _DEFAULT_SLO_MS, **fields: Any) -> Callable[[F], F]:
    """Decorador equivalente a `step`, para funciones sync y async."""

    def decorator(func: F) -> F:
        event = name or f"{func.__module__.rsplit('.', 1)[-1]}.{func.__name__}"

        if _is_coroutine(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with step(event, slo_ms=slo_ms, **fields):
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with step(event, slo_ms=slo_ms, **fields):
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def _is_coroutine(func: Callable[..., Any]) -> bool:
    import asyncio

    return asyncio.iscoroutinefunction(func)
