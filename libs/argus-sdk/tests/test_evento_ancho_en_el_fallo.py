"""El evento ancho en el fallo.

El numero que motivo esto, medido contra el almacen el 03/10: de 747 spans con
error en siete dias, **cuatro** llevaban `argus.event`, y los cuatro eran
sondas nuestras de ese mismo dia. Ningun error real de ningun equipo tenia
evento ancho.

Y no por falta de datos: 735 tenian `StatusMessage` y 703 evento de excepcion.
Era que `emit_wide_event` es API publica que la aplicacion tiene que llamar, y
nadie la llama. Un patron que hay que invocar a mano en el sitio donde las
cosas fallan es un patron que no existe donde hace falta.
"""

from __future__ import annotations

import logging

import pytest
from argus._wide import EventoAnchoEnElFallo
from argus_semconv import attributes as A
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode


@pytest.fixture
def proveedor() -> TracerProvider:
    """Proveedor PROPIO, no el global del conftest.

    El procesador se engancha a un proveedor, y el global de la suite lo
    comparten todos los tests: enganchar ahi emitiria eventos anchos durante
    los tests de otros modulos.
    """
    p = TracerProvider()
    p.add_span_processor(EventoAnchoEnElFallo())
    return p


class _Lineas:
    """Lector perezoso de `caplog.records`.

    Primero devolvi `caplog.records` desde la fixture y los doce tests vieron
    cero lineas: es una PROPIEDAD que construye una lista nueva en cada acceso,
    asi que guardarla es guardar una instantanea vacia. El codigo estaba bien y
    lo roto era la prueba — se vio porque el propio pytest imprimia las lineas
    capturadas justo debajo del fallo.
    """

    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog

    def __iter__(self):
        return iter(self._caplog.records)


@pytest.fixture
def lineas(caplog: pytest.LogCaptureFixture) -> _Lineas:
    caplog.set_level(logging.ERROR, logger="argus.wide")
    return _Lineas(caplog)


def _campos(registro: logging.LogRecord) -> dict:
    return registro.__dict__


# --- Lo que existe y lo que no -----------------------------------------------


def test_un_span_que_falla_emite_un_evento_ancho(proveedor, lineas) -> None:
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("auth.validate") as s:
        s.set_attribute("http.route", "/admin/api/auth/login")
        s.set_attribute("http.response.status_code", 500)
        s.set_status(Status(StatusCode.ERROR))

    anchos = [r for r in lineas if r.name == "argus.wide"]
    assert len(anchos) == 1, "un span fallido tiene que dar UN evento ancho"
    c = _campos(anchos[0])
    assert anchos[0].getMessage() == "auth.validate"
    assert c[A.ARGUS_OUTCOME] == "error"
    # El contexto que la instrumentacion ya habia puesto: es lo que explica.
    assert c["http.route"] == "/admin/api/auth/login"
    assert c["http.response.status_code"] == 500


def test_un_span_sano_no_emite_nada(proveedor, lineas) -> None:
    """Un evento ancho por cada span seria el volumen que el patron del
    `canonical log line` viene a reducir."""
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("ok") as s:
        s.set_attribute("http.route", "/salud")

    assert not [r for r in lineas if r.name == "argus.wide"]


def test_cubre_un_span_que_NADIE_envolvio(proveedor, lineas) -> None:
    """La razon de ser de un `SpanProcessor` en vez de un decorador.

    Los errores no ocurren donde pusiste el decorador: de los 747, los que mas
    importaban salieron de spans auto-instrumentados —un `GET` de httpx, una
    activity de Temporal— que ningun `argus.step` envuelve.
    """
    t = proveedor.get_tracer("httpx-simulado")
    with t.start_as_current_span("GET") as s:  # sin `argus.event`, como la auto-inst.
        s.set_attribute("error.type", "ReadTimeout")
        s.set_status(Status(StatusCode.ERROR))

    anchos = [r for r in lineas if r.name == "argus.wide"]
    assert len(anchos) == 1
    assert anchos[0].getMessage() == "GET", "sin `argus.event` se usa el nombre del span"
    assert _campos(anchos[0])[A.ERROR_TYPE] == "ReadTimeout"


