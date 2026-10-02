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
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from . import _scope
from . import attributes as A

F = TypeVar("F", bound=Callable[..., Any])

_tracer = trace.get_tracer(
    "argus-semconv",
    _scope.version_paquete(),
    attributes=_scope.atributos(),
)

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
        """Como acabo el paso. Vocabulario CERRADO, y ahora comprobado.

        `argus.outcome` estaba declarado `enum` en el modelo desde el principio
        y aqui se aceptaba cualquier cadena. El modelo afirmaba una cosa y el
        codigo hacia otra, que es la forma exacta de defecto que un equipo
        consumidor nos describio el mismo dia que preguntaba por este campo
        (D-096).

        El coste no era teorico: un campo de cardinalidad cerrada existe para
        poder agregarlo, y si cada equipo mete sus propios valores el campo
        esta ahi y no se puede agrupar. Nadie se entera, porque un valor
        inventado se escribe igual de bien que uno bueno.

        AVISA, no levanta. El contrato del SDK es no tumbar nunca la
        aplicacion: un desenlace mal escrito es un dato peor, no un motivo para
        romper un proceso en produccion. El valor se escribe igualmente para no
        perder la informacion.
        """
        if value not in A.ARGUS_OUTCOME_VALUES:
            warnings.warn(
                f"argus.outcome={value!r} no esta en el vocabulario: "
                f"{', '.join(A.ARGUS_OUTCOME_VALUES)}. Se escribe igual, pero no "
                f"agrupara con el resto del portafolio.",
                RuntimeWarning,
                stacklevel=2,
            )
        return self.set(**{A.ARGUS_OUTCOME: value})

    def denied(self, by: str) -> Step:
        """El paso se nego. `by` dice QUE CLASE de decision lo paro.

        Existe como UNA llamada y no como dos porque el fallo de este par no es
        poner un valor malo, es poner medio par: `denied` sin `by` deja una
        denegacion que no se puede atribuir, y `by` sin `denied` cuelga la
        dimension de algo que no fue una denegacion. Las dos mitades sueltas se
        escriben sin error y las dos rompen el `GROUP BY` que es la unica razon
        por la que el campo existe.

        `argus.denied_by` lo pidio Aeon, y su argumento era consecuencia de una
        recomendacion NUESTRA: les dijimos que no pusieran `argus.guardrail` en
        una denegacion de politica —porque ese campo enciende el camino
        caliente— y con eso dejamos de tener cualquier portador de quien denego.

        NO marca `argus.hot`, al contrario que `error()`. Una denegacion es el
        sistema haciendo lo correcto. Lo que si pasa es que la traza se conserva
        entera: la politica `auditoria` del gateway se queda el 100% de las
        trazas con `argus.outcome=denied`.
        """
        return self.set(**{A.ARGUS_OUTCOME: "denied", A.ARGUS_DENIED_BY: by})

    def error(self, error_type: str, *, retryable: bool | None = None) -> Step:
        self.set(**{A.ERROR_TYPE: error_type, A.ARGUS_OUTCOME: "error", A.ARGUS_HOT: True})
        if retryable is not None:
            self.set(**{A.ARGUS_ERROR_RETRYABLE: retryable})
        try:
            self._span.set_status(Status(StatusCode.ERROR, error_type))
        except Exception:  # noqa: BLE001
            pass
        return self

    def _avisar_de_la_atribucion(self) -> None:
        """Comprueba el par `denied` / `denied_by` sobre el estado RESUELTO.

        Aqui y no en `outcome()` por un motivo que ya me costo una ronda: un
        aviso que mira un campo antes de que el paso acabe se dispara sobre
        quien va a ponerlo bien en la linea siguiente. Lo que hay que mirar es
        lo que se emite, no lo que se ha escrito hasta ahora (D-110).

        Y por eso valida tambien el VALOR aqui: asi cubre a quien escribe el
        atributo con `set()` en vez de con `denied()`, que es como lo haran los
        servicios Go que emiten nuestros atributos a mano.
        """
        desenlace = self._fields.get(A.ARGUS_OUTCOME)
        quien = self._fields.get(A.ARGUS_DENIED_BY)
        evento = self._fields.get(A.ARGUS_EVENT, "?")

        if desenlace == "denied" and quien is None:
            warnings.warn(
                f"Argus: {evento!r} se nego sin decir quien.\n"
                f"  `argus.outcome=denied` sin `argus.denied_by` deja una "
                f"denegacion que no se puede atribuir, y el hueco no se ve al "
                f"consultar: un GROUP BY sobre un campo ausente devuelve una "
                f"fila, no una queja.\n"
                f"  Usa `s.denied(by=...)` con uno de: "
                f"{', '.join(A.ARGUS_DENIED_BY_VALUES)}.",
                RuntimeWarning,
                stacklevel=4,
            )
        elif quien is not None and desenlace != "denied":
            # La otra mitad del par. Cuelga la dimension de algo que no fue
            # una denegacion, asi que `GROUP BY argus.denied_by` cuenta pasos
            # que siguieron adelante.
            warnings.warn(
                f"Argus: {evento!r} lleva `argus.denied_by={quien!r}` y su "
                f"desenlace es {desenlace!r}, no `denied`.\n"
                f"  Esa dimension solo tiene sentido sobre una denegacion; "
                f"asi contamina cualquier recuento de denegaciones.",
                RuntimeWarning,
                stacklevel=4,
            )
        elif quien is not None and quien not in A.ARGUS_DENIED_BY_VALUES:
            warnings.warn(
                f"argus.denied_by={quien!r} no esta en el vocabulario: "
                f"{', '.join(A.ARGUS_DENIED_BY_VALUES)}. Se escribe igual, pero "
                f"no agrupara con el resto del portafolio.",
                RuntimeWarning,
                stacklevel=4,
            )

    def _finalize(self) -> None:
        elapsed_ms = int((time.perf_counter() - self._started) * 1000)
        self.set(**{A.ARGUS_DURATION_MS: elapsed_ms})
        # OJO: no poner desenlace NO deja el campo vacio, lo pone en `ok`.
        #
        # Es deliberado —un paso que llega al final sin fallar es un paso que
        # fue bien— y es una trampa para quien asume lo contrario. Aeon planteo
        # dejarlo sin poner mientras un paso espera a una persona; eso habria
        # registrado cada espera como un exito (D-097). Para eso existe
        # `suspended`.
        self._fields.setdefault(A.ARGUS_OUTCOME, "ok")

        self._avisar_de_la_atribucion()

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
