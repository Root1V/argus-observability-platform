"""El receptor OTLP: decodificacion y clasificacion de spans.

Construye peticiones OTLP reales con los mismos mensajes protobuf que usa el
Collector, no diccionarios inventados. Un test que se inventa el formato no
prueba nada sobre si el Collector podra hablar con esto.
"""

from __future__ import annotations

import pytest
from alert_bus import normalize
from argus_schemas import Severity, Signal, SignalKind
from argus_semconv import attributes as A
from otlp_factory import STATUS_ERROR, build_request

# --- Decodificacion ----------------------------------------------------------


def test_decodifica_protobuf() -> None:
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    decodificada = normalize.decode_protobuf(peticion.SerializeToString())
    (senal,) = normalize.signals_from_otlp(decodificada)
    assert senal.signature == "timeout"


def test_decodifica_json() -> None:
    """El Collector puede mandar JSON; hay que aceptar los dos formatos."""
    from google.protobuf.json_format import MessageToJson

    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    decodificada = normalize.decode_json(MessageToJson(peticion).encode())
    (senal,) = normalize.signals_from_otlp(decodificada)
    assert senal.signature == "timeout"


def test_un_cuerpo_invalido_lanza_en_vez_de_corromper() -> None:
    """Mejor un fallo ruidoso que una senal inventada a partir de basura."""
    from google.protobuf.message import DecodeError

    with pytest.raises(DecodeError):
        normalize.decode_protobuf(b"esto no es protobuf en absoluto")


# --- Clasificacion -----------------------------------------------------------


def test_extrae_la_identidad_de_dos_niveles() -> None:
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    (senal,) = normalize.signals_from_otlp(peticion)

    assert senal.app == "intelligent-document-platform"
    assert senal.component == "idp-ocr"
    assert senal.role == "model-server"
    assert senal.environment == "mac-dev"


def test_un_span_normal_no_produce_senal() -> None:
    """El filtro del Collector es generoso: aqui se descarta lo que sobra."""
    peticion = build_request(span_attrs={"http.route": "/ok"})
    assert list(normalize.signals_from_otlp(peticion)) == []


def test_un_status_error_produce_senal_aunque_no_haya_error_type() -> None:
    peticion = build_request(status_code=STATUS_ERROR)
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.kind is SignalKind.ERROR


def test_un_guardarrail_gana_al_error_generico() -> None:
    """Se clasifica por lo mas concreto que se sepa."""
    peticion = build_request(
        status_code=STATUS_ERROR,
        span_attrs={A.ERROR_TYPE: "timeout", A.ARGUS_GUARDRAIL: "tool-call-loop"},
    )
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.kind is SignalKind.GUARDRAIL
    assert senal.signature == "tool-call-loop"


def test_un_slo_superado_se_clasifica_como_tal() -> None:
    peticion = build_request(
        span_attrs={
            A.ARGUS_SLO_BREACHED: True,
            A.ARGUS_SLO_THRESHOLD_MS: 30_000,
            A.ARGUS_EVENT: "ocr.extract",
        },
        duration_ms=45_000,
    )
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.kind is SignalKind.SLO_BREACH
    assert senal.duration_ms == 45_000
    assert senal.slo_threshold_ms == 30_000


def test_argus_hot_sin_mas_contexto_produce_senal() -> None:
    """El filtro del Collector marca generosamente; aqui se recoge igual."""
    peticion = build_request(span_attrs={A.ARGUS_HOT: True, A.ARGUS_EVENT: "algo.raro"})
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.kind is SignalKind.ERROR


def test_conserva_el_trace_id_para_saltar_a_la_traza() -> None:
    """Es lo que conecta el aviso con la traza completa del camino frio."""
    peticion = build_request(status_code=STATUS_ERROR)
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_solo_copia_los_atributos_que_el_informe_usa() -> None:
    """Copiar el span entero llenaria de ruido la ventana del agente."""
    peticion = build_request(
        status_code=STATUS_ERROR,
        span_attrs={
            A.ERROR_TYPE: "timeout",
            A.ARGUS_ERROR_RETRYABLE: True,
            "atributo.irrelevante": "x" * 500,
        },
    )
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.attributes[A.ERROR_TYPE] == "timeout"
    assert senal.attributes[A.ARGUS_ERROR_RETRYABLE] is True
    assert "atributo.irrelevante" not in senal.attributes


def test_un_servicio_sin_namespace_cae_en_unregistered() -> None:
    peticion = build_request(
        resource_attrs={A.SERVICE_NAME: "algo-suelto"},
        status_code=STATUS_ERROR,
    )
    (senal,) = normalize.signals_from_otlp(peticion)
    assert senal.app == "unregistered"
    assert senal.component == "algo-suelto"


# --- Camino templado ---------------------------------------------------------


