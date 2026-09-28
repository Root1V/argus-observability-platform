"""El contexto no cruza a otro hilo, y eso rompe los logs sin romper la traza.

Sale de una integracion real. Prosodia cerro su piloto con 61 de 67 registros
correlacionados y dio los seis restantes por resto. Cuatro de ellos eran de
`mlx_whisper` y `pyannote`, y el patron se veia al ordenarlos por tiempo: dos
registros separados por UN milisegundo, el del pipeline con `trace_id` y el de
la libreria sin el (D-095).

La causa no es Celery ni HTTP: es que el contexto de OTel vive en un
`contextvars.ContextVar` y `ThreadPoolExecutor` no lo copia al hilo trabajador.
`asyncio` si lo copia, que es por lo que el resto del pipeline —asincrono—
funcionaba y solo fallaba lo que pasaba por el pool.

Lo que se pierde importa mas de lo que su numero sugiere: eran las etapas de
transcripcion y diarizacion, o sea las mas lentas. Los registros que explican
"por que tardo tanto" son justo los que se quedan sin traza.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from argus import propagate
from argus._logging import TraceCorrelationFilter
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


def _traza_vista_por_un_log() -> str:
    """Lo que acabaria en el campo `trace_id` de un registro JSON.

    Se mide a traves del filtro de correlacion y no de la API de trazas a
    proposito: el sintoma que reporto el equipo era un log huerfano, asi que la
    prueba tiene que mirar por donde se vio.
    """
    registro = logging.LogRecord("x", logging.INFO, "", 0, "m", None, None)
    TraceCorrelationFilter().filter(registro)
    return registro.trace_id


def _tracer() -> trace.Tracer:
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())
    return trace.get_tracer("pruebas-hilos")


def test_el_executor_de_la_libreria_estandar_pierde_la_traza() -> None:
    """Se fija el fallo, no solo el arreglo.

    Sin esto, el dia que `concurrent.futures` propague contexto por su cuenta
    nadie sabria que `propagate.Executor` ya no hace falta, y seguiria ahi
    resolviendo un problema que no existe.
    """
    with _tracer().start_as_current_span("etapa"):
        assert _traza_vista_por_un_log() != "none"
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_traza_vista_por_un_log).result() == "none"


def test_el_executor_propio_la_conserva() -> None:
    with _tracer().start_as_current_span("etapa") as span:
        esperado = format(span.get_span_context().trace_id, "032x")
        with propagate.Executor(max_workers=1) as pool:
            assert pool.submit(_traza_vista_por_un_log).result() == esperado


def test_map_tambien_propaga() -> None:
    """`map` va aparte porque no es obvio que pase por `submit`."""
    with _tracer().start_as_current_span("etapa") as span:
        esperado = format(span.get_span_context().trace_id, "032x")
        with propagate.Executor(max_workers=2) as pool:
            vistos = list(pool.map(lambda _: _traza_vista_por_un_log(), range(4)))
        assert vistos == [esperado] * 4


def test_with_context_captura_al_envolver_no_al_ejecutar() -> None:
    """El contexto que importa es el de quien ENCARGA el trabajo.

    Si se capturara al ejecutar, la funcion se ataria al contexto del hilo
    trabajador —que no tiene ninguno— y el arreglo no arreglaria nada.
    """
    with _tracer().start_as_current_span("etapa") as span:
        esperado = format(span.get_span_context().trace_id, "032x")
        atado = propagate.with_context(_traza_vista_por_un_log)

    # Fuera del span: si capturara ahora, veria "none".
    assert _traza_vista_por_un_log() == "none"
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(atado).result() == esperado


def test_sin_traza_activa_no_estalla() -> None:
    """Contrato del SDK: nunca tumbar la aplicacion."""
    with propagate.Executor(max_workers=1) as pool:
        assert pool.submit(_traza_vista_por_un_log).result() == "none"


def test_conserva_el_nombre_de_la_funcion_envuelta() -> None:
    """Un `functools.wraps` que falte convierte cada traza de pila y cada
    metrica por funcion en `dentro_del_contexto`."""

    def transcribir() -> None:
        return None

    assert propagate.with_context(transcribir).__name__ == "transcribir"


def test_el_contexto_llega_a_un_subproceso_de_verdad() -> None:
    """Un hilo copia contexto; un proceso no. Ahi hay que pasar la cadena.

    Se lanza un proceso REAL y no se simula el entorno: lo que se comprueba es
    justamente que sobrevive al `execve`, y un diccionario pasado a mano no
    prueba eso.
    """
    hijo = """
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from argus import propagate
trace.set_tracer_provider(TracerProvider())
with propagate.extract_env(name="diarization.run") as s:
    print(format(s.get_span_context().trace_id, "032x"))
"""
    with _tracer().start_as_current_span("etapa.diarizacion") as span:
        esperado = format(span.get_span_context().trace_id, "032x")
        entorno = propagate.inject_env(dict(os.environ))

    assert entorno["TRACEPARENT"].split("-")[1] == esperado
    salida = subprocess.run(
        [sys.executable, "-c", hijo], env=entorno, capture_output=True, text=True, timeout=60
    )
    assert salida.returncode == 0, salida.stderr
    assert salida.stdout.strip() == esperado


def test_extract_env_admite_las_dos_grafias() -> None:
    """La cabecera HTTP viene en minusculas y el entorno en mayusculas, y mas
    de una vez alguien copia una en el sitio de la otra."""
    with _tracer().start_as_current_span("etapa") as span:
        esperado = format(span.get_span_context().trace_id, "032x")
        cabeceras = propagate.inject_headers()

    for entorno in ({"TRACEPARENT": cabeceras["traceparent"]}, dict(cabeceras)):
        with propagate.extract_env(entorno, name="hijo") as hijo:
            assert format(hijo.get_span_context().trace_id, "032x") == esperado


def test_sin_traceparent_en_el_entorno_arranca_una_raiz() -> None:
    """Un subproceso lanzado a mano no tiene por que fallar por no traer traza."""
    with propagate.extract_env({}, name="hijo") as span:
        assert span.get_span_context().trace_id != 0