# --- La clase del error ------------------------------------------------------


def test_la_clase_se_rescata_del_evento_de_excepcion(proveedor, lineas) -> None:
    """46 de los 747 estaban marcados `Error` SIN `error.type`.

    La materia prima estaba en el evento de excepcion, que es otro ambito y que
    nadie subia. Es la misma jerarquia que aplica el gateway (D-115).
    """
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("RunActivity:execute_tool_activity") as s:
        try:
            raise TypeError("argumento invalido")
        except TypeError as exc:
            s.record_exception(exc)
        s.set_status(Status(StatusCode.ERROR))

    c = _campos(next(r for r in lineas if r.name == "argus.wide"))
    assert c[A.ERROR_TYPE] == "TypeError"
    assert c["error.message"] == "argumento invalido"


def test_un_error_type_puesto_gana_al_de_la_excepcion(proveedor, lineas) -> None:
    """Al reves, un `http-502` con un `ValueError` interno se agruparia por el
    ValueError, que es el detalle de implementacion y no el fallo que ve quien
    llama. Misma precedencia que en el gateway."""
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("GET") as s:
        s.set_attribute("error.type", "http-502")
        try:
            raise ValueError("interno")
        except ValueError as exc:
            s.record_exception(exc)
        s.set_status(Status(StatusCode.ERROR))

    assert _campos(next(r for r in lineas if r.name == "argus.wide"))[A.ERROR_TYPE] == "http-502"


def test_sin_nada_que_clasificar_queda_unclassified(proveedor, lineas) -> None:
    """Un VALOR, no un hueco: distingue "no se pudo clasificar" de "no es un
    error". Mismo criterio que `unknown` en `argus.actor.kind`."""
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("backend.stop") as s:
        s.set_status(Status(StatusCode.ERROR))

    assert _campos(next(r for r in lineas if r.name == "argus.wide"))[A.ERROR_TYPE] == "unclassified"


# --- La correlacion, que es lo unico que se le pide --------------------------


def test_lleva_el_trace_id_del_span_QUE_FALLO(proveedor, lineas) -> None:
    """La trampa fina de emitir desde `on_end`.

    Ahi el span activo ya NO es este: es el padre, o ninguno. El puente de
    registro pondria el `trace_id` del contexto activo, asi que la linea
    apuntaria al padre — y un evento ancho que no lleva a su traza no sirve
    para lo unico que se le pide. Van a mano.
    """
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("padre") as padre:
        with t.start_as_current_span("hijo") as hijo:
            hijo.set_status(Status(StatusCode.ERROR))
            esperado_traza = format(hijo.get_span_context().trace_id, "032x")
            esperado_span = format(hijo.get_span_context().span_id, "016x")
            del_padre = format(padre.get_span_context().span_id, "016x")

    c = _campos(next(r for r in lineas if r.name == "argus.wide"))
    assert c["trace_id"] == esperado_traza
    assert c["span_id"] == esperado_span
    assert c["span_id"] != del_padre, "el evento ancho apunta al padre, no al span que fallo"


def test_lleva_la_duracion(proveedor, lineas) -> None:
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("lento") as s:
        s.set_status(Status(StatusCode.ERROR))

    c = _campos(next(r for r in lineas if r.name == "argus.wide"))
    assert A.ARGUS_DURATION_MS in c
    assert isinstance(c[A.ARGUS_DURATION_MS], int)


# --- Lo que NO puede llevar --------------------------------------------------


def test_el_contenido_de_genai_no_va_en_el_evento_ancho(proveedor, lineas) -> None:
    """Son decenas de KB de prompt por span, y esto existe para leerse.

    Y es el patron de D-108: un dato que hay que recordar borrar en dos sitios
    es un dato que se filtra por el que se olvida. En el almacen de trazas ya
    se borra; aqui no se copia nunca.
    """
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("chat") as s:
        s.set_attribute("gen_ai.input.messages", '[{"role":"user","content":"' + "x" * 5000 + '"}]')
        s.set_attribute("gen_ai.output.messages", "respuesta larguisima")
        s.set_attribute("gen_ai.request.model", "qwen3-0.6b")
        s.set_status(Status(StatusCode.ERROR))

    c = _campos(next(r for r in lineas if r.name == "argus.wide"))
    assert "gen_ai.input.messages" not in c
    assert "gen_ai.output.messages" not in c
    # Y lo que SI es contexto util se queda.
    assert c["gen_ai.request.model"] == "qwen3-0.6b"


