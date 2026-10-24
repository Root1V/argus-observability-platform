"""Como se identifica ante OpenTelemetry el codigo que emite.

El scope de un tracer o un meter tiene que decir **que version del codigo
emitio esto**. Hasta 1.0.0a4 registrabamos ahi `SEMCONV_VERSION`, que es la
version del MODELO de convenciones y vale `1.0.0` desde hace tiempo. El efecto
era que un prelanzamiento y una estable declaraban exactamente lo mismo
—`scope: argus-semconv 1.0.0`— y en el almacen no habia forma de separarlos
(D-082). Es justo lo que quieres poder hacer cuando algo no cuadra durante una
adopcion.

Ahora el scope lleva la version del PAQUETE, que es la que distingue.

**Y aqui NO hay atributos de scope, desde `1.0.0a25`.** D-082 dejo la version
del modelo viajando como atributo del scope, «que es donde pertenece». La frase
era razonable y el sitio no existe:

- La tabla de trazas tiene columnas `ScopeName` y `ScopeVersion` y **ninguna
  para los atributos del scope**. Medido: 3.406 spans emitidos con
  `argus.semconv.version` desde la a4, y el atributo **no esta en ninguno**. Se
  emitia a la nada, un mes y medio.
- Y aunque aterrizara, no diria nada nuevo. El modelo **viaja dentro del
  paquete** —`modelo.json` se genera de `libs/semconv-model/argus.yaml` en la
  construccion—, asi que una version del paquete fija exactamente una version
  del modelo. `ScopeVersion` ya responde las dos preguntas.

Lo que resolvio B-28 de sus tres salidas —columna, atributo de span, o
retirarlo— fue la tercera, y por la segunda razon: la primera habria sido
hacerle sitio a un dato redundante. El SDK llego a la misma conclusion por su
cuenta y lo documenta en `argus/_scope.py`; ahora los dos paquetes dicen lo
mismo (D-126).
"""

from __future__ import annotations


def version_paquete() -> str:
    """Version instalada de `argus-obs-semconv`, o un marcador si no lo esta."""
    try:
        from importlib.metadata import version

        return version("argus-obs-semconv")
    except Exception:  # noqa: BLE001 - nunca por no saber una version
        return "0.0.0.dev0"
