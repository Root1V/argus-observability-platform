"""F3-07: `mcp-obs`, probado en proceso y con dobles: sin ClickHouse ni alert-bus.

Lo que se fija es el contrato con el agente que lo va a usar: que todas las
herramientas se anuncien como solo lectura, que el almacen solo reciba
lecturas, que los volcados de filas no salgan, y que las ventanas que pida un
modelo se acoten a lo que el almacen guarda.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from diagnose.mcp_obs import crear_servidor

from .test_recoleccion import AlmacenFalso, _incidente

VOLCADO = 'null value in column "tenant" DETAIL:  Failing row contains (0012345, x).'


def _bus(abiertos=None, historico=None):
    rutas = {
        "/incidents": abiertos if abiertos is not None else [_incidente(signature=VOLCADO)],
        "/incidents/history?dias=30": historico or [],
        "/registry": [{"id": "prometheus-inference-platform", "componentes": ["gateway"], "depende_de": []}],
    }
    pedidas: list[str] = []

    def bus(ruta: str) -> Any:
        pedidas.append(ruta)
        return rutas.get(ruta)

    bus.pedidas = pedidas  # type: ignore[attr-defined]
    return bus


def _llamar(servidor, nombre: str, **argumentos) -> Any:
    resultado = asyncio.run(servidor.call_tool(nombre, argumentos))
    bloques = [b.text for b in resultado.content if getattr(b, "type", "") == "text"]
    texto = "\n".join(bloques)
    try:
        return json.loads(texto)
    except ValueError:
        return texto


def test_todas_las_herramientas_se_anuncian_de_solo_lectura() -> None:
    """Sin la pista, synaptum las trataria como destructivas — que es lo correcto si no se dice."""
    herramientas = asyncio.run(crear_servidor(AlmacenFalso(), _bus()).list_tools())

    assert {h.name for h in herramientas} >= {"incidentes_abiertos", "incidente", "expediente", "traza", "linea_base"}
    for h in herramientas:
        assert h.annotations.read_only_hint is True, h.name
        assert h.annotations.destructive_hint is False, h.name


def test_el_volcado_de_una_fila_no_sale_por_ninguna_herramienta() -> None:
    servidor = crear_servidor(AlmacenFalso(), _bus())

    for nombre, args in (("incidentes_abiertos", {}), ("incidente", {"huella": "h1"})):
        salida = json.dumps(_llamar(servidor, nombre, **args), ensure_ascii=False)
        assert "0012345" not in salida and "Failing row" not in salida, nombre


def test_el_expediente_es_el_mismo_texto_que_lee_l3() -> None:
    texto = _llamar(crear_servidor(AlmacenFalso(), _bus()), "expediente", huella="h1")

    assert texto.startswith("INCIDENTE")
    assert "ARBOL DE UNA TRAZA DE EJEMPLO" in texto


def test_una_huella_que_no_existe_se_dice_y_no_revienta() -> None:
    salida = _llamar(crear_servidor(AlmacenFalso(), _bus(abiertos=[])), "incidente", huella="nada")
    assert "no hay ningun incidente" in salida["error"]


def test_un_modelo_no_puede_pedir_mas_de_lo_que_guarda_el_almacen() -> None:
    almacen = AlmacenFalso()
    _llamar(crear_servidor(almacen, _bus()), "recurrencia", app="a", componente="c", clase="X", dias=3650)

    _, kw = next(p for p in almacen.preguntas if p[0] == "recurrencia")
    assert kw["dias"] == 30