def test_un_valor_enorme_se_recorta(proveedor, lineas) -> None:
    """Un cuerpo de respuesta en un atributo es algo que pasa."""
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("x") as s:
        s.set_attribute("respuesta.cuerpo", "y" * 10_000)
        s.set_status(Status(StatusCode.ERROR))

    valor = _campos(next(r for r in lineas if r.name == "argus.wide"))["respuesta.cuerpo"]
    assert len(valor) < 600, f"se copiaron {len(valor)} caracteres sin recortar"


def test_muchos_atributos_se_truncan_y_SE_DICE(proveedor, lineas) -> None:
    """Truncar en silencio es peor que no truncar: quien lee no sabe que falta."""
    t = proveedor.get_tracer("x")
    with t.start_as_current_span("x") as s:
        for i in range(200):
            s.set_attribute(f"campo.{i}", i)
        s.set_status(Status(StatusCode.ERROR))

    c = _campos(next(r for r in lineas if r.name == "argus.wide"))
    assert c.get("argus.wide.truncated") is True, "se trunco sin decirlo"


# --- El contrato del SDK: no tumbar nunca la aplicacion ----------------------


def test_no_levanta_aunque_el_span_sea_raro(lineas) -> None:
    """`on_end` corre en el hilo de la APLICACION.

    Una excepcion aqui se propaga al codigo de negocio, que es lo contrario de
    lo que esta clase existe para hacer. Se le pasa un objeto que no es un span.
    """
    procesador = EventoAnchoEnElFallo()

    class NoEsUnSpan:
        status = None
        name = "raro"
        attributes = None
        events = None

        def get_span_context(self):
            raise RuntimeError("ni esto funciona")

    procesador.on_end(NoEsUnSpan())  # type: ignore[arg-type]
    procesador.on_end(None)  # type: ignore[arg-type]


def test_no_es_reentrante(proveedor, lineas) -> None:
    """Si el puente de registro estuviera instrumentado, emitir desde `on_end`
    abriria un span, que al cerrar volveria aqui. El guardia es por hilo."""
    procesador = EventoAnchoEnElFallo()
    procesador._local.dentro = True  # como si ya estuvieramos dentro

    t = proveedor.get_tracer("x")
    with t.start_as_current_span("x") as s:
        s.set_status(Status(StatusCode.ERROR))
        procesador.on_end(s)  # type: ignore[arg-type]

    # El del proveedor emite uno; el reentrante, ninguno.
    assert len([r for r in lineas if r.name == "argus.wide"]) == 1


def test_el_guardia_se_libera_tras_emitir(proveedor, lineas) -> None:
    """Un guardia que no se libera convierte el primer error en el unico."""
    t = proveedor.get_tracer("x")
    for i in range(3):
        with t.start_as_current_span(f"fallo-{i}") as s:
            s.set_status(Status(StatusCode.ERROR))

    assert len([r for r in lineas if r.name == "argus.wide"]) == 3


# --- La LINEA emitida, no el LogRecord ---------------------------------------
#
# Esta seccion existe porque las pruebas de arriba pasaban todas y la salida
# real decia `"trace_id": "none"`. Comprobaban el `LogRecord`, donde el valor
# si estaba; el filtro de correlacion lo pisaba una capa mas abajo, con el span
# ACTIVO — que en `on_end` es el padre o ninguno.
#
# Cuarta vez esta semana que la comprobacion mira un nivel y el defecto vive en
# el siguiente. Asi que aqui se formatea de verdad.


