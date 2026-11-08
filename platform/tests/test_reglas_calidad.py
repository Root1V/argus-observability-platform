"""F5-03: la regla de calidad solo avisa por la condicion CONJUNTA, y con el Mac despierto.

El comportamiento lo prueba `platform/scripts/probar-reglas.sh` con
`vmalert-tool` (cinco casos con series sinteticas). Esto fija la ESTRUCTURA,
que es lo que un cambio rapido romperia sin que nadie corriera el script.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REGLAS = Path(__file__).resolve().parents[1] / "rules"


def _reglas(fichero: str) -> list[dict]:
    datos = yaml.safe_load((REGLAS / fichero).read_text(encoding="utf-8"))
    return [r for g in datos["groups"] for r in g["rules"]]


def test_la_unica_alerta_de_calidad_es_la_conjunta() -> None:
    """Deriva sola es falsa alarma: ninguna de las dos mitades puede avisar sola."""
    alertas = [r for r in _reglas("calidad.yaml") if "alert" in r]
    assert [a["alert"] for a in alertas] == ["ArgusEvalCaeConDerivaDeEntrada"]
    expr = alertas[0]["expr"]
    assert "argus:eval_caida" in expr and "argus:deriva_entrada" in expr
    assert len(re.findall(r"\band on\b", expr)) >= 2, "las dos mitades y la guarda, unidas con `and`"


def test_la_alerta_exige_el_observador_despierto() -> None:
    """El Mac se suspende: una ventana de 6 h con medio dia de sueno no se compara con una semana."""
    alerta = next(r for r in _reglas("calidad.yaml") if "alert" in r)
    assert "argus:observador_despierto:fraccion6h > 0.8" in alerta["expr"]
    assert any(r.get("record") == "argus:observador_despierto:fraccion6h" for r in _reglas("plataforma.yaml"))


def test_cada_mitad_exige_un_volumen_minimo() -> None:
    por_nombre = {r["record"]: r["expr"] for r in _reglas("calidad.yaml") if "record" in r}
    assert "argus:eval_volumen:6h >= 10" in por_nombre["argus:eval_caida"]
    assert "argus:llamadas:6h >= 20" in por_nombre["argus:deriva_entrada"]


def test_desconocido_no_cuenta_como_evaluado() -> None:
    """D-154: un span sin el atributo no es ni bueno ni malo."""
    for r in _reglas("calidad.yaml"):
        if "argus_eval_results_total" in r.get("expr", ""):
            assert "desconocido" not in r["expr"]


def test_el_runbook_existe() -> None:
    alerta = next(r for r in _reglas("calidad.yaml") if "alert" in r)
    assert (REGLAS.parents[1] / alerta["annotations"]["runbook"]).exists()
