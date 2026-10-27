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
    return (f"INCIDENTE {huella}\n  firma: ReadTimeout\n"
            "EL OTRO LADO\n  atendida : manager-api, 81471ms\n  el 4.1% tarda mas de lo que espero quien llama (75.2 s)")


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



# --- F4-05: la evidencia, verificada contra lo que devolvio la herramienta ------


def test_una_cifra_que_ninguna_herramienta_devolvio_se_señala_y_baja_la_confianza() -> None:
    r = _inv(calls("expediente", huella="h1"),
             entrega(causa="manager-api es lento", confianza="alta", descartadas=["la red: hubo respuesta"],
                     evidencia=["[expediente] tardo 81 s y el gateway espero 75 s",
                                "[expediente] el 63% de las peticiones fallaron"])).investigar("h1")

    estados = [v["estado"] for v in r.verificacion]
    assert estados == ["verificada", "cifras sin respaldo"]
    assert r.verificacion[1]["sin_respaldo"] == ["63"]
    assert r.confianza == "media" and "alta → media" in r.confianza_rebajada


def test_los_redondeos_y_los_milisegundos_cuentan_como_respaldo() -> None:
    from diagnose.investigador import _respaldada

    assert _respaldada("81", ["81471"])         # 81471 ms → «81 s»
    assert _respaldada("4.1", ["4.13"])         # redondeo a sus decimales
    assert not _respaldada("26.7", ["26.3"])    # cercano no es lo mismo
    assert not _respaldada("63", ["4.1", "75.2", "81471"])


def test_las_cifras_pequeñas_no_cuentan_porque_estan_en_cualquier_expediente() -> None:
    """Contra el expediente equivocado, «30 dias» o «1 error» aprobaban cualquier cosa (D-152)."""
    from diagnose.investigador import _cifras

    assert _cifras("errores en 4 de los ultimos 30 dias, 0 minutos, 1 error") == []
    assert _cifras("ultimo dato el 2026-10-25 03:07Z, en 11 de los 60 minutos") == []
    assert _cifras("tardo 81471ms, el 4.1% y 75 s") == ["81471", "4.1", "75"]


def test_el_informe_para_una_persona_dice_que_se_comprobo_y_que_no() -> None:
    from diagnose.investigador import informe_markdown

    r = _inv(calls("expediente", huella="h1"),
             entrega(causa="manager-api es lento", confianza="alta", descartadas=["la red: hubo respuesta 200"],
                     evidencia=["[expediente] tardo 81 s", "[expediente] el 63% fallo", "[traza] inventado"])
             ).investigar("h1")
    texto = informe_markdown(r)

    assert "✓ [expediente] tardo 81 s" in texto
    assert "✗ [expediente] el 63% fallo — no aparece: 63" in texto
    assert "Quitado del informe" in texto and "[traza] inventado" in texto
    assert "Alternativas descartadas" in texto and "la red: hubo respuesta 200" in texto
    assert "Confianza rebajada" in texto
