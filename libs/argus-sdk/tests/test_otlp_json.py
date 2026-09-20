"""El exportador OTLP/HTTP+JSON, que es el que permite vivir sin protobuf.

Estos tests existen porque un exportador mal serializado NO falla en alto:
el Collector acepta el cuerpo, contesta 200, y la telemetria llega mutilada.
Cada uno de ellos cubre un fallo que se vio de verdad al probarlo contra el
Collector real, no un caso imaginado.
"""

from __future__ import annotations

import json
import logging

import pytest
from argus._otlp_json import (
    _url,
    _valor,
    construir_logs,
    construir_metricas,
    construir_trazas,
)

# --- Codificacion de valores ----------------------------------------------

def test_bool_no_se_convierte_en_entero():
    """En Python `bool` ES `int`. Comprobarlo al reves convierte True en 1."""
    assert _valor(True) == {"boolValue": True}
    assert _valor(1) == {"intValue": "1"}


def test_los_enteros_van_como_cadena():
    """Mapeo JSON de protobuf: un uint64 no cabe en un numero de JSON."""
    assert _valor(2**53 + 1) == {"intValue": str(2**53 + 1)}


def test_tipos_compuestos():
    assert _valor([1, "a"]) == {"arrayValue": {"values": [{"intValue": "1"}, {"stringValue": "a"}]}}
    assert _valor(1.5) == {"doubleValue": 1.5}


def test_url_no_duplica_la_ruta():
    assert _url("http://localhost:4318", "/v1/traces") == "http://localhost:4318/v1/traces"
    assert _url("http://localhost:4318/v1/traces", "/v1/traces") == "http://localhost:4318/v1/traces"
    assert _url("http://localhost:4318/", "/v1/traces") == "http://localhost:4318/v1/traces"


# --- Trazas ----------------------------------------------------------------

@pytest.fixture
def span_exportado():
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memoria = InMemorySpanExporter()
    proveedor = TracerProvider(resource=Resource.create({"service.name": "s", "service.namespace": "n"}))
    proveedor.add_span_processor(SimpleSpanProcessor(memoria))
    tracer = proveedor.get_tracer("prueba")

    with tracer.start_as_current_span("padre") as padre:
        padre.set_attribute("entero", 42)
        padre.add_event("evento", {"paso": 1})
        with tracer.start_as_current_span("hijo"):
            pass
    return memoria.get_finished_spans()


def test_los_identificadores_van_en_hexadecimal(span_exportado):
    """Es la unica desviacion de OTLP sobre el mapeo JSON canonico.

    En base64 el Collector devuelve 400 sin decir por que.
    """
    cuerpo = construir_trazas(span_exportado)
    span = cuerpo["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    int(span["traceId"], 16)  # hexadecimal valido o ValueError
    int(span["spanId"], 16)


def test_el_recurso_viaja_con_los_spans(span_exportado):
    cuerpo = construir_trazas(span_exportado)
    atributos = {a["key"]: a["value"] for a in cuerpo["resourceSpans"][0]["resource"]["attributes"]}
    assert atributos["service.name"] == {"stringValue": "s"}
    assert atributos["service.namespace"] == {"stringValue": "n"}


def test_la_relacion_padre_hijo_se_conserva(span_exportado):
    cuerpo = construir_trazas(span_exportado)
    spans = cuerpo["resourceSpans"][0]["scopeSpans"][0]["spans"]
    por_nombre = {s["name"]: s for s in spans}
    assert "parentSpanId" not in por_nombre["padre"]
    assert por_nombre["hijo"]["parentSpanId"] == por_nombre["padre"]["spanId"]


def test_eventos_y_marcas_de_tiempo(span_exportado):
    cuerpo = construir_trazas(span_exportado)
    padre = next(s for s in cuerpo["resourceSpans"][0]["scopeSpans"][0]["spans"] if s["name"] == "padre")
    assert padre["events"][0]["name"] == "evento"
    # Nanosegundos como cadena: en numero JSON pierden precision.
    assert isinstance(padre["startTimeUnixNano"], str)
    assert int(padre["startTimeUnixNano"]) > 0


def test_el_cuerpo_es_json_serializable(span_exportado):
    """Si no lo es, el exportador falla en tiempo de envio y no aqui."""
    json.dumps(construir_trazas(span_exportado))


# --- Logs ------------------------------------------------------------------

def test_los_logs_llevan_su_recurso():
    """El fallo mas caro que se vio: logs con `ServiceName` vacio.

    El SDK movio el `Resource` del interior de `.log_record` al envoltorio del
    lote. Mirar solo en un sitio no da error: exporta, el Collector contesta
    200, y los logs llegan sin servicio. Un log que no se sabe de quien es no
    sirve para nada, y nada avisa de que ha pasado.
    """
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.resources import Resource

    capturado: list = []

    class Espia:
        def export(self, lote):
            capturado.extend(lote)

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis: int = 0) -> bool:
            return True

    proveedor = LoggerProvider(resource=Resource.create({"service.name": "servicio-log"}))
    proveedor.add_log_record_processor(SimpleLogRecordProcessor(Espia()))
    manejador = LoggingHandler(logger_provider=proveedor)
    registrador = logging.getLogger("prueba.otlp.json")
    registrador.addHandler(manejador)
    registrador.setLevel(logging.INFO)
    try:
        registrador.warning("hola")
    finally:
        registrador.removeHandler(manejador)

    assert capturado, "el procesador no entrego ningun registro"
    cuerpo = construir_logs(capturado)
    atributos = {a["key"]: a["value"] for a in cuerpo["resourceLogs"][0]["resource"]["attributes"]}
    assert atributos["service.name"] == {"stringValue": "servicio-log"}

    registro = cuerpo["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    assert registro["body"] == {"stringValue": "hola"}
    assert registro["severityText"] == "WARN"
    json.dumps(cuerpo)


# --- Metricas --------------------------------------------------------------

def test_histograma_y_contador():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource

    lector = InMemoryMetricReader()
    proveedor = MeterProvider(resource=Resource.create({"service.name": "m"}), metric_readers=[lector])
    medidor = proveedor.get_meter("prueba")
    medidor.create_counter("peticiones").add(3, {"ruta": "/x"})
    medidor.create_histogram("duracion").record(1.5, {"ruta": "/x"})

    cuerpo = construir_metricas(lector.get_metrics_data())
    metricas = {m["name"]: m for m in cuerpo["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]}

    assert metricas["peticiones"]["sum"]["isMonotonic"] is True
    assert metricas["peticiones"]["sum"]["dataPoints"][0]["asInt"] == "3"

    punto = metricas["duracion"]["histogram"]["dataPoints"][0]
    assert punto["count"] == "1"
    assert punto["sum"] == 1.5
    # Los contadores de cubo son uint64: cadenas, no numeros.
    assert all(isinstance(b, str) for b in punto["bucketCounts"])
    json.dumps(cuerpo)


def test_una_metrica_no_soportada_no_tumba_el_lote():
    """Preferimos perder una metrica a que el Collector rechace el cuerpo entero."""

    class Rara:
        pass

    class Metrica:
        name, description, unit, data = "rara", "", "", Rara()

    class Ambito:
        name, version, attributes = "a", None, None

    sm = type("SM", (), {"scope": Ambito(), "metrics": [Metrica()]})()
    rm = type("RM", (), {"resource": None, "scope_metrics": [sm]})()

    cuerpo = construir_metricas(type("D", (), {"resource_metrics": [rm]})())
    assert cuerpo["resourceMetrics"] == []
