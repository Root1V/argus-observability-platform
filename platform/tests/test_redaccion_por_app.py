"""Patrones de redaccion por aplicacion (D-121).

Lo pidio Veritium (`VRT-ARG-003`) con una evidencia exacta: nuestro patron de
documento es `\\b\\d{8}[A-HJ-NP-TV-Z]\\b` —ocho digitos **y una letra**, que es
el DNI espanol— asi que el peruano, ocho digitos solos, no lo reconoce. Y el de
telefono exige separadores, con buen criterio, asi que un celular escrito
seguido pasa.

**Por que no se amplia el patron global**: un `\\b\\d{8}\\b` para todos taparia
fechas como `20261015` y cualquier contador de ocho cifras de cualquier equipo.

La capa global existe para cuando la de origen falla. Si la global no conoce los
identificadores del pais de una aplicacion, para esa aplicacion la red no esta.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GATEWAY = Path(__file__).resolve().parents[1] / "collector" / "gateway.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load(GATEWAY.read_text(encoding="utf-8"))


def _sentencias(cfg: dict) -> list[tuple[str, str]]:
    proc = cfg["processors"]["transform/redaccion_por_app"]
    fuera = []
    for clave in ("trace_statements", "log_statements"):
        for bloque in proc.get(clave, []):
            for s in bloque["statements"]:
                fuera.append((bloque["context"], s))
    return fuera


def test_todo_patron_va_acotado_a_una_aplicacion(cfg: dict) -> None:
    """Un patron propio que se aplicara a todos seria el patron global, y
    entonces taparia las fechas y los contadores de los demas."""
    for ambito, s in _sentencias(cfg):
        assert 'resource.attributes["service.namespace"]' in s, (
            f"esta sentencia no esta acotada a una aplicacion:\n  [{ambito}] {s}"
        )


def test_cubre_el_ambito_del_EVENTO_y_no_solo_el_span(cfg: dict) -> None:
    """El `DETAIL:` de Postgres que provoco esto vivia en el evento de
    excepcion. Es el ambito que se escapo en D-092, D-098 y D-113."""
    ambitos = {a for a, _ in _sentencias(cfg)}
    assert "spanevent" in ambitos, "los eventos de excepcion quedan sin redactar"
    assert "span" in ambitos
    assert "log" in ambitos, "los registros quedan sin redactar"


def test_la_marca_contable_va_ANTES_del_reemplazo(cfg: dict) -> None:
    """Despues del reemplazo no queda nada que detectar.

    La marca existe porque el tercer criterio de Veritium era que «el conteo
    suba», y ese contador es del processor `redaction`, que no sabe aplicar
    patrones por app. `transform` no cuenta nada.
    """
    sentencias = [s for _, s in _sentencias(cfg)]
    i_marca = next(i for i, s in enumerate(sentencias) if "argus.redaction.app_pattern" in s)
    i_primer_reemplazo = next(i for i, s in enumerate(sentencias) if "replace_pattern" in s)
    assert i_marca < i_primer_reemplazo, (
        "la marca se pone despues de reemplazar, asi que no puede cazar nada"
    )


def test_la_marca_esta_declarada_en_el_modelo() -> None:
    from argus_semconv import attributes as A

    assert A.ARGUS_REDACTION_APP_PATTERN == "argus.redaction.app_pattern"


def test_va_despues_de_la_redaccion_global_y_antes_de_seudonimizar(cfg: dict) -> None:
    """Orden: la red global primero, los patrones propios despues, y la
    seudonimizacion al final sobre lo que quede."""
    for nombre, tuberia in cfg["service"]["pipelines"].items():
        procs = tuberia.get("processors", [])
        if "transform/redaccion_por_app" not in procs:
            continue
        i = procs.index("transform/redaccion_por_app")
        assert procs.index("redaction") < i, f"{nombre}: va antes de la redaccion global"
        assert i < procs.index("transform/pseudonymize"), f"{nombre}: va despues de seudonimizar"


def test_esta_en_las_dos_tuberias_que_llevan_texto_libre(cfg: dict) -> None:
    """Trazas y registros. Un dato personal en un registro no esta mas
    protegido por estar en un registro."""
    con = {
        n for n, t in cfg["service"]["pipelines"].items()
        if "transform/redaccion_por_app" in t.get("processors", [])
    }
    assert "traces/clickhouse" in con
    assert "logs" in con


def test_el_patron_global_sigue_sin_el_ocho_digitos_suelto() -> None:
    """La decision que esto NO cambia, y que hay que poder leer.

    `\\b\\d{8}\\b` global taparia `20261015` y cualquier contador. Que siga
    fuera del patron global es la razon de ser de esta via.
    """
    from argus_semconv import _content

    patrones = [p.pattern for p, _ in _content._PATTERNS]
    assert r"\b\d{8}\b" not in patrones, (
        "el patron de ocho digitos sueltos entro en los patrones GLOBALES: "
        "ahora tapa fechas y contadores de todos los equipos"
    )
    assert any(p == r"\b\d{8}[A-HJ-NP-TV-Z]\b" for p in patrones), (
        "desaparecio el patron de DNI con letra"
    )
