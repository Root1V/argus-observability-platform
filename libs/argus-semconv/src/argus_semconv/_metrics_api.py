"""Puente de compatibilidad. El modulo publico es `argus_semconv.metrics`.

Vivio aqui, con guion bajo, hasta 1.0.0a5. Era incoherente: estas metricas
existen porque nadie las emitiria a mano, y aun asi obligaban a quien las
necesitaba a importar de un modulo privado (D-082).

Se mantiene una version para no romper a quien ya escribio el import feo por
necesidad. Reexporta, no duplica: los instrumentos son los mismos objetos.
"""

from __future__ import annotations

import warnings

from .metrics import (
    record_cost,
    record_duration,
    record_tokens,
    record_ttft,
)

warnings.warn(
    "argus_semconv._metrics_api es privado y desaparece en 1.1. "
    "Usa argus_semconv.metrics, que es lo mismo y es publico.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["record_cost", "record_duration", "record_tokens", "record_ttft"]
