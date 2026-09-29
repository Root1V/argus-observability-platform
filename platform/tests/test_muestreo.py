"""Lo que no se puede muestrear.

Un registro de auditoria al 10% no es un registro de auditoria.

Se descubrio midiendo: Prometheus adopto `argus.target.*` y ejecuto dos
acciones admin reales para que las verificaramos contra el almacen. Ninguna
llego. Ninguna politica aplicaba a un PATCH admin —no falla, dura
milisegundos, no toca cola, no es GenAI— asi que caian al `baseline` del 10%
(D-098).

El coste de equivocarse no es simetrico: una traza normal perdida es una
muestra menos; una accion administrativa perdida es un hueco en el registro de
quien hizo que, y el Anexo III del AI Act obliga a conservarlo seis meses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GATEWAY = Path(__file__).resolve().parents[1] / "collector" / "gateway.yaml"


@pytest.fixture(scope="module")
def politicas() -> list[dict]:
    cfg = yaml.safe_load(GATEWAY.read_text(encoding="utf-8"))
    return cfg["processors"]["tail_sampling"]["policies"]


def test_la_auditoria_tiene_politica_propia(politicas: list[dict]) -> None:
    nombres = [p["name"] for p in politicas]
    assert "auditoria" in nombres, f"sin politica de auditoria: {nombres}"


def test_la_politica_mira_el_ambito_del_evento(politicas: list[dict]) -> None:
    """El evento de auditoria vive en `spanevent`, y las politicas de atributo
    solo miran el span. Es el mismo ambito que se escapo en D-092."""
    aud = next(p for p in politicas if p["name"] == "auditoria")
    assert aud["type"] == "ottl_condition", (
        "una politica `string_attribute` no alcanza los atributos del evento"
    )
    condiciones = aud["ottl_condition"]
    assert condiciones.get("spanevent"), "la politica no mira el ambito spanevent"


def test_la_auditoria_se_decide_antes_del_baseline(politicas: list[dict]) -> None:
    """El orden no cambia el resultado —basta con que UNA politica diga que si—
    pero leerlo despues del muestreo probabilistico invita a pensar lo
    contrario, y quien edite esto despues no tiene por que saberlo."""
    nombres = [p["name"] for p in politicas]
    assert nombres.index("auditoria") < nombres.index("baseline")


def test_el_baseline_sigue_siendo_lo_ultimo(politicas: list[dict]) -> None:
    """Una politica anadida despues del baseline seguiria funcionando, pero la
    lista dejaria de leerse como "estas se quedan, el resto al 10%"."""
    assert politicas[-1]["name"] == "baseline"
    assert politicas[-1]["type"] == "probabilistic"
