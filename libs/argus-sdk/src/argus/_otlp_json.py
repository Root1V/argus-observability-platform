"""Exportadores OTLP/HTTP con codificacion JSON, sin una sola dependencia.

Por que existe este modulo
--------------------------
Los exportadores oficiales de OTLP en Python —gRPC y HTTP por igual— dependen
de `opentelemetry-proto`, que exige `protobuf>=5.0`. Ese rango es incompatible
con cualquier aplicacion que arrastre `protobuf<3.20`, y eso ocurre de verdad:
media pila de audio y ML sigue anclada ahi por `descript-audiotools`. Un equipo
consumidor no pudo declarar el SDK por esto (D-077).

Cambiar de gRPC a HTTP no salva: el limite viene de `opentelemetry-proto`, no
del transporte. La unica salida es no usar protobuf.

OTLP define la codificacion JSON sobre HTTP como transporte de primera clase,
asi que la salida es escribir el serializador. Son unos cientos de lineas de
`json` y `urllib` de la biblioteca estandar, y a cambio el nucleo del SDK deja
de tener una sola dependencia con restricciones de version conflictivas.

Detalles de la codificacion que NO son obvios
---------------------------------------------
- `trace_id` y `span_id` van en **hexadecimal**, no en base64. Es la unica
  desviacion deliberada de OTLP respecto al mapeo JSON canonico de protobuf, y
  saltarsela produce un 400 del Collector que no dice por que.
- Los enteros de 64 bits (`timeUnixNano`, `bucketCounts`) van como **cadenas**.
  Es el mapeo JSON de protobuf: un `uint64` no cabe en un numero de JSON sin
  perder precision, y los nanosegundos de una marca de tiempo estan justo en
  ese rango.
- Los campos vacios se omiten. Protobuf no distingue ausente de valor cero, y
  emitirlos infla el cuerpo sin aportar nada.
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

_log = logging.getLogger("argus.otlp")

# Codigos en los que reintentar. El resto son errores del cliente: reintentar
# un 400 es repetir el mismo cuerpo mal formado hasta agotar la paciencia.
_REINTENTABLES = frozenset({408, 429, 500, 502, 503, 504})


# --------------------------------------------------------------------------
# Valores y atributos
# --------------------------------------------------------------------------

def _valor(v: Any) -> dict[str, Any]:
    """Codifica un valor de atributo como `AnyValue` de OTLP."""
    # bool antes que int: en Python `bool` ES `int`, y comprobarlo al reves
    # convierte True en 1 silenciosamente.
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (bytes, bytearray)):
        import base64

        return {"bytesValue": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_valor(x) for x in v]}}
    if isinstance(v, dict):
        return {"kvlistValue": {"values": _attrs(v)}}
    # Nunca dejamos caer un atributo por no saber codificarlo: un atributo
    # como texto es mas util que un hueco.
    return {"stringValue": str(v)}


def _attrs(d: Any) -> list[dict[str, Any]]:
    if not d:
        return []
    return [{"key": str(k), "value": _valor(v)} for k, v in d.items()]


def _recurso(resource: Any) -> dict[str, Any]:
    if resource is None:
        return {}
    salida: dict[str, Any] = {"attributes": _attrs(getattr(resource, "attributes", {}))}
    return salida


def _ambito(scope: Any) -> dict[str, Any]:
    if scope is None:
        return {}
    salida: dict[str, Any] = {"name": getattr(scope, "name", "") or ""}
    version = getattr(scope, "version", None)
    if version:
        salida["version"] = version
    atributos = _attrs(getattr(scope, "attributes", None))
    if atributos:
        salida["attributes"] = atributos
    return salida


def _hex(valor: int | None, ancho: int) -> str:
    return format(valor or 0, f"0{ancho}x")


# --------------------------------------------------------------------------
# Transporte
# --------------------------------------------------------------------------

class _Emisor:
    """POST con gzip y reintentos con retroceso exponencial.

    Respeta `Retry-After` porque un Collector saturado que pide 30 segundos
    sabe mejor que nosotros cuando volver.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
        max_reintentos: int = 3,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.max_reintentos = max_reintentos
        self.headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            **(headers or {}),
        }
        self._cerrado = False

    def enviar(self, cuerpo: dict[str, Any]) -> bool:
        if self._cerrado:
            return False

        datos = gzip.compress(json.dumps(cuerpo, separators=(",", ":")).encode("utf-8"))

        espera = 1.0
        for intento in range(self.max_reintentos + 1):
            try:
                peticion = urllib.request.Request(
                    self.url, data=datos, headers=self.headers, method="POST"
                )
                with urllib.request.urlopen(peticion, timeout=self.timeout_s) as resp:
                    if 200 <= resp.status < 300:
                        return True
                    _log.debug("otlp.json respuesta inesperada status=%s", resp.status)
                    return False
            except urllib.error.HTTPError as exc:
                if exc.code not in _REINTENTABLES or intento == self.max_reintentos:
                    _log.debug("otlp.json rechazado status=%s", exc.code)
                    return False
                espera = self._espera(exc, espera)
            except Exception as exc:  # noqa: BLE001 - regla 1: nunca tumbar la app
                if intento == self.max_reintentos:
                    _log.debug("otlp.json fallo de red: %s", exc)
                    return False
            # Jitter: sin el, N procesos que pierden el Collector a la vez
            # vuelven todos en el mismo instante y lo tumban otra vez.
            time.sleep(espera * (0.5 + random.random()))
            espera = min(espera * 2, 30.0)
        return False

    @staticmethod
    def _espera(exc: urllib.error.HTTPError, por_defecto: float) -> float:
        cabecera = exc.headers.get("Retry-After") if exc.headers else None
        if cabecera:
            try:
                return min(float(cabecera), 60.0)
            except ValueError:
                pass
        return por_defecto

    def cerrar(self) -> None:
        self._cerrado = True


