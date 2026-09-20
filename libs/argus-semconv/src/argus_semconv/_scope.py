"""Como se identifica ante OpenTelemetry el codigo que emite.

El scope de un tracer o un meter tiene que decir **que version del codigo
emitio esto**. Hasta 1.0.0a4 registrabamos ahi `SEMCONV_VERSION`, que es la
version del MODELO de convenciones y vale `1.0.0` desde hace tiempo. El efecto
era que un prelanzamiento y una estable declaraban exactamente lo mismo
—`scope: argus-semconv 1.0.0`— y en el almacen no habia forma de separarlos
(D-082). Es justo lo que quieres poder hacer cuando algo no cuadra durante una
adopcion.

Ahora el scope lleva la version del PAQUETE, y la del modelo viaja como
atributo del scope, que es donde pertenece: son dos cosas distintas y las dos
son utiles.
"""

from __future__ import annotations

from typing import Any

from . import attributes as A


def version_paquete() -> str:
    """Version instalada de `argus-obs-semconv`, o un marcador si no lo esta."""
    try:
        from importlib.metadata import version

        return version("argus-obs-semconv")
    except Exception:  # noqa: BLE001 - nunca por no saber una version
        return "0.0.0.dev0"


def atributos() -> dict[str, Any]:
    """Atributos del scope. La version del modelo va aqui, no en la del scope."""
    return {"argus.semconv.version": A.SEMCONV_VERSION}
