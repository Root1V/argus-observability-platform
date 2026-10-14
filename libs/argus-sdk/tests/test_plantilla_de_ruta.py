"""`http.route` tiene que ser la PLANTILLA, no el path resuelto.

Lo encontro Veritium en su primer dia, y no lo encontraron leyendo: lo
encontramos nosotros midiendo su trafico real en el almacen. Su
`POST /v1/cases/{case_id}/reprocess` llegaba asi:

    http.route  /v1/cases/c1919a15-e1f0-4711-8f6f-2dcd5436cdd9/reprocess
    SpanName    POST /v1/cases/c1919a15-e1f0-4711-8f6f-2dcd5436cdd9/reprocess

El middleware leia `scope.get("route_path")`, que **no es una clave de ASGI ni
de Starlette**: aparecia una sola vez en todo el repositorio, en la linea que la
leia. Siempre caia al path resuelto.

Y por eso duro tanto: **con una ruta estatica el path resuelto ES la
plantilla**, asi que los diez tests del middleware pasaban y `/mcp` salia
perfecto. Solo fallaba con parametros, que son las rutas que importan.

Las pruebas de `test_asgi_trust.py` no lo cazaban porque construyen el `scope` a
mano, sin router. Aqui hay una aplicacion de verdad, porque la plantilla la pone
el router y no el diccionario.
"""

from __future__ import annotations

import pytest
from argus.asgi import ASGIMiddleware

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CASO = "c1919a15-e1f0-4711-8f6f-2dcd5436cdd9"


@pytest.fixture
def app_con_parametro() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/cases/{case_id}/reprocess")
    async def reprocess(case_id: str) -> dict:
        return {"case_id": case_id}

    @app.get("/mcp")
    async def mcp() -> dict:
        return {"ok": True}

    app.add_middleware(ASGIMiddleware, propagate_mode="never")
    return app


def _span_de(exporter, nombre_parcial: str):
    return next(s for s in exporter.get_finished_spans() if nombre_parcial in s.name)


def test_una_ruta_con_parametro_sale_como_plantilla(app_con_parametro, exporter) -> None:
    exporter.clear()
    with TestClient(app_con_parametro) as c:
        assert c.post(f"/v1/cases/{CASO}/reprocess").status_code == 200

    s = _span_de(exporter, "reprocess")
    assert s.attributes["http.route"] == "/v1/cases/{case_id}/reprocess", (
        f"http.route = {s.attributes['http.route']!r}: lleva el id dentro, asi que "
        f"un GROUP BY da una fila por caso"
    )
    assert s.name == "POST /v1/cases/{case_id}/reprocess", (
        f"el nombre del span es {s.name!r}. Entra en `argus.error.fingerprint`, "
        f"asi que cada caso que falle daria una huella distinta"
    )
    assert CASO not in s.attributes["http.route"]
    assert CASO not in s.name


def test_el_path_resuelto_SI_se_conserva_aparte(app_con_parametro, exporter) -> None:
    """La plantilla agrupa; el path concreto filtra. Los dos hacen falta, y por
    eso van en campos distintos — es el mismo argumento que separa
    `argus.target.type` de `argus.target.id`."""
    exporter.clear()
    with TestClient(app_con_parametro) as c:
        c.post(f"/v1/cases/{CASO}/reprocess")

    s = _span_de(exporter, "reprocess")
    assert s.attributes["url.path"] == f"/v1/cases/{CASO}/reprocess"


def test_una_ruta_estatica_no_cambia(app_con_parametro, exporter) -> None:
    """El caso que hacia parecer que todo estaba bien."""
    exporter.clear()
    with TestClient(app_con_parametro) as c:
        assert c.get("/mcp").status_code == 200

    s = _span_de(exporter, "/mcp")
    assert s.attributes["http.route"] == "/mcp"
    assert s.name == "GET /mcp"




def test_cien_casos_distintos_dan_UNA_sola_ruta(app_con_parametro, exporter) -> None:
    """La propiedad, dicha en la forma en que importa.

    Antes de esto, cien expedientes daban cien `http.route`, cien nombres de
    span y —desde `a20`— cien huellas de error distintas.
    """
    import uuid

    exporter.clear()
    with TestClient(app_con_parametro) as c:
        for _ in range(100):
            c.post(f"/v1/cases/{uuid.uuid4()}/reprocess")

    spans = [s for s in exporter.get_finished_spans() if "reprocess" in s.name]
    assert len(spans) == 100
    assert len({s.attributes["http.route"] for s in spans}) == 1
    assert len({s.name for s in spans}) == 1