def test_la_linea_FORMATEADA_lleva_la_traza_del_span_que_fallo(proveedor) -> None:
    """Se formatea con el formateador real y el filtro real."""
    import json

    from argus._logging import JSONFormatter, TraceCorrelationFilter

    capturados: list[logging.LogRecord] = []

    class Captura(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            capturados.append(record)

    manejador = Captura()
    manejador.addFilter(TraceCorrelationFilter())
    propio = logging.getLogger("argus.wide")
    propio.addHandler(manejador)
    propio.setLevel(logging.ERROR)
    try:
        t = proveedor.get_tracer("x")
        with t.start_as_current_span("padre"):
            with t.start_as_current_span("hijo") as hijo:
                hijo.set_status(Status(StatusCode.ERROR))
                esperado = format(hijo.get_span_context().trace_id, "032x")
                esperado_span = format(hijo.get_span_context().span_id, "016x")
    finally:
        propio.removeHandler(manejador)

    assert capturados, "no se capturo ninguna linea"
    linea = json.loads(JSONFormatter("gateway", "pruebas").format(capturados[-1]))
    assert linea["trace_id"] == esperado, (
        f"la linea emitida dice trace_id={linea['trace_id']!r}. Un evento ancho "
        f"que no lleva a su traza no sirve para lo unico que se le pide"
    )
    assert linea["span_id"] == esperado_span


def test_una_linea_normal_sigue_tomando_la_traza_del_contexto(proveedor) -> None:
    """La guarda no puede romper el caso de siempre: un `logger.info()` dentro
    de un span sigue heredando su traza del contexto activo."""
    import json

    from argus._logging import JSONFormatter, TraceCorrelationFilter

    capturados: list[logging.LogRecord] = []

    class Captura(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            capturados.append(record)

    manejador = Captura()
    manejador.addFilter(TraceCorrelationFilter())
    propio = logging.getLogger("prueba.normal")
    propio.addHandler(manejador)
    propio.setLevel(logging.INFO)
    try:
        t = proveedor.get_tracer("x")
        with t.start_as_current_span("trabajo") as s:
            esperado = format(s.get_span_context().trace_id, "032x")
            propio.info("algo pasa")
    finally:
        propio.removeHandler(manejador)

    linea = json.loads(JSONFormatter("gateway", "pruebas").format(capturados[-1]))
    assert linea["trace_id"] == esperado


def test_el_REGISTRO_OTLP_lleva_la_traza(proveedor) -> None:
    """La capa que de verdad importa: el campo que el almacen usa para el JOIN.

    Las pruebas de arriba comprobaban el `LogRecord` y la linea formateada, y
    las dos pasaban mientras la columna `TraceId` de `otel_logs` llegaba VACIA.
    Un evento ancho que no se puede unir a su traza no sirve para lo unico que
    se le pide, y esa union la hace el campo OTLP, no el atributo.

    Tercera capa del mismo defecto, y la unica que se ve desde el almacen.
    """
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    exportador = InMemoryLogExporter()
    proveedor_logs = LoggerProvider()
    proveedor_logs.add_log_record_processor(SimpleLogRecordProcessor(exportador))
    manejador = LoggingHandler(level=logging.ERROR, logger_provider=proveedor_logs)

    propio = logging.getLogger("argus.wide")
    propio.addHandler(manejador)
    propio.setLevel(logging.ERROR)
    try:
        t = proveedor.get_tracer("x")
        with t.start_as_current_span("padre"):
            with t.start_as_current_span("hijo") as hijo:
                hijo.set_status(Status(StatusCode.ERROR))
                esperada = hijo.get_span_context().trace_id
                esperado_span = hijo.get_span_context().span_id
    finally:
        propio.removeHandler(manejador)

    registros = exportador.get_finished_logs()
    assert registros, "no llego ningun registro OTLP"
    r = registros[-1].log_record
    assert r.trace_id == esperada, (
        f"el registro OTLP lleva trace_id={r.trace_id!r} en vez de {esperada!r}: "
        f"el almacen no podra unirlo con su traza"
    )
    assert r.span_id == esperado_span, "apunta al padre, no al span que fallo"
