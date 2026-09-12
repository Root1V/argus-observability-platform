"""Constructores de peticiones OTLP reales para los tests.

Se construyen con los mismos mensajes protobuf que usa el Collector, no con
diccionarios inventados: un test que se inventa el formato no prueba nada sobre
si el Collector podra hablar con esto.
"""

from __future__ import annotations

import pytest
from argus_semconv import attributes as A

pytest.importorskip("opentelemetry.proto")

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

STATUS_ERROR = 2


def _kv(key: str, value) -> KeyValue:
    if isinstance(value, bool):
        any_value = AnyValue(bool_value=value)
    elif isinstance(value, int):
        any_value = AnyValue(int_value=value)
    elif isinstance(value, float):
        any_value = AnyValue(double_value=value)
    else:
        any_value = AnyValue(string_value=str(value))
    return KeyValue(key=key, value=any_value)


def build_request(
    *,
    span_name: str = "ocr.extract",
    resource_attrs: dict | None = None,
    span_attrs: dict | None = None,
    status_code: int = 0,
    duration_ms: int = 100,
) -> ExportTraceServiceRequest:
    resource_attrs = resource_attrs or {
        A.SERVICE_NAMESPACE: "intelligent-document-platform",
        A.SERVICE_NAME: "idp-ocr",
        A.ARGUS_COMPONENT_ROLE: "model-server",
        A.DEPLOYMENT_ENVIRONMENT_NAME: "mac-dev",
    }
    inicio = 1_700_000_000_000_000_000
    span = Span(
        trace_id=bytes.fromhex("4bf92f3577b34da6a3ce929d0e0e4736"),
        span_id=bytes.fromhex("00f067aa0ba902b7"),
        name=span_name,
        start_time_unix_nano=inicio,
        end_time_unix_nano=inicio + duration_ms * 1_000_000,
        attributes=[_kv(k, v) for k, v in (span_attrs or {}).items()],
        status=Status(code=status_code),
    )
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=[_kv(k, v) for k, v in resource_attrs.items()]),
                scope_spans=[ScopeSpans(spans=[span])],
            )
        ]
    )