def _url(endpoint: str, ruta: str) -> str:
    """Compone la URL de la senal, sin duplicar la ruta si ya viene puesta."""
    base = endpoint.rstrip("/")
    if base.endswith(ruta):
        return base
    return f"{base}{ruta}"


def _agrupar(elementos: Sequence[Any], clave_recurso: Any, clave_ambito: Any) -> list[tuple[Any, dict[Any, list[Any]]]]:
    """Agrupa por (recurso, ambito) conservando el orden de aparicion.

    OTLP exige la jerarquia resource -> scope -> senal. Emitir un bloque por
    elemento seria valido pero multiplica el tamano del cuerpo por diez.
    """
    por_recurso: dict[int, tuple[Any, dict[Any, list[Any]]]] = {}
    for elemento in elementos:
        recurso = clave_recurso(elemento)
        ambito = clave_ambito(elemento)
        entrada = por_recurso.setdefault(id(recurso), (recurso, {}))
        entrada[1].setdefault(ambito, []).append(elemento)
    return list(por_recurso.values())


# --------------------------------------------------------------------------
# Trazas
# --------------------------------------------------------------------------

def _span(span: Any) -> dict[str, Any]:
    ctx = span.get_span_context()
    salida: dict[str, Any] = {
        "traceId": _hex(ctx.trace_id, 32),
        "spanId": _hex(ctx.span_id, 16),
        "name": span.name,
        "kind": int(getattr(span.kind, "value", 0)) + 1,  # OTLP numera desde 1
        "startTimeUnixNano": str(span.start_time or 0),
        "endTimeUnixNano": str(span.end_time or 0),
        "flags": int(getattr(ctx.trace_flags, "__int__", lambda: int(ctx.trace_flags))()),
    }

    padre = getattr(span, "parent", None)
    if padre is not None and padre.span_id:
        salida["parentSpanId"] = _hex(padre.span_id, 16)

    estado_traza = getattr(ctx, "trace_state", None)
    if estado_traza:
        texto = str(estado_traza)
        if texto:
            salida["traceState"] = texto

    atributos = _attrs(span.attributes)
    if atributos:
        salida["attributes"] = atributos
    if getattr(span, "dropped_attributes", 0):
        salida["droppedAttributesCount"] = span.dropped_attributes

    eventos = [
        {
            "timeUnixNano": str(e.timestamp or 0),
            "name": e.name,
            **({"attributes": _attrs(e.attributes)} if e.attributes else {}),
        }
        for e in (span.events or ())
    ]
    if eventos:
        salida["events"] = eventos

    enlaces = []
    for enlace in span.links or ():
        lctx = enlace.context
        entrada: dict[str, Any] = {"traceId": _hex(lctx.trace_id, 32), "spanId": _hex(lctx.span_id, 16)}
        if enlace.attributes:
            entrada["attributes"] = _attrs(enlace.attributes)
        enlaces.append(entrada)
    if enlaces:
        salida["links"] = enlaces

    estado = span.status
    if estado is not None:
        codigo = int(getattr(estado.status_code, "value", 0))
        if codigo:
            salida["status"] = {"code": codigo}
            if estado.description:
                salida["status"]["message"] = estado.description

    return salida


