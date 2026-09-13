"""OTLP crudo -> `Signal`.

El Collector agente ya filtro: aqui solo llega lo que puede ser un incidente.
Este modulo traduce spans a senales sin opinar sobre si merecen aviso — eso lo
decide `dedup` con el registro.

Nota sobre por que se decodifica con `opentelemetry-proto` y no a mano: OTLP es
un formato con definicion canonica. Escribir un parser propio seria
reimplementar algo que ya existe, y que ademas cambia entre versiones.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from argus_schemas import Signal, SignalKind
from argus_semconv import attributes as A

log = logging.getLogger("alert_bus.normalize")

# Codigo de estado ERROR en OTLP.
_STATUS_ERROR = 2


def _ns_to_dt(nanos: int) -> datetime | None:
    if not nanos:
        return None
    return datetime.fromtimestamp(nanos / 1e9, tz=UTC)


def _anyvalue(value: Any) -> Any:
    """Desempaqueta un `AnyValue` de OTLP a un tipo de Python."""
    for field in ("string_value", "bool_value", "int_value", "double_value"):
        if value.HasField(field):
            return getattr(value, field)
    if value.HasField("array_value"):
        return [_anyvalue(v) for v in value.array_value.values]
    if value.HasField("kvlist_value"):
        return {kv.key: _anyvalue(kv.value) for kv in value.kvlist_value.values}
    return None


def _attrs(pairs: Any) -> dict[str, Any]:
    return {kv.key: _anyvalue(kv.value) for kv in pairs}


def decode_protobuf(body: bytes) -> Any:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    return request


def decode_json(body: bytes) -> Any:
    from google.protobuf.json_format import Parse
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    return Parse(body.decode("utf-8"), ExportTraceServiceRequest())


def signals_from_otlp(request: Any) -> Iterator[Signal]:
    """Recorre un `ExportTraceServiceRequest` y emite las senales que contenga.

    Un span puede no producir ninguna: llegan lotes enteros de los que solo
    algunos spans son candidatos.
    """
    for resource_spans in request.resource_spans:
        resource = _attrs(resource_spans.resource.attributes)
        app = str(resource.get(A.SERVICE_NAMESPACE) or "unregistered")
        component = str(resource.get(A.SERVICE_NAME) or "unknown")
        role = resource.get(A.ARGUS_COMPONENT_ROLE)
        environment = resource.get(A.DEPLOYMENT_ENVIRONMENT_NAME)

        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                signal = _signal_from_span(span, app, component, role, environment)
                if signal is not None:
                    yield signal


def _signal_from_span(
    span: Any,
    app: str,
    component: str,
    role: Any,
    environment: Any,
) -> Signal | None:
    attrs = _attrs(span.attributes)

    kind, signature, title = _classify(span, attrs, component)
    if kind is None:
        return None

    duration_ms = None
    if span.end_time_unix_nano and span.start_time_unix_nano:
        duration_ms = int((span.end_time_unix_nano - span.start_time_unix_nano) / 1e6)

    return Signal(
        kind=kind,
        occurred_at=_ns_to_dt(span.end_time_unix_nano or span.start_time_unix_nano),
        app=app,
        component=component,
        role=str(role) if role else None,
        environment=str(environment) if environment else None,
        signature=signature,
        title=title,
        trace_id=span.trace_id.hex() or None,
        span_id=span.span_id.hex() or None,
        duration_ms=duration_ms,
        slo_threshold_ms=attrs.get(A.ARGUS_SLO_THRESHOLD_MS),
        # Solo los atributos que el informe va a usar. Copiar el span entero
        # llenaria el incidente de ruido y la ventana de contexto del agente.
        attributes={
            k: v
            for k, v in attrs.items()
            if k
            in {
                A.ERROR_TYPE,
                A.ARGUS_ERROR_RETRYABLE,
                A.ARGUS_ERROR_RETRY_POLICY,
                A.ARGUS_EVENT,
                A.ARGUS_GUARDRAIL,
                A.GEN_AI_REQUEST_MODEL,
                A.GEN_AI_OPERATION_NAME,
                "http.route",
                "http.response.status_code",
            }
        },
    )


def _classify(span: Any, attrs: dict[str, Any], component: str) -> tuple[SignalKind | None, str, str]:
    """Decide que clase de senal es, si es alguna.

    El orden importa: un guardarrail roto es mas especifico que un error
    generico, y un error es mas especifico que un SLO superado. Se clasifica por
    lo mas concreto que se sepa.
    """
    # 1. Guardarrail: presupuesto de coste, bucle de agente. Lo mas especifico.
    if guardrail := attrs.get(A.ARGUS_GUARDRAIL):
        return (
            SignalKind.GUARDRAIL,
            str(guardrail),
            f"Guardarrail «{guardrail}» incumplido en {component}",
        )

    # 2. Error. `error.type` es de cardinalidad cerrada, asi que sirve de firma.
    is_error = span.status.code == _STATUS_ERROR
    error_type = attrs.get(A.ERROR_TYPE)
    if is_error or error_type:
        signature = str(error_type or span.status.message or "error")
        return (
            SignalKind.ERROR,
            signature,
            f"{signature} en {component}",
        )

    # 3. SLO superado. Lo marca el SDK al cerrar el paso.
    if attrs.get(A.ARGUS_SLO_BREACHED):
        signature = str(attrs.get(A.ARGUS_EVENT) or span.name)
        return (
            SignalKind.SLO_BREACH,
            signature,
            f"«{signature}» supero su objetivo de latencia en {component}",
        )

    # 4. Marcado como caliente sin mas contexto. Llega porque el filtro del
    #    Collector es deliberadamente generoso: mejor que sobre aqui, donde
    #    deduplicamos, que no que falte alla, donde no hay vuelta atras.
    if attrs.get(A.ARGUS_HOT):
        signature = str(attrs.get(A.ARGUS_EVENT) or span.name)
        return (SignalKind.ERROR, signature, f"{signature} en {component}")

    return (None, "", "")


def signals_from_alertmanager(payload: dict[str, Any] | list[dict[str, Any]]) -> Iterator[Signal]:
    """Alertas de vmalert en formato Alertmanager -> senales.

    Es el camino TEMPLADO: burn-rate y bandas de anomalia, que necesitan una
    ventana para tener sentido y por tanto nunca podrian venir por el caliente.

    Acepta DOS formas, y la distincion costo un fallo en produccion:

      - Un ARRAY pelado `[{...}, {...}]`. Es lo que manda vmalert, porque es el
        formato de la API v2 de Alertmanager.
      - Un objeto `{"alerts": [...]}`. Es el formato de WEBHOOK de Alertmanager,
        comodo para scripts y para el canario.

    Asumir solo el segundo dejo la integracion real rota mientras los tests
    pasaban, porque los tests hablaban nuestro formato en vez del suyo.
    """
    alertas = payload if isinstance(payload, list) else payload.get("alerts", [])

    for alert in alertas:
        labels = alert.get("labels", {}) or {}
        annotations = alert.get("annotations", {}) or {}
        name = labels.get("alertname", "alerta")

        kind = SignalKind.ANOMALY if "anomal" in name.lower() else SignalKind.BURN_RATE

        yield Signal(
            kind=kind,
            app=labels.get("service_namespace") or labels.get("app") or "unregistered",
            component=labels.get("service_name") or labels.get("component") or "unknown",
            environment=labels.get("deployment_environment_name"),
            signature=name,
            title=annotations.get("summary") or name,
            attributes={"labels": labels, "annotations": annotations},
        )


def decompress(body: bytes, content_encoding: str | None) -> bytes:
    """Descomprime el cuerpo si viene comprimido.

    El Collector comprime con gzip POR DEFECTO y FastAPI no descomprime los
    cuerpos de peticion. Sin esto, el sintoma es "Wire format was corrupt", que
    apunta a un protobuf malo cuando en realidad es un gzip sin abrir.

    Se detecta tambien por los magic bytes: un cliente puede comprimir y no
    declararlo.
    """
    encoding = (content_encoding or "").strip().lower()

    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    if encoding in ("deflate", "zlib"):
        return zlib.decompress(body)
    return body


def parse_body(body: bytes, content_type: str, content_encoding: str | None = None) -> Any:
    """Decodifica un cuerpo OTLP, sea protobuf o JSON, comprimido o no."""
    body = decompress(body, content_encoding)
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized == "application/json":
        return decode_json(body)
    if normalized in ("application/x-protobuf", "application/protobuf", ""):
        return decode_protobuf(body)
    raise ValueError(f"Content-Type no soportado para OTLP: {content_type!r}")


def looks_like_json(body: bytes) -> bool:
    """Algunos clientes no ponen Content-Type. Es barato comprobarlo."""
    stripped = body.lstrip()
    if not stripped.startswith(b"{"):
        return False
    try:
        json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    return True
