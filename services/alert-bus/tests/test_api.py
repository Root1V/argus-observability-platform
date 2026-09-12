"""La API: autenticacion, rutas y respuesta inmediata."""

from __future__ import annotations

import pytest
from alert_bus.app import create_app
from alert_bus.config import Settings
from alert_bus.engine import Engine
from alert_bus.sinks import MemorySink
from argus_semconv import attributes as A
from fastapi.testclient import TestClient
from otlp_factory import STATUS_ERROR, build_request


@pytest.fixture
def client(registry, sink: MemorySink):
    engine = Engine(registry, [sink])
    app = create_app(Settings(token="secreto", sinks="memory"), engine=engine)
    with TestClient(app) as c:
        c.engine = engine          # type: ignore[attr-defined]
        c.sink = sink              # type: ignore[attr-defined]
        yield c


AUTH = {"Authorization": "Bearer secreto"}


def test_rechaza_sin_token(client) -> None:
    """El token protege la integridad de lo que entra.

    Importa porque los agentes leen esta telemetria y sacan conclusiones:
    aceptar senales de cualquiera es aceptar conclusiones de cualquiera.
    """
    r = client.post("/v1/traces", content=b"", headers={"Content-Type": "application/x-protobuf"})
    assert r.status_code == 401


def test_rechaza_con_token_incorrecto(client) -> None:
    r = client.post("/v1/traces", content=b"", headers={"Authorization": "Bearer otro"})
    assert r.status_code == 401


def test_acepta_otlp_protobuf_y_abre_incidente(client) -> None:
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    r = client.post(
        "/v1/traces",
        content=peticion.SerializeToString(),
        headers={**AUTH, "Content-Type": "application/x-protobuf"},
    )
    assert r.status_code == 200
    assert len(client.sink.opened) == 1
    assert client.sink.opened[0].signature == "timeout"


def test_acepta_otlp_json(client) -> None:
    from google.protobuf.json_format import MessageToJson

    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    r = client.post(
        "/v1/traces",
        content=MessageToJson(peticion).encode(),
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert len(client.sink.opened) == 1


def test_un_cuerpo_vacio_no_es_un_error(client) -> None:
    """El Collector manda lotes vacios cuando no hay nada que reportar."""
    r = client.post("/v1/traces", content=b"", headers={**AUTH, "Content-Type": "application/x-protobuf"})
    assert r.status_code == 200


def test_un_lote_corrupto_devuelve_400_sin_tumbar_el_receptor(client) -> None:
    """Perderiamos TODA la deteccion por un cliente que envia mal."""
    r = client.post(
        "/v1/traces",
        content=b"basura binaria que no es protobuf" * 20,
        headers={**AUTH, "Content-Type": "application/x-protobuf"},
    )
    assert r.status_code == 400

    # Y sigue funcionando despues.
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    assert client.post(
        "/v1/traces",
        content=peticion.SerializeToString(),
        headers={**AUTH, "Content-Type": "application/x-protobuf"},
    ).status_code == 200


def test_acepta_alertas_de_vmalert(client) -> None:
    r = client.post(
        "/api/v2/alerts",
        json={
            "alerts": [
                {
                    "labels": {
                        "alertname": "ArgusSLOBurnRateFast",
                        "service_namespace": "intelligent-document-platform",
                        "service_name": "idp-api",
                    },
                    "annotations": {"summary": "quema rapida"},
                }
            ]
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert len(client.sink.opened) == 1


def test_expone_los_incidentes_abiertos(client) -> None:
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    client.post("/v1/traces", content=peticion.SerializeToString(), headers={**AUTH, "Content-Type": "application/x-protobuf"})

    incidentes = client.get("/incidents").json()
    assert len(incidentes) == 1
    assert incidentes[0]["signature"] == "timeout"
    assert incidentes[0]["severity"] == "page"


def test_reconocer_un_incidente(client) -> None:
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    client.post("/v1/traces", content=peticion.SerializeToString(), headers={**AUTH, "Content-Type": "application/x-protobuf"})
    incidente_id = client.get("/incidents").json()[0]["id"]

    assert client.post(f"/incidents/{incidente_id}/ack").json()["status"] == "acknowledged"
    assert client.post("/incidents/no-existe/ack").status_code == 404


def test_enriquecer_actualiza_el_mismo_hilo(client) -> None:
    """Lo llama el agente de investigacion al terminar (D-015)."""
    peticion = build_request(span_attrs={A.ERROR_TYPE: "timeout"}, status_code=STATUS_ERROR)
    client.post("/v1/traces", content=peticion.SerializeToString(), headers={**AUTH, "Content-Type": "application/x-protobuf"})
    huella = client.get("/incidents").json()[0]["fingerprint"]

    r = client.post(
        f"/incidents/{huella}/enrich",
        json={"root_cause": "El despliegue a9f3c21 bajo el timeout", "confidence": "alta"},
    )
    assert r.status_code == 200
    assert len(client.sink.opened) == 1      # sigue habiendo UN aviso
    assert len(client.sink.updated) == 1     # y una actualizacion


def test_healthz_y_stats(client) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    assert "signals_in" in client.get("/stats").json()


def test_expone_el_registro(client) -> None:
    apps = client.get("/registry").json()
    assert any(a["id"] == "intelligent-document-platform" for a in apps)