def construir_trazas(spans: Sequence[Any]) -> dict[str, Any]:
    """Serializa spans del SDK al cuerpo `ExportTraceServiceRequest`."""
    grupos = _agrupar(
        spans,
        lambda s: s.resource,
        lambda s: s.instrumentation_scope,
    )
    return {
        "resourceSpans": [
            {
                "resource": _recurso(recurso),
                "scopeSpans": [
                    {"scope": _ambito(ambito), "spans": [_span(s) for s in lista]}
                    for ambito, lista in ambitos.items()
                ],
            }
            for recurso, ambitos in grupos
        ]
    }


# --------------------------------------------------------------------------
# Metricas
# --------------------------------------------------------------------------

def _punto_numero(p: Any) -> dict[str, Any]:
    salida: dict[str, Any] = {
        "startTimeUnixNano": str(getattr(p, "start_time_unix_nano", 0) or 0),
        "timeUnixNano": str(getattr(p, "time_unix_nano", 0) or 0),
    }
    atributos = _attrs(getattr(p, "attributes", None))
    if atributos:
        salida["attributes"] = atributos
    valor = getattr(p, "value", 0)
    if isinstance(valor, int) and not isinstance(valor, bool):
        salida["asInt"] = str(valor)
    else:
        salida["asDouble"] = float(valor)
    return salida


def _punto_histograma(p: Any) -> dict[str, Any]:
    salida: dict[str, Any] = {
        "startTimeUnixNano": str(getattr(p, "start_time_unix_nano", 0) or 0),
        "timeUnixNano": str(getattr(p, "time_unix_nano", 0) or 0),
        "count": str(getattr(p, "count", 0) or 0),
        "bucketCounts": [str(b) for b in (getattr(p, "bucket_counts", None) or ())],
        "explicitBounds": [float(b) for b in (getattr(p, "explicit_bounds", None) or ())],
    }
    atributos = _attrs(getattr(p, "attributes", None))
    if atributos:
        salida["attributes"] = atributos
    suma = getattr(p, "sum", None)
    if suma is not None:
        salida["sum"] = float(suma)
    # min/max solo si existen: en un histograma vacio el SDK los deja en None y
    # emitir `null` hace que el Collector descarte el punto entero.
    for nombre, clave in (("min", "min"), ("max", "max")):
        v = getattr(p, nombre, None)
        if v is not None:
            salida[clave] = float(v)
    return salida


