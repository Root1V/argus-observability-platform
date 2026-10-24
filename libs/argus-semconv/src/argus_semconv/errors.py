"""La jerarquia de la clase de un error, en UN solo sitio.

Lo que agrupa los errores no es un campo: es un ORDEN de preferencia entre
campos. Y ese orden estaba escrito tres veces —en el OTTL del gateway, en el
evento ancho del SDK y en el normalizador del alert-bus—, con un comentario en
cada copia afirmando que las tres coincidian.

**No coincidian.** Al reunirlas aqui, medido sobre el codigo:

| | pasos | valor terminal |
|---|---|---|
| gateway (`argus.error.kind`) | `error.type` → `exception.type` | `unclassified` |
| SDK (`_wide.py`)             | `error.type` → `exception.type` | `unclassified` |
| alert-bus (`normalize.py`)   | + `argus.error.kind` y + la operacion | **`error`** |

Dos divergencias reales, y ninguna salia en ninguna prueba: el alert-bus
terminaba en `error` donde los otros dos terminan en `unclassified`, y el
docstring del evento ancho afirmaba que *«hay una prueba que compara la
jerarquia de las dos»* — no existia. Lo que habia eran dos pruebas de
comportamiento con la frase «misma precedencia que en el gateway» en prosa, y
ningun test que leyera `gateway.yaml`.

Es la misma forma que D-117, D-118, D-122 y D-123: **la prosa correcta al lado
de la linea que hace otra cosa.** Aqui la prosa estaba en tres sitios y el
desacuerdo en dos (D-125).

**Lo que vive aqui y lo que no.** Aqui vive la parte CANONICA, la que las tres
tienen que compartir. El alert-bus necesita ademas que la firma nunca quede
vacia, asi que cae a la operacion — pero eso es una capa SUYA, explicita y
encima de esta, no otra version de esta. El gateway no puede importar Python,
asi que sigue en OTTL; lo que le ata es `test_la_jerarquia_del_error_es_una`,
que lee `gateway.yaml` y compara el orden contra `ORDEN`.

Por que el paso de `argus.error.kind` esta en el medio y no estorba: es la
SALIDA de esta misma jerarquia cuando el gateway ya la calculo. En el SDK la
clave no existe todavia —corre antes— y en el gateway es el campo que se esta
escribiendo. Solo hace algo en el alert-bus, que es el unico que puede recibir
una señal ya enriquecida.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from . import attributes as A

# Un valor, no una ausencia: distingue «no se pudo clasificar» de «no es un
# error», que es justo la distincion que un campo vacio pierde. Mismo criterio
# que `unknown` en `argus.actor.kind`.
SIN_CLASIFICAR: Final[str] = "unclassified"

# El orden de preferencia, declarado como dato para que se pueda comprobar
# contra otra implementacion en vez de leerse y creerse.
ORDEN: Final[tuple[str, ...]] = (
    A.ERROR_TYPE,
    A.ARGUS_ERROR_KIND,
    "exception.type",
)


def clase_y_mensaje(
    atributos: Mapping[str, Any] | None,
    excepciones: Iterable[Mapping[str, Any]] = (),
    descripcion: str | None = None,
) -> tuple[str, str | None]:
    """La clase de un error y su mensaje, siguiendo `ORDEN`.

    `atributos` son los del span. `excepciones` son los atributos de sus
    eventos llamados `exception`, ya extraidos por quien llama: cada emisor
    tiene su propia forma de span —el SDK trae `ReadableSpan`, el alert-bus
    trae protobuf de OTLP— y esta funcion no deberia conocer ninguna de las
    dos. `descripcion` es el `status.description`, el ultimo sitio donde
    buscar un mensaje.

    La clase SIEMPRE es un valor util para un `GROUP BY`: un nombre de clase o
    `unclassified`. El mensaje puede faltar, y **nunca** entra en la clase: es
    texto libre, y el `DETAIL:` de Postgres lleva los UUID de la fila. Meterlo
    fue lo que convirtio un error en 335 firmas distintas (D-118).
    """
    atributos = atributos or {}
    tipo: Any = atributos.get(A.ERROR_TYPE)

    if not tipo:
        derivado = atributos.get(A.ARGUS_ERROR_KIND)
        if derivado and derivado != SIN_CLASIFICAR:
            tipo = derivado

    mensaje: str | None = None
    for ea in excepciones:
        if not tipo:
            tipo = ea.get("exception.type")
        mensaje = ea.get("exception.message") or mensaje
        break

    if not mensaje and descripcion:
        mensaje = descripcion

    return (str(tipo) if tipo else SIN_CLASIFICAR, mensaje or None)


def clase_del_error(
    atributos: Mapping[str, Any] | None,
    excepciones: Iterable[Mapping[str, Any]] = (),
) -> str:
    """Solo la clase, para quien no necesita el mensaje."""
    return clase_y_mensaje(atributos, excepciones)[0]
