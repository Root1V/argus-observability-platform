"""Convenciones semanticas de Argus, para LIBRERIAS.

Este paquete depende SOLO de `opentelemetry-api`. Nunca del SDK, nunca de un
exportador. Esa restriccion es el contrato:

- Si la aplicacion que te importa no inicializo un SDK, todo esto es no-op con
  coste cero, y tu libreria funciona exactamente igual que sin instrumentar.
- Si lo inicializo, tu instrumentacion se enciende sola usando SU
  configuracion, SU endpoint y SU muestreo.

Una libreria que dependiera del SDK impondria en silencio su version y sus
opiniones sobre exportadores a todo el que dependa de ella.

Las aplicaciones no usan este paquete directamente: usan `argus-sdk`, que lo
re-exporta ademas de configurar la telemetria.
"""

from __future__ import annotations

from . import attributes
from ._content import capture_enabled, mask
from .genai import GenAISpan, agent, documents, genai, retrieval, tool
from .steps import Step, instrument, step

__version__ = attributes.SEMCONV_VERSION

__all__ = [
    # Convenciones generadas
    "attributes",
    # GenAI
    "genai",
    "retrieval",
    "tool",
    "agent",
    "documents",
    "GenAISpan",
    # Unidades de trabajo
    "step",
    "instrument",
    "Step",
    # Contenido
    "capture_enabled",
    "mask",
    "__version__",
]
