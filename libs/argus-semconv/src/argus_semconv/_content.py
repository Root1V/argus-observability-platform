"""Captura y enmascarado de contenido de prompts.

Dos decisiones que conviene entender antes de tocar este fichero.

1. El contenido va en ATRIBUTOS de span, no en eventos.

   La guia canonica de las semconv GenAI dice que el contenido debe ir en
   eventos de span, para poder descartarlo en el Collector sin tocar codigo.
   El razonamiento es bueno. Pero Langfuse lee `gen_ai.input.messages` y
   `gen_ai.output.messages` de los ATRIBUTOS del span. Si seguimos la guia al
   pie de la letra, Langfuse ingiere el span y lo muestra sin input ni output,
   que es justo lo que lo hace util.

   Resolucion: emitir en atributos, y borrarlos en la rama del Collector que
   va a ClickHouse (donde son decenas de KB sin ninguna consulta que los use).

2. Enmascarar aqui es la PRIMERA capa, no la unica.

   El Collector tiene un `redaction` processor como red de seguridad. Esa
   segunda capa es la que importa de verdad: significa que un fallo de
   instrumentacion en una app no se convierte en una fuga en el almacen.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any, Final

_TRUE: Final = frozenset({"1", "true", "yes", "on"})

# Umbral por defecto. Un prompt entero puede ser de decenas de KB; un atributo
# de span no es sitio para eso.
_DEFAULT_MAX_BYTES: Final = 8192

_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[CARD]"),
    # Telefono: EXIGE separadores o prefijo internacional. Una tirada suelta de
    # diez digitos es casi siempre un identificador, un timestamp o un contador,
    # no un telefono. Redactarla destruye datos utiles y no protege nada.
    # Mismo criterio que el `redaction` processor del Collector: las dos capas
    # tienen que coincidir o los datos salen distintos segun por donde pasen.
    (re.compile(r"\+\d{1,3}[-.\s]\d{2,4}[-.\s]\d{2,4}[-.\s]?\d{0,4}"), "[PHONE]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{8}[A-HJ-NP-TV-Z]\b"), "[DNI]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[IBAN]"),
    (re.compile(r"(?i)\b(?:sk|pk)-[A-Za-z0-9_\-]{16,}"), "[KEY]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.]{16,}"), "[TOKEN]"),
)


def capture_enabled() -> bool:
    """Si se captura el contenido de prompts y respuestas.

    Por defecto NO. Se activa en desarrollo y de forma selectiva por aplicacion
    en produccion, donde la evaluacion aporta.
    """
    val = os.getenv("ARGUS_CAPTURE_CONTENT", "")
    if val:
        return val.strip().lower() in _TRUE
    # Compatibilidad con la variable estandar de OpenTelemetry.
    return os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "").strip().lower() in _TRUE


def max_bytes() -> int:
    try:
        return max(0, int(os.getenv("ARGUS_CONTENT_MAX_BYTES", str(_DEFAULT_MAX_BYTES))))
    except ValueError:
        return _DEFAULT_MAX_BYTES


def mask(text: str) -> str:
    """Redacta PII conocida. Primera capa; el Collector es la segunda."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def serialize(
    payload: Any,
    *,
    masker: Callable[[str], str] | None = None,
    limit: int | None = None,
) -> tuple[str, bool]:
    """Serializa a JSON, enmascara y trunca.

    Devuelve `(texto, truncado)`. Nunca lanza: si el payload no es
    serializable cae a `repr`, porque perder telemetria es aceptable y tumbar
    la aplicacion del usuario no lo es.
    """
    try:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = repr(payload)

    raw = (masker or mask)(raw)

    cap = max_bytes() if limit is None else limit
    if cap and len(raw.encode("utf-8")) > cap:
        # Cortamos por bytes y descartamos el ultimo caracter posiblemente
        # partido a mitad de secuencia UTF-8.
        raw = raw.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
        return raw, True

    return raw, False