def test_traduce_alertas_de_vmalert() -> None:
    payload = {
        "alerts": [
            {
                "labels": {
                    "alertname": "ArgusSLOBurnRateFast",
                    "service_namespace": "intelligent-document-platform",
                    "service_name": "idp-api",
                    "severity": "page",
                },
                "annotations": {"summary": "quema rapida del presupuesto de error"},
            }
        ]
    }
    (senal,) = normalize.signals_from_alertmanager(payload)
    assert senal.kind is SignalKind.BURN_RATE
    assert senal.app == "intelligent-document-platform"
    assert senal.title == "quema rapida del presupuesto de error"


# --- Compresion --------------------------------------------------------------
# Regresion encontrada al conectar la tuberia caliente real. El Collector
# comprime con gzip POR DEFECTO y FastAPI no descomprime los cuerpos de
# peticion. El sintoma era "Wire format was corrupt", que apunta a un protobuf
# malo cuando en realidad era un gzip sin abrir.


def test_descomprime_gzip_declarado() -> None:
    import gzip

    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    comprimido = gzip.compress(peticion.SerializeToString())

    decodificada = normalize.parse_body(comprimido, "application/x-protobuf", "gzip")
    (senal,) = normalize.signals_from_otlp(decodificada)
    assert senal.signature == "timeout"


def test_descomprime_gzip_no_declarado() -> None:
    """Un cliente puede comprimir y no decirlo. Los magic bytes lo delatan."""
    import gzip

    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    comprimido = gzip.compress(peticion.SerializeToString())

    decodificada = normalize.parse_body(comprimido, "application/x-protobuf", None)
    (senal,) = normalize.signals_from_otlp(decodificada)
    assert senal.signature == "timeout"


def test_un_cuerpo_sin_comprimir_sigue_funcionando() -> None:
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    decodificada = normalize.parse_body(peticion.SerializeToString(), "application/x-protobuf", None)
    assert len(list(normalize.signals_from_otlp(decodificada))) == 1


# --- El formato REAL de vmalert ----------------------------------------------
# Regresion encontrada al arrancar vmalert contra el alert-bus. Manda un ARRAY
# pelado, que es el formato de la API v2 de Alertmanager; nosotros asumiamos
# `{"alerts": [...]}`, que es el formato de WEBHOOK.
#
# La integracion real llevaba rota un rato mientras los tests pasaban, porque
# los tests hablaban nuestro formato en vez del suyo.


def test_acepta_el_array_pelado_que_manda_vmalert() -> None:
    payload = [
        {
            "labels": {
                "alertname": "ArgusSLOBurnRateFast",
                "service_namespace": "intelligent-document-platform",
                "service_name": "idp-api",
                "severity": "page",
            },
            "annotations": {"summary": "quema rápida del presupuesto de error"},
            "startsAt": "2026-09-13T00:02:46Z",
        }
    ]
    (senal,) = normalize.signals_from_alertmanager(payload)
    assert senal.kind is SignalKind.BURN_RATE
    assert senal.app == "intelligent-document-platform"


def test_sigue_aceptando_el_formato_de_webhook() -> None:
    """Los scripts y el canario usan esta forma, que es mas comoda de escribir."""
    payload = {"alerts": [{"labels": {"alertname": "x", "service_namespace": "a"}, "annotations": {}}]}
    assert len(list(normalize.signals_from_alertmanager(payload))) == 1


def test_un_array_vacio_no_produce_senales() -> None:
    assert list(normalize.signals_from_alertmanager([])) == []


def test_la_severidad_de_la_regla_se_respeta() -> None:
    """`severity: ticket` en una regla es intención del autor, no ruido.

    Sin leerla, toda alerta del camino templado se resolvía por tipo de señal
    —BURN_RATE = page— así que una regla que pedía `ticket` despertaba a alguien
    igual. Con la fatiga de alertas como problema dominante, una plataforma que
    convierte todo en `page` se silencia sola.
    """
    from alert_bus.normalize import signals_from_alertmanager

    (senal,) = signals_from_alertmanager([{
        "labels": {"alertname": "ArgusAlgo", "severity": "ticket",
                   "service_namespace": "argus", "service_name": "vmalert"},
        "annotations": {"summary": "algo"},
    }])
    assert senal.severity is Severity.TICKET


def test_una_severidad_desconocida_no_revienta_la_ingesta() -> None:
    """Un valor mal escrito en una regla no puede tirar el camino templado."""
    from alert_bus.normalize import signals_from_alertmanager

    (senal,) = signals_from_alertmanager([{
        "labels": {"alertname": "X", "severity": "urgentisimo"},
        "annotations": {},
    }])
    assert senal.severity is None


def test_la_criticidad_sigue_acotando_lo_que_pide_la_regla(registry) -> None:
    """Pedir `page` no basta: un laboratorio no despierta a nadie ni pidiéndolo.

    La severidad de la regla es una petición, no una orden. La válvula de
    criticidad es lo que impide que una app de juguete genere guardias.
    """
    from alert_bus.engine import Engine

    motor = Engine(registry, [])
    motor.ingest([Signal(kind=SignalKind.BURN_RATE, severity=Severity.PAGE,
                         app="llm-benchmark", component="bench-runner",
                         signature="X", title="x")])

    (incidente,) = motor.open_incidents()
    assert incidente.severity.rank <= registry.get("llm-benchmark").severity_ceiling.rank
