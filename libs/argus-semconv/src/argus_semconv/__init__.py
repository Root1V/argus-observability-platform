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

from . import attributes, metrics
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


def modelo() -> dict:
    """El modelo de convenciones de ESTA version instalada, como diccionario.

    Para quien no emite desde Python —Aeon tiene cuatro binarios en Go— el
    fichero se lee directo:

        python -c "import argus_semconv,pathlib;print(pathlib.Path(argus_semconv.__file__).parent/'modelo.json')"

    La gracia es que describe la version que tienes instalada. Un fichero en la
    rama principal describe lo que habra, no lo que corre.
    """
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parent / "modelo.json").read_text(encoding="utf-8"))

__all__ = [
    "modelo",
    # Convenciones generadas
    "attributes",
    # Metricas GenAI, emitidas desde la API. Publico desde 1.0.0a5: quien
    # sirve inferencia las necesita y no puede importar de un modulo privado.
    "metrics",
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