def _metrica(m: Any) -> dict[str, Any] | None:
    salida: dict[str, Any] = {"name": m.name}
    if getattr(m, "description", None):
        salida["description"] = m.description
    if getattr(m, "unit", None):
        salida["unit"] = m.unit

    datos = m.data
    puntos = list(getattr(datos, "data_points", ()) or ())
    tipo = type(datos).__name__

    if tipo == "Sum":
        salida["sum"] = {
            "dataPoints": [_punto_numero(p) for p in puntos],
            "aggregationTemporality": int(getattr(datos.aggregation_temporality, "value", 2)),
            "isMonotonic": bool(getattr(datos, "is_monotonic", False)),
        }
    elif tipo == "Gauge":
        salida["gauge"] = {"dataPoints": [_punto_numero(p) for p in puntos]}
    elif tipo == "Histogram":
        salida["histogram"] = {
            "dataPoints": [_punto_histograma(p) for p in puntos],
            "aggregationTemporality": int(getattr(datos.aggregation_temporality, "value", 2)),
        }
    else:
        # Histograma exponencial y lo que venga en el futuro. Preferimos
        # descartar la metrica a enviar un cuerpo que el Collector rechace
        # entero, llevandose por delante las metricas que si sabemos codificar.
        _log.debug("otlp.json tipo de metrica no soportado: %s", tipo)
        return None

    return salida


def construir_metricas(datos: Any) -> dict[str, Any]:
    """Serializa `MetricsData` del SDK al cuerpo `ExportMetricsServiceRequest`."""
    recursos = []
    for rm in getattr(datos, "resource_metrics", ()) or ():
        ambitos = []
        for sm in getattr(rm, "scope_metrics", ()) or ():
            metricas = [x for x in (_metrica(m) for m in sm.metrics) if x is not None]
            if metricas:
                ambitos.append({"scope": _ambito(sm.scope), "metrics": metricas})
        if ambitos:
            recursos.append({"resource": _recurso(rm.resource), "scopeMetrics": ambitos})
    return {"resourceMetrics": recursos}


# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------

def _registro(dato: Any) -> dict[str, Any]:
    r = getattr(dato, "log_record", None) or dato
    salida: dict[str, Any] = {
        "timeUnixNano": str(getattr(r, "timestamp", 0) or 0),
        "observedTimeUnixNano": str(getattr(r, "observed_timestamp", 0) or 0),
    }
    if getattr(r, "severity_number", None) is not None:
        salida["severityNumber"] = int(getattr(r.severity_number, "value", r.severity_number))
    if getattr(r, "severity_text", None):
        salida["severityText"] = r.severity_text
    if getattr(r, "body", None) is not None:
        salida["body"] = _valor(r.body)
    atributos = _attrs(getattr(r, "attributes", None))
    if atributos:
        salida["attributes"] = atributos
    if getattr(r, "trace_id", None):
        salida["traceId"] = _hex(r.trace_id, 32)
    if getattr(r, "span_id", None):
        salida["spanId"] = _hex(r.span_id, 16)
    banderas = getattr(r, "trace_flags", None)
    if banderas is not None:
        salida["flags"] = int(banderas)
    return salida


def _recurso_de_log(dato: Any) -> Any:
    """Localiza el `Resource` de un registro de log.

    El SDK cambio la forma del lote: hasta 1.3x era un `LogData` que llevaba el
    recurso dentro de `.log_record`, y desde `ReadableLogRecord` lo lleva en el
    envoltorio. Mirar solo uno de los dos sitios no falla en alto: exporta los
    logs sin recurso, y llegan con `ServiceName` vacio, que es peor que un
    error porque parece que funciona.
    """
    recurso = getattr(dato, "resource", None)
    if recurso is not None:
        return recurso
    return getattr(getattr(dato, "log_record", None), "resource", None)


def construir_logs(lote: Sequence[Any]) -> dict[str, Any]:
    """Serializa el lote de logs del SDK al cuerpo `ExportLogsServiceRequest`."""
    grupos = _agrupar(
        lote,
        _recurso_de_log,
        lambda d: d.instrumentation_scope,
    )
    return {
        "resourceLogs": [
            {
                "resource": _recurso(recurso),
                "scopeLogs": [
                    {"scope": _ambito(ambito), "logRecords": [_registro(d) for d in lista]}
                    for ambito, lista in ambitos.items()
                ],
            }
            for recurso, ambitos in grupos
        ]
    }


