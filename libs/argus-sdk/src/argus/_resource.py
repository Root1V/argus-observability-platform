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

from argus_semconv import attributes as A
from opentelemetry.sdk.resources import Resource

from ._config import Config


def build_resource(cfg: Config, extra: dict[str, object] | None = None) -> Resource:
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

    # Resource.create() fusiona detectores (process, os, host) y
    # OTEL_RESOURCE_ATTRIBUTES con lo que pasamos. Lo explicito gana.
    return Resource.create(attributes)
