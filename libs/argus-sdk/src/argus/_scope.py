"""Como se identifica ante OpenTelemetry el codigo del SDK que emite.

**El scope de un tracer dice QUE VERSION DEL CODIGO emitio un span**, y es lo
unico que lo dice: no hay ningun atributo de recurso que lleve la version de
nuestras librerias. `service.version` es la de la aplicacion, no la nuestra.

`argus-semconv` ya lo hace bien desde `1.0.0a4`, y lo hace porque se arreglo:
hasta entonces registraba ahi la version del MODELO, que vale `1.0.0` desde
hace mucho, asi que un prelanzamiento y una estable declaraban lo mismo y en el
almacen no habia forma de separarlos (D-082).

**Este fichero existe porque ese arreglo no se aplico al SDK.** Sus tres
`get_tracer("argus-sdk")` no pasaban version ninguna, que es peor que el estado
que D-082 corrigio — alli al menos habia un numero equivocado; aqui no habia
numero. El efecto se vio al querer contestar una pregunta sencilla del dueño de
la plataforma: *«que version de argus corre cada equipo?»*. De los cuatro
equipos integrados solo se podia responder de uno, y de rebote, porque usan
`semconv` ademas del SDK. De los otros tres la respuesta era lo que nos habian
dicho, no lo que medimos (D-119).

Una propiedad util del arreglo: **la ausencia pasa a ser informativa.** Un span
de `argus-sdk` sin version de scope es, desde ahora, un emisor anterior a
`1.0.0a23`.
"""

from __future__ import annotations

from typing import Any


def version_paquete() -> str:
    """Version instalada de `argus-obs-sdk`, o un marcador si no lo esta.

    Nunca levanta. Una version desconocida es un dato peor; una excepcion al
    construir el tracer seria la aplicacion caida por telemetria, que es lo
    primero que el contrato del SDK prohibe.
    """
    try:
        from importlib.metadata import version

        return version("argus-obs-sdk")
    except Exception:  # noqa: BLE001 - nunca por no saber una version
        return "0.0.0.dev0"


def atributos() -> dict[str, Any]:
    """Atributos del scope.

    Hoy solo el nombre del paquete que de verdad emite. Se distingue de
    `argus-semconv`, que publica su propio scope: un span puede venir del
    middleware del SDK o de un decorador de semconv, y en el almacen eso se
    tiene que poder separar sin adivinar.
    """
    return {"argus.sdk.package": "argus-obs-sdk"}
