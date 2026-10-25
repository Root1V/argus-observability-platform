"""El titular es lo unico que se lee en el movil, y no puede afirmar de mas.

Decia dos cosas: `investigando…` sin `root_cause` y `causa identificada` con
ella. El problema aparecio al escribir quien rellena ese campo. L3 tiene casos
en los que recolecta el expediente entero y NINGUNA regla concluye; con dos
estados, la unica forma de entregar ese contexto era escribir una descripcion
del sintoma en `root_cause`, y entonces el titular anunciaba una causa
identificada que nadie habia identificado (D-130).

Son tres pruebas y una de ellas es la que importa: **un diagnostico de
confianza baja no se anuncia como una certeza**.
"""

from __future__ import annotations

import pytest
from alert_bus.sinks.render import titular
from argus_schemas import Incident, Severity, SignalKind


def _incidente(**campos) -> Incident:
    base = {
        "id": "i1", "fingerprint": "h1", "kind": SignalKind.ERROR, "severity": Severity.PAGE,
        "app": "prometheus-inference-platform", "component": "gateway",
        "title": "502 en gateway", "signature": "502",
    }
    return Incident(**{**base, **campos})


def test_sin_causa_sigue_diciendo_que_investiga_aunque_ya_haya_evidencia() -> None:
    """Es la verdad, y la evidencia va igualmente en el cuerpo.

    L3 manda confianza y evidencia incluso cuando no concluye. Eso ya convierte
    el aviso en algo utilizable —cuanto trafico fallo, desde cuando, que mas
    estaba roto— sin anunciar una causa.
    """
    incidente = _incidente(
        confidence="baja",
        evidence=["12 de 12 peticiones fallaron en 5 min (100.0%)"],
    )

    assert "investigando…" in titular(incidente)


def test_una_causa_de_confianza_baja_se_anuncia_como_posible_no_como_identificada() -> None:
    incidente = _incidente(root_cause="puede que el pool de conexiones", confidence="baja")

    linea = titular(incidente)
    assert "causa posible" in linea
    assert "identificada" not in linea


@pytest.mark.parametrize("confianza", ["media", "alta"])
def test_una_causa_con_confianza_se_anuncia_como_probable_y_nunca_identificada(confianza: str) -> None:
    """Identificada la deja una persona cuando lo confirma; L3 propone."""
    incidente = _incidente(root_cause="auth-service dio la llamada por buena", confidence=confianza)

    linea = titular(incidente)
    assert "causa probable" in linea
    assert "identificada" not in linea


def test_una_causa_sin_confianza_declarada_no_se_hunde_a_posible() -> None:
    """`confidence` es opcional en el esquema y puede llegar vacio.

    Un enriquecimiento hecho a mano por el endpoint `enrich` —que acepta
    cualquier subconjunto de campos— puede traer causa y no traer confianza. Ese
    caso se trata como probable y no como posible: quien escribio una causa sin
    calificarla no estaba diciendo que dudara de ella.
    """
    incidente = _incidente(root_cause="lo dijo una persona", confidence=None)

    assert "causa probable" in titular(incidente)
