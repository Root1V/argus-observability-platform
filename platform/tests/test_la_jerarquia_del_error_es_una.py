"""La jerarquia de la clase de error es UNA, y esta prueba es lo que la ata.

El orden de preferencia entre campos —`error.type`, luego el `exception.type`
del evento, luego `unclassified`— es lo que decide que errores caen juntos. Y
estaba escrito tres veces: en el OTTL de este `gateway.yaml`, en el evento
ancho del SDK y en el normalizador del alert-bus.

Las tres copias llevaban un comentario afirmando que coincidian. **Dos habian
divergido**: el alert-bus terminaba en `error` donde las otras dos terminan en
`unclassified`. No lo cazo ninguna prueba porque no habia ninguna que mirara
las dos cosas a la vez: lo que habia era, en el SDK, un docstring diciendo
*«hay una prueba que compara la jerarquia de las dos»* junto a dos pruebas de
comportamiento que no leen este fichero (D-125).

Los dos modulos de Python ya importan la jerarquia de `argus_semconv.errors`.
El gateway no puede importar Python, asi que su copia en OTTL se queda — y
esto es lo unico que puede impedir que se separe otra vez.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from argus_semconv import attributes as A
from argus_semconv.errors import ORDEN, SIN_CLASIFICAR

GATEWAY = Path(__file__).resolve().parents[1] / "collector" / "gateway.yaml"
RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sentencias() -> list[str]:
    """Las sentencias OTTL de `transform/error_fingerprint`, en orden.

    El procesador declara bloques por contexto (`span`, `spanevent`) y el orden
    entre bloques es el orden de ejecucion, asi que se aplanan conservandolo.
    """
    cfg = yaml.safe_load(GATEWAY.read_text(encoding="utf-8"))
    bloques = cfg["processors"]["transform/error_fingerprint"]["trace_statements"]
    return [s for b in bloques for s in b["statements"]]


def _clasificadoras(sentencias: list[str]) -> list[str]:
    """Las sentencias que derivan `argus.error.kind`, que son la jerarquia.

    El mismo procesador calcula ademas `argus.error.fingerprint`, y esa si lee
    `service.namespace`, `service.name` y `span.name` — pero eso es de que se
    compone la huella, no en que orden se clasifica. Mezclarlas fue el primer
    intento de esta prueba y daba un orden que no era el de nadie.
    """
    return [s for s in sentencias if f'["{A.ARGUS_ERROR_KIND}"]' in s.split(",")[0]]


def _leidas(sentencias: list[str]) -> list[str]:
    """Los campos que el OTTL LEE para clasificar, en orden de aparicion.

    Se descuenta `argus.error.kind`: aparece en el lado derecho de la segunda
    sentencia y de la tercera, pero como guardia —«si todavia no hay clase»—,
    no como fuente. Es el campo que se esta escribiendo.
    """
    orden: list[str] = []
    for s in _clasificadoras(sentencias):
        cuerpo = s[s.index(",") + 1 :] if "," in s else s
        for campo in re.findall(r'attributes\["([^"]+)"\]', cuerpo):
            if campo != A.ARGUS_ERROR_KIND and campo not in orden:
                orden.append(campo)
        if f'"{SIN_CLASIFICAR}"' in cuerpo and SIN_CLASIFICAR not in orden:
            orden.append(SIN_CLASIFICAR)
    return orden


def test_el_gateway_clasifica_en_el_mismo_orden_que_las_convenciones(sentencias) -> None:
    """El orden del OTTL es el de `ORDEN`, con su mismo valor terminal.

    `argus.error.kind` se descuenta de la comparacion: en la jerarquia
    compartida es el paso que aprovecha un valor YA derivado, y aqui es
    justamente el campo que se esta derivando. Solo hace algo en el alert-bus,
    que es el unico de los tres que puede recibir una señal ya enriquecida.
    """
    esperado = [c for c in ORDEN if c != A.ARGUS_ERROR_KIND] + [SIN_CLASIFICAR]

    assert _leidas(sentencias) == esperado, (
        "el gateway clasifica en otro orden que `argus_semconv.errors.ORDEN`; "
        "si el orden cambio a proposito, cambialo en los dos sitios"
    )


def test_el_gateway_termina_en_el_mismo_valor_que_las_convenciones(sentencias) -> None:
    """`unclassified`, el ultimo, y el valor sale de las convenciones.

    Es la divergencia que de verdad habia: un valor terminal distinto en una de
    las tres copias parte en dos el `GROUP BY` de todo lo inclasificable.

    Que sea el ULTIMO no es un detalle de estilo: asignarlo antes de intentar
    el `exception.type` haria que el `where ... == nil` de la sentencia de
    despues no se cumpliera nunca, y los 46 errores que esa mitad rescata
    volverian a ser inagrupables — con las tres sentencias presentes y en el
    fichero, que es como se ve bien y funciona mal.
    """
    clasificadoras = _clasificadoras(sentencias)
    terminales = [s for s in clasificadoras if f'"{SIN_CLASIFICAR}"' in s]

    assert terminales, f"el gateway no asigna {SIN_CLASIFICAR!r} a {A.ARGUS_ERROR_KIND}"
    assert terminales[-1] == clasificadoras[-1], (
        f"{SIN_CLASIFICAR!r} se asigna antes de agotar la jerarquia"
    )


@pytest.mark.parametrize(
    "modulo",
    [
        "libs/argus-sdk/src/argus/_wide.py",
        "services/alert-bus/src/alert_bus/normalize.py",
    ],
)
def test_nadie_vuelve_a_escribir_la_jerarquia_en_python(modulo: str) -> None:
    """Los dos consumidores la IMPORTAN; ninguno la reimplementa.

    La marca es `exception.type`: es el paso que solo existe dentro de la
    jerarquia, asi que verlo escrito en otro modulo es verlo copiado. Si algun
    dia hace falta de verdad ahi, lo que toca es exponerlo desde
    `argus_semconv.errors`, no volver a teclearlo.
    """
    texto = (RAIZ / modulo).read_text(encoding="utf-8")
    assert "argus_semconv.errors import" in texto, f"{modulo} no importa la jerarquia"
    assert '"exception.type"' not in texto, (
        f"{modulo} vuelve a nombrar `exception.type`: la jerarquia se esta copiando otra vez"
    )
