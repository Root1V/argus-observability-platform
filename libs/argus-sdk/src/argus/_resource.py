"""Construccion del Resource.

Una nota que parece un detalle y no lo es: usamos `Resource.create()` y NUNCA
el constructor `Resource(attributes=...)`. La diferencia es que el constructor
directo SALTA los detectores y la variable estandar OTEL_RESOURCE_ATTRIBUTES,
en silencio. Y esa variable es justo el mecanismo por el que se inyecta la
version y el entorno desde el compose sin tocar codigo.

Es un fallo de una linea con impacto en todos los repos, y no da ningun error:
simplemente los atributos no aparecen.
"""

from __future__ import annotations

import os

from argus_semconv import attributes as A
from opentelemetry.sdk.resources import Resource

from ._config import Config


def _del_entorno() -> set[str]:
    """Claves que OTEL_RESOURCE_ATTRIBUTES ya trae."""
    crudo = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
    return {p.split("=", 1)[0].strip() for p in crudo.split(",") if "=" in p}


def build_resource(cfg: Config, extra: dict[str, object] | None = None) -> Resource:
    # Un RELLENO nuestro nunca puede pisar la variable estandar.
    #
    # `Resource.create()` fusiona, y lo que le pasamos gana. Eso es correcto
    # para lo que alguien eligio y es un fallo para lo que rellenamos: sin esto,
    # `OTEL_RESOURCE_ATTRIBUTES=service.namespace=X` se ignoraba en silencio
    # porque `namespace` cae a `service` y `role` a "api" cuando no se dicen.
    #
    # Es el mismo fallo que pedimos arreglar a otro equipo, en nuestra propia
    # casa y con otra forma: alli era el constructor directo, aqui un valor por
    # defecto disfrazado de eleccion (D-072).
    del_entorno = _del_entorno()
    relleno = {
        A.SERVICE_NAMESPACE: "namespace",
        A.ARGUS_APP: "namespace",
        A.ARGUS_COMPONENT_ROLE: "role",
        A.DEPLOYMENT_ENVIRONMENT_NAME: "environment",
        A.SERVICE_VERSION: "version",
        A.SERVICE_INSTANCE_ID: "instance_id",
    }

    def cede(clave: str) -> bool:
        campo = relleno.get(clave)
        return (
            campo is not None
            and clave in del_entorno
            and campo not in getattr(cfg, "elegidos", set())
        )

    attributes: dict[str, object] = {
        # Identidad de dos niveles: la aplicacion y el sub-componente.
        A.SERVICE_NAMESPACE: cfg.namespace,
        A.SERVICE_NAME: cfg.service,
        A.ARGUS_COMPONENT_ROLE: cfg.role,
        A.SERVICE_INSTANCE_ID: cfg.instance_id,
        A.DEPLOYMENT_ENVIRONMENT_NAME: cfg.environment,
        # `argus.app` tambien viaja en baggage entre procesos, para atribucion.
        A.ARGUS_APP: cfg.namespace,
    }

    if cfg.version:
        attributes[A.SERVICE_VERSION] = cfg.version

    # Langfuse lee estos dos LITERALMENTE. Ponerlos en el Resource es gratis y
    # ahorra un processor de transformacion en el Collector.
    attributes[A.LANGFUSE_ENVIRONMENT] = cfg.environment
    if cfg.version:
        attributes[A.LANGFUSE_RELEASE] = cfg.version

    if extra:
        attributes.update(extra)

    # Lo rellenado por nosotros se retira si el entorno lo declara.
    for clave in [k for k in attributes if cede(k)]:
        del attributes[clave]

    # Resource.create() fusiona detectores (process, os, host) y
    # OTEL_RESOURCE_ATTRIBUTES con lo que pasamos. Lo explicito gana.
    return Resource.create(attributes)
