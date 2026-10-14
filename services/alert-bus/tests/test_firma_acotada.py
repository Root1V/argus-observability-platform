"""La firma de un error tiene que ser de cardinalidad ACOTADA.

El 14/10 Veritium emitio 460 errores de Postgres en 22 minutos, todos el mismo:
la columna `tenant` a null en `webhook_deliveries`. **Sin `error.type`**, porque
su codigo no lo pone.

La firma era `error_type or span.status.message`, asi que fue el mensaje entero
— incluida la linea `DETAIL:` que Postgres adjunta con los UUID de la fila que
fallo y su timestamp:

    DETAIL:  Failing row contains (b929521c-…, 77e22778-…, …, 2026-10-14 11:18:11.307931+00, …).

**335 firmas distintas para UN error.** 335 incidentes, 335 notificaciones, y el
canal humano saturado hasta que el dueño de la plataforma pidio apagarlo.

El componente cuyo unico trabajo es deduplicar, multiplicando. Y el comentario
de al lado decia, con razon, que `error.type` «es de cardinalidad cerrada, asi
que sirve de firma» — el razonamiento correcto pegado al respaldo que hacia lo
contrario (D-118).

Es la tercera vez esta semana que algo se agrupa por un identificador sin
querer: el cursor del long-poll de Prometheus (D-115), la plantilla de ruta con
el UUID dentro (D-117), y esto.
"""

from __future__ import annotations

import uuid

import pytest
from alert_bus.normalize import signals_from_otlp

pytest.importorskip("opentelemetry.proto")

from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status
from otlp_factory import build_request

STATUS_ERROR = 2
VERITIUM = {
    "service.namespace": "veritium",
    "service.name": "veritium-api",
    "argus.component.role": "api",
    "deployment.environment.name": "development",
}


def _mensaje_de_postgres() -> str:
    """El mensaje real, con los UUID y el timestamp que cambian cada vez."""
    return (
        'NotNullViolationError: null value in column "tenant" of relation '
        '"webhook_deliveries" violates not-null constraint\n'
        f"DETAIL:  Failing row contains ({uuid.uuid4()}, {uuid.uuid4()}, "
        f"{uuid.uuid4()}, pending, 0, 2026-10-14 11:18:11.{uuid.uuid4().int % 999999}+00, "
        "null, null, null, null, 2026-10-14 11:18:11.307931+00, null)."
    )


def _peticion_con_excepcion(mensaje: str):
    """Un span de error SIN `error.type`, con el evento de excepcion.

    Es la forma exacta que emitia Veritium: `error.type` en 2 de 460, y el
    evento de excepcion en 460 de 460.
    """
    req = build_request(span_name="POST /v1/cases/{case_id}/reprocess", resource_attrs=VERITIUM)
    span = req.resource_spans[0].scope_spans[0].spans[0]
    span.status.CopyFrom(Status(code=STATUS_ERROR, message=mensaje))
    del span.attributes[:]
    evento = Span.Event(name="exception")
    evento.attributes.append(
        KeyValue(key="exception.type", value=AnyValue(string_value="NotNullViolationError"))
    )
    evento.attributes.append(
        KeyValue(key="exception.message", value=AnyValue(string_value=mensaje))
    )
    span.events.append(evento)
    return req


def test_cuatrocientos_errores_iguales_dan_UNA_firma() -> None:
    """La propiedad, en la forma en que costo."""
    firmas = set()
    for _ in range(400):
        for s in signals_from_otlp(_peticion_con_excepcion(_mensaje_de_postgres())):
            firmas.add(s.signature)

    assert firmas == {"NotNullViolationError"}, (
        f"400 ocurrencias del mismo error dieron {len(firmas)} firmas distintas. "
        f"Cada firma es un incidente y una notificacion."
    )


def test_la_firma_no_contiene_el_DETAIL_de_postgres() -> None:
    mensaje = _mensaje_de_postgres()
    s = next(iter(signals_from_otlp(_peticion_con_excepcion(mensaje))))
    assert "DETAIL" not in s.signature
    assert "Failing row" not in s.signature
    assert "null value in column" not in s.signature


def test_pero_el_mensaje_SI_llega_en_el_titulo() -> None:
    """Acotar la firma no puede costar la informacion.

    El mensaje es lo que una persona necesita leer UNA vez: sin el, el
    incidente dice `NotNullViolationError en veritium-api` y nadie sabe de que
    columna habla. Va en el titulo, que no agrupa.
    """
    s = next(iter(signals_from_otlp(_peticion_con_excepcion(_mensaje_de_postgres()))))
    assert "NotNullViolationError" in s.title
    assert "tenant" in s.title, f"el titulo no dice de que columna habla: {s.title!r}"
    # Una sola linea y acotado: un `DETAIL:` de Postgres son parrafos.
    assert "\n" not in s.title
    assert "Failing row" not in s.title
    assert len(s.title) < 260, f"titulo de {len(s.title)} caracteres"


def test_error_type_sigue_ganando_cuando_esta() -> None:
    """La jerarquia no cambia: lo que el emisor declara va primero."""
    req = _peticion_con_excepcion(_mensaje_de_postgres())
    span = req.resource_spans[0].scope_spans[0].spans[0]
    span.attributes.append(
        KeyValue(key="error.type", value=AnyValue(string_value="http-500"))
    )
    s = next(iter(signals_from_otlp(req)))
    assert s.signature == "http-500"


def test_sin_tipo_ni_excepcion_cae_a_la_operacion_y_no_al_mensaje() -> None:
    """Ultimo respaldo: el nombre del span, acotado por convencion desde D-117.

    Lo que NO puede pasar es caer al mensaje, que es de donde venimos.
    """
    mensaje = _mensaje_de_postgres()
    req = build_request(span_name="POST /v1/cases/{case_id}/reprocess", resource_attrs=VERITIUM)
    span = req.resource_spans[0].scope_spans[0].spans[0]
    span.status.CopyFrom(Status(code=STATUS_ERROR, message=mensaje))
    del span.attributes[:]

    s = next(iter(signals_from_otlp(req)))
    assert s.signature == "POST /v1/cases/{case_id}/reprocess"
    assert "DETAIL" not in s.signature
