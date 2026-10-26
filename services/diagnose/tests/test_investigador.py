"""F4-01/F4-02: el investigador de L4 con el doble de synaptum.

Se prueba lo que no puede depender del modelo: que entregue un informe, que el
presupuesto corte de verdad y deje un informe PARCIAL sin causa, que no arranque
con herramientas que escriben, y que lo que devuelve vaya sin volcados.
"""

from __future__ import annotations

import pytest
from diagnose.investigador import Investigador, Presupuesto
from synaptum import Risk, tool
from synaptum.testing import FakeGateway, calls, says

INFORME = ('{"causa": "manager-api tarda normalmente mas que el plazo del gateway", "confianza": "alta",'
           ' "evidencia": ["[linea_base] el 4% pasa de 75 s"], "descartadas": ["la red: hubo respuesta 200"]}')


@tool
def expediente(huella: str) -> str:
    """El expediente del incidente."""
    return f"INCIDENTE {huella}\n  firma: ReadTimeout"


@tool(risk=Risk.DESTRUCTIVE)
def borrar(huella: str) -> str:
    """Borra un incidente."""
    return "borrado"


def _inv(*guion, presupuesto: Presupuesto | None = None, herramientas=(expediente,)) -> Investigador:
    return Investigador(lambda hs: FakeGateway(*guion, tools=hs), herramientas, modelo="doble",
                        presupuesto=presupuesto)


def test_mira_el_expediente_y_entrega_un_informe_que_cita_y_descarta() -> None:
    r = _inv(calls("expediente", huella="h1"), says(INFORME)).investigar("h1")

    assert r.final == "informe" and not r.parcial
    assert r.herramientas_usadas == ["expediente"]
    assert r.confianza == "alta"
    assert r.evidencia[0].startswith("[linea_base]")
    assert r.descartadas == ["la red: hubo respuesta 200"]


def test_al_agotar_las_herramientas_sale_un_informe_parcial_sin_causa() -> None:
    """F4-02: cortar no es fallar; es entregar lo visto, sin inventar una conclusion."""
    guion = [calls("expediente", id=f"c{i}", huella="h1") for i in range(10)] + [says(INFORME)]
    r = _inv(*guion, presupuesto=Presupuesto(herramientas=3)).investigar("h1")

    assert r.final == "tope_herramientas" and r.parcial
    assert r.causa is None and r.confianza == "baja"
    assert len(r.herramientas_usadas) == 3
    assert "INFORME PARCIAL" in r.evidencia[0]


def test_no_arranca_con_una_herramienta_que_escribe() -> None:
    with pytest.raises(ValueError, match="borrar"):
        _inv(herramientas=(expediente, borrar))


def test_un_informe_sin_causa_baja_la_confianza() -> None:
    r = _inv(says('{"causa": null, "confianza": "alta", "evidencia": [], "descartadas": []}')).investigar("h1")

    assert r.causa is None and r.confianza == "baja" and r.final == "informe"


def test_el_volcado_de_una_fila_no_sale_en_el_informe() -> None:
    sucio = ('{"causa": "null value in column tenant DETAIL:  Failing row contains (0012345).", "confianza": "media",'
             ' "evidencia": [], "descartadas": []}')
    r = _inv(says(sucio)).investigar("h1")

    assert "0012345" not in r.causa