# --------------------------------------------------------------------------
# Exportadores
# --------------------------------------------------------------------------
# Implementan las interfaces del SDK de OpenTelemetry, asi que se enchufan en
# `BatchSpanProcessor`, `PeriodicExportingMetricReader` y
# `BatchLogRecordProcessor` sin que nada mas del SDK sepa que son nuestros.

from opentelemetry.sdk.metrics.export import (  # noqa: E402
    AggregationTemporality,
    MetricExporter,
    MetricExportResult,
)
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult  # noqa: E402


class ExportadorTrazasJSON(SpanExporter):
    """Exportador de trazas OTLP/HTTP+JSON. Sin protobuf, sin grpcio."""

    def __init__(
        self,
        endpoint: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._emisor = _Emisor(_url(endpoint, "/v1/traces"), headers, timeout_s)

    def export(self, spans: Sequence[Any]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS
        try:
            ok = self._emisor.enviar(construir_trazas(spans))
        except Exception as exc:  # noqa: BLE001 - regla 1
            _log.debug("otlp.json fallo serializando trazas: %s", exc)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS if ok else SpanExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        # El envio es sincrono dentro de `export`: cuando esto se llama no hay
        # nada pendiente por nuestra parte.
        return True

    def shutdown(self) -> None:
        self._emisor.cerrar()


class ExportadorMetricasJSON(MetricExporter):
    """Exportador de metricas OTLP/HTTP+JSON."""

    def __init__(
        self,
        endpoint: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
        preferred_temporality: dict[type, AggregationTemporality] | None = None,
        preferred_aggregation: dict[type, Any] | None = None,
    ) -> None:
        super().__init__(
            preferred_temporality=preferred_temporality or {},
            preferred_aggregation=preferred_aggregation or {},
        )
        self._emisor = _Emisor(_url(endpoint, "/v1/metrics"), headers, timeout_s)

    def export(
        self, metrics_data: Any, timeout_millis: float = 10_000, **kwargs: Any
    ) -> MetricExportResult:
        try:
            cuerpo = construir_metricas(metrics_data)
            if not cuerpo["resourceMetrics"]:
                return MetricExportResult.SUCCESS
            ok = self._emisor.enviar(cuerpo)
        except Exception as exc:  # noqa: BLE001 - regla 1
            _log.debug("otlp.json fallo serializando metricas: %s", exc)
            return MetricExportResult.FAILURE
        return MetricExportResult.SUCCESS if ok else MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        self._emisor.cerrar()


def _clase_exportador_logs() -> Any:
    """`LogExporter` vive en un modulo privado que ha cambiado de sitio.

    Se importa tarde y con alternativa para que un cambio de ruta en el SDK
    deje sin logs, no sin telemetria.
    """
    try:
        from opentelemetry.sdk._logs.export import LogExporter, LogExportResult
    except ImportError:  # pragma: no cover - rutas antiguas del SDK
        from opentelemetry.sdk._logs import (  # type: ignore[attr-defined, no-redef]
            LogExporter,
            LogExportResult,
        )
    return LogExporter, LogExportResult


def crear_exportador_logs_json(
    endpoint: str, headers: dict[str, str] | None = None, timeout_s: float = 10.0
) -> Any:
    """Construye el exportador de logs JSON.

    Es una fabrica y no una clase de modulo porque su clase base solo se puede
    importar en tiempo de ejecucion (ver arriba).
    """
    LogExporter, LogExportResult = _clase_exportador_logs()

    class ExportadorLogsJSON(LogExporter):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self._emisor = _Emisor(_url(endpoint, "/v1/logs"), headers, timeout_s)

        def export(self, batch: Sequence[Any]) -> Any:
            if not batch:
                return LogExportResult.SUCCESS
            try:
                ok = self._emisor.enviar(construir_logs(batch))
            except Exception as exc:  # noqa: BLE001 - regla 1
                _log.debug("otlp.json fallo serializando logs: %s", exc)
                return LogExportResult.FAILURE
            return LogExportResult.SUCCESS if ok else LogExportResult.FAILURE

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self) -> None:
            self._emisor.cerrar()

    return ExportadorLogsJSON()
