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
from .guardrails import AgentRun, Budget, GuardrailBreach, current_run
from .steps import Step, instrument, step

# La version del PAQUETE, distinta de la de las convenciones: el paquete puede
# publicarse varias veces sin que cambien las convenciones.
try:
    from importlib.metadata import version as _version

    __version__ = _version("argus-obs-semconv")
except Exception:  # noqa: BLE001
    __version__ = "0.0.0.dev0"

#: Version del MODELO de convenciones (libs/semconv-model/argus.yaml).
SEMCONV_VERSION = attributes.SEMCONV_VERSION

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
    # Guardarrailes de agentes
    "Budget",
    "GuardrailBreach",
    "AgentRun",
    "current_run",
    # Unidades de trabajo
    "step",
    "instrument",
    "Step",
    # Contenido
    "capture_enabled",
    "mask",
    "SEMCONV_VERSION",
    "__version__",
]
