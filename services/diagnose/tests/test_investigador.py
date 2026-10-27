"""F4-01/F4-02: el investigador de L4 con el doble de synaptum.

Se prueba lo que no puede depender del modelo: que entregue un informe, que el
presupuesto corte de verdad y deje un informe PARCIAL sin causa, que no arranque
con herramientas que escriben, y que lo que devuelve vaya sin volcados.
"""

from __future__ import annotations

import pytest
from diagnose.investigador import Investigador, Presupuesto
from synaptum import Risk, tool
from synaptum.testing import FakeGateway, calls

INFORME = {"causa": "manager-api tarda normalmente mas que el plazo del gateway", "confianza": "alta",
           "evidencia": ["[expediente] el 4% pasa de 75 s"], "descartadas": ["la red: hubo respuesta 200"]}


def entrega(**informe):
    """El informe va por la herramienta de entrega (`submit_tool`), no como texto."""
    return calls("submit", id="entrega", **informe)


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
    r = _inv(calls("expediente", huella="h1"), entrega(**INFORME)).investigar("h1")

    assert r.final == "informe" and not r.parcial
    assert r.herramientas_usadas == ["expediente"]
    assert r.confianza == "alta"
    assert r.evidencia[0].startswith("[expediente]")
    assert r.descartadas == ["la red: hubo respuesta 200"]


def test_al_agotar_las_herramientas_sale_un_informe_parcial_sin_causa() -> None:
    """F4-02: cortar no es fallar; es entregar lo visto, sin inventar una conclusion."""
    guion = [calls("expediente", id=f"c{i}", huella="h1") for i in range(10)] + [entrega(**INFORME)]
    r = _inv(*guion, presupuesto=Presupuesto(herramientas=3)).investigar("h1")

    assert r.final == "tope_herramientas" and r.parcial
    assert r.causa is None and r.confianza == "baja"
    assert len(r.herramientas_usadas) == 3
    assert "INFORME PARCIAL" in r.evidencia[0]


def test_no_arranca_con_una_herramienta_que_escribe() -> None:
    with pytest.raises(ValueError, match="borrar"):
        _inv(herramientas=(expediente, borrar))


def test_un_informe_sin_causa_baja_la_confianza() -> None:
    r = _inv(entrega(causa=None, confianza="alta", evidencia=[], descartadas=[])).investigar("h1")

    assert r.causa is None and r.confianza == "baja" and r.final == "informe"


def test_el_volcado_de_una_fila_no_sale_en_el_informe() -> None:
    r = _inv(entrega(causa="null value in column tenant DETAIL:  Failing row contains (0012345).",
                     confianza="media", evidencia=[], descartadas=[])).investigar("h1")

    assert "0012345" not in r.causa


def test_la_evidencia_que_cita_una_herramienta_no_llamada_se_quita() -> None:
    """El 26/10 citaba `[traza]` habiendo llamado solo a `expediente` (D-146)."""
    r = _inv(calls("expediente", huella="h1"),
             entrega(causa="x", confianza="alta", descartadas=[],
                     evidencia=["[expediente] tres ConnectError", "[traza] fallo tras 19 s"])).investigar("h1")

    assert r.evidencia == ["[expediente] tres ConnectError"]
    assert r.citas_inventadas == ["[traza] fallo tras 19 s"]


def test_si_toda_la_evidencia_era_inventada_la_confianza_baja() -> None:
    r = _inv(entrega(causa="x", confianza="alta", descartadas=[], evidencia=["[linea_base] el 4%"])).investigar("h1")

    assert r.evidencia == [] and r.confianza == "baja"


def test_citar_un_bloque_del_expediente_leido_es_citar_el_expediente() -> None:
    """Con razonamiento, el agente rotulaba los hechos con el bloque del expediente (D-147)."""
    r = _inv(calls("expediente", huella="h1"),
             entrega(causa="x", confianza="media", descartadas=[],
                     evidencia=["[alcance] 23,2% de fallos", "[otra_cosa] inventado"])).investigar("h1")

    assert r.evidencia == ["[alcance] 23,2% de fallos"]
    assert r.citas_inventadas == ["[otra_cosa] inventado"]


# --- F4-01: durable -------------------------------------------------------------


def test_reanudar_con_el_mismo_run_id_no_repite_inferencias_ni_herramientas(tmp_path) -> None:
    """D-135: lo pagado se reproduce desde el diario; solo se llama al modelo para lo que faltaba."""
    from synaptum.run.sqlite import SqliteCheckpointer

    diario = tmp_path / "l4.sqlite"
    gateways: list[FakeGateway] = []

    def investigador(*guion) -> Investigador:
        def fabrica(hs):
            gateways.append(FakeGateway(*guion, tools=hs))
            return gateways[-1]
        return Investigador(fabrica, [expediente], modelo="doble", checkpointer=SqliteCheckpointer(diario))

    with pytest.raises(RuntimeError):     # el proceso «muere» a mitad: nada lo atrapa, como en la realidad
        investigador(calls("expediente", huella="h1"), RuntimeError("el proceso murio")).investigar(
            "h1", run_id="l4-x")
    assert gateways[-1].tool_calls == 1

    segundo = investigador(entrega(**INFORME)).investigar("h1", run_id="l4-x")
    assert segundo.final == "informe" and segundo.causa
    assert gateways[-1].model_calls == 1, "solo la llamada que faltaba"
    assert gateways[-1].tool_calls == 0, "el expediente se reprodujo, no se volvio a pedir"
    assert segundo.pasos_reproducidos >= 2


def test_el_run_id_es_el_mismo_para_la_misma_peticion_y_otro_para_otra() -> None:
    from diagnose.investigador import id_de_investigacion

    a = id_de_investigacion("m", "off", "h1", "2026-10-27T10:00")
    assert a == id_de_investigacion("m", "off", "h1", "2026-10-27T10:00")
    assert a != id_de_investigacion("m", "off", "h1", "2026-10-27T16:00")
    assert a != id_de_investigacion("m", "high", "h1", "2026-10-27T10:00")
