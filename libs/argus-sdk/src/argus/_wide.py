"""El evento ancho en el fallo: la capa de explicacion.

El problema que resuelve, medido el 03/10 contra el almacen:

    747 spans con error en siete dias, 48 servicios
      4 llevaban `argus.event`  ->  y los cuatro eran sondas nuestras de ese dia

O sea: **ningun error real de ningun equipo tenia evento ancho.** Y no por
falta de datos —735 tenian `StatusMessage` y 703 evento de excepcion— sino
porque `emit_wide_event` es API publica que la aplicacion tiene que llamar, y
nadie la llama. Un patron que hay que invocar a mano en el sitio donde las
cosas fallan es un patron que no existe donde hace falta.

Por que un `SpanProcessor` y no un decorador: **los errores no ocurren donde
pusiste el decorador.** De los 747, los que mas importaban salieron de spans
auto-instrumentados —un `GET` de httpx, un `auth.validate`, una activity de
Temporal— que ningun `argus.step` envuelve. Un procesador ve CADA span que
cierra en el proceso, instrumentado por nosotros o no, asi que la cobertura no
depende de que alguien se acordara.

Lo que el evento ancho aporta sobre el span, que ya esta en el almacen: una
LINEA, con todo el contexto adjunto, en el canal donde se busca cuando algo
falla. Las trazas dan el flujo entre servicios; el evento ancho da el contexto
dentro de uno, y es lo que convierte "hubo un error en auth.validate" en algo
que se puede leer sin abrir una traza.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from argus_semconv import attributes as A
from argus_semconv.errors import clase_y_mensaje
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import NonRecordingSpan, StatusCode, set_span_in_context

log = logging.getLogger("argus.wide")

# Contenido de GenAI. NUNCA va al evento ancho: son decenas de KB de prompt por
# span, y el evento ancho existe para leerse. En el almacen de trazas ya se
# borra por el mismo motivo; aqui habria que borrarlo otra vez, y un dato que
# hay que recordar borrar en dos sitios es un dato que se filtra por el que se
# olvida (D-108).
_FUERA: frozenset[str] = frozenset({
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.prompt",
    "gen_ai.completion",
})

# Tope de atributos que se copian. La auto-instrumentacion puede poner muchos y
# el evento ancho no es un volcado: es una linea para leer.
_MAX_CAMPOS = 48

# Tope del valor de un atributo. Un cuerpo de respuesta en un atributo es algo
# que pasa, y el evento ancho no es el sitio.
_MAX_VALOR = 512


class EventoAnchoEnElFallo(SpanProcessor):
    """Emite UN evento ancho por span que termina en error.

    Tres propiedades que el contrato del SDK exige y que estan fijadas por
    pruebas:

    - **No levanta nunca.** `on_end` corre en el hilo de la aplicacion. Una
      excepcion aqui se propaga al codigo de negocio, que es lo contrario de
      lo que esta clase existe para hacer.
    - **No es reentrante.** Si el puente de registro que alimenta estuviera
      instrumentado, emitir desde `on_end` abriria un span, que al cerrar
      volveria aqui. Hay un guardia por hilo.
    - **Solo en el fallo.** Un evento ancho por cada span de una aplicacion con
      trafico seria el volumen que el patron del `canonical log line` viene a
      reducir. Los errores son la excepcion por construccion, asi que el coste
      esta acotado por la propia rareza del caso.
    """

    __slots__ = ("_local",)

    def __init__(self) -> None:
        self._local = threading.local()

    # El SDK de OTel llama a estos dos y no se usan aqui.
    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def on_end(self, span: ReadableSpan) -> None:
        if getattr(self._local, "dentro", False):
            return
        try:
            if span.status is None or span.status.status_code is not StatusCode.ERROR:
                return
            self._local.dentro = True
            try:
                nombre, campos = _componer(span)
                # El contexto del span QUE FALLO, re-activado mientras se emite.
                #
                # Esto es la raiz del problema de correlacion, y no un apaño:
                # en `on_end` el span ya se cerro y el activo es el padre o
                # ninguno, asi que TODO lo que lee el contexto ambiental se
                # equivoca a la vez. Lo vi en tres capas distintas antes de
                # arreglarlo aqui:
                #
                #   1. el `LogRecord`           -> bien, porque lo pongo a mano
                #   2. la linea JSON formateada -> `trace_id: none`
                #   3. el campo TraceId de OTLP -> VACIO, y es el que usa el
                #      almacen para el JOIN con la traza
                #
                # Parchear cada consumidor era perseguir capas. Lo correcto es
                # que el contexto sea cierto mientras se emite, y entonces los
                # tres aciertan solos.
                ficha = otel_context.attach(
                    set_span_in_context(NonRecordingSpan(span.get_span_context()))
                )
                try:
                    log.error(nombre, extra=campos)
                finally:
                    otel_context.detach(ficha)
            finally:
                self._local.dentro = False
        except Exception:  # noqa: BLE001
            # Un fallo al explicar un fallo no puede ser un segundo fallo.
            self._local.dentro = False


def _clase_del_error(span: ReadableSpan) -> tuple[str, str | None]:
    """La clase del error y su mensaje, con la jerarquia COMPARTIDA.

    El orden no se decide aqui: vive en `argus_semconv.errors`, que es lo unico
    que el SDK y el alert-bus pueden importar los dos. Antes estaba escrito
    aqui, otra vez en `normalize.py` y otra en el OTTL del gateway, y los tres
    comentarios afirmaban que coincidian. Dos de las tres copias habian
    divergido ya (D-125).

    Lo que esta funcion si decide es como se sacan los eventos de excepcion de
    un `ReadableSpan`, que es forma del SDK y no de las convenciones.
    """
    return clase_y_mensaje(
        span.attributes,
        (e.attributes or {} for e in span.events or () if e.name == "exception"),
        span.status.description if span.status is not None else None,
    )


def _recortar(valor: Any) -> Any:
    if isinstance(valor, str) and len(valor) > _MAX_VALOR:
        return valor[:_MAX_VALOR] + "…"
    return valor


def _componer(span: ReadableSpan) -> tuple[str, dict[str, Any]]:
    atributos = span.attributes or {}
    clase, mensaje = _clase_del_error(span)

    # El nombre del evento. `argus.event` si el span venia de un `step`, y si no
    # el nombre del span: en los dos casos es de cardinalidad cerrada, que es la
    # condicion para que un `GROUP BY` sobre este campo devuelva algo.
    nombre = str(atributos.get(A.ARGUS_EVENT) or span.name)

    ctx = span.get_span_context()
    campos: dict[str, Any] = {
        A.ARGUS_EVENT: nombre,
        A.ARGUS_OUTCOME: "error",
        A.ERROR_TYPE: clase,
        # El puente de registro ya pone `trace_id` y `span_id` en cada linea,
        # pero este evento se emite desde `on_end`, donde el span ACTIVO ya no
        # es este. Sin ponerlos a mano, la linea apuntaria al padre o a nada —
        # y un evento ancho que no lleva a su traza no sirve para lo unico que
        # se le pide.
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
    }

    if mensaje:
        campos["error.message"] = _recortar(mensaje)

    if span.end_time is not None and span.start_time is not None:
        campos[A.ARGUS_DURATION_MS] = int((span.end_time - span.start_time) / 1_000_000)

    # Y el contexto que la instrumentacion ya habia puesto. Es lo que hace que
    # esto explique algo: de un `GET` que falla, la ruta y el codigo; de una
    # consulta, el sistema y la operacion. Gratis, y sin que la aplicacion
    # tenga que acordarse de nada.
    copiados = 0
    for clave, valor in atributos.items():
        if clave in _FUERA or clave in campos:
            continue
        if copiados >= _MAX_CAMPOS:
            campos["argus.wide.truncated"] = True
            break
        campos[clave] = _recortar(valor)
        copiados += 1

    return nombre, campos
