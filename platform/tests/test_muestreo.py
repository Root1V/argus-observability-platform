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


def test_la_auditoria_cubre_los_desenlaces_de_gobierno(politicas: list[dict]) -> None:
    """Una denegación y una espera son registros, no muestras.

    Le recomendamos a Aeon emitir `argus.outcome=denied` sin `argus.guardrail`
    —para no paginar en cada denegación de política— y medir la tasa. Con solo
    eso no aplicaba ninguna política: **0 de 8 denegaciones** sobrevivieron al
    `baseline` en la prueba. El consejo cambiaba una notificación de más por un
    registro incompleto al 90%, que es peor porque el ruido se nota y un
    registro con huecos no (D-099).
    """
    aud = next(p for p in politicas if p["name"] == "auditoria")
    for ambito in ("span", "spanevent"):
        condiciones = " ".join(aud["ottl_condition"][ambito])
        for desenlace in ("denied", "suspended"):
            assert f'"{desenlace}"' in condiciones, (
                f"`{ambito}` no conserva `argus.outcome={desenlace}`"
            )


def test_la_politica_de_auditoria_no_perdio_sus_condiciones_originales(politicas: list[dict]) -> None:
    """Un `spanevent:` duplicado en YAML pisa al anterior SIN error.

    Pasó al añadir los desenlaces de gobierno: escribí un segundo bloque
    `spanevent` en vez de ampliar el que había, y el fichero siguió siendo YAML
    válido y el Collector lo habría aceptado — con la condición del evento de
    auditoría desaparecida. Se vio al imprimir el YAML *parseado*, no el texto.
    """
    aud = next(p for p in politicas if p["name"] == "auditoria")
    spanevent = aud["ottl_condition"]["spanevent"]
    assert any("audit.admin_action" in c for c in spanevent), (
        "la condición del evento de auditoría ya no está"
    )
    assert any("argus.target.type" in c for c in spanevent)
    assert len(spanevent) >= 4


def test_el_fichero_no_tiene_claves_duplicadas() -> None:
    """Generaliza lo de arriba a todo el gateway.

    `yaml.safe_load` acepta claves repetidas y se queda con la última, así que
    un bloque duplicado desactiva al anterior en silencio en cualquier parte
    del fichero, no solo en esta política.
    """
    import yaml

    class SinDuplicados(yaml.SafeLoader):
        pass

    def _mapa(loader, nodo, deep=False):
        vistas = set()
        for clave_nodo, _ in nodo.value:
            clave = loader.construct_object(clave_nodo, deep=deep)
            if clave in vistas:
                raise AssertionError(f"clave duplicada en gateway.yaml: {clave!r}")
            vistas.add(clave)
        return yaml.SafeLoader.construct_mapping(loader, nodo, deep)

    SinDuplicados.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa)
    yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=SinDuplicados)
