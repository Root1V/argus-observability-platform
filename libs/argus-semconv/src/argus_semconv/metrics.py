"""Instrumentos de metricas GenAI, emitidos desde la API de OpenTelemetry.

**Superficie publica.** Vivio como `_metrics_api` hasta 1.0.0a5, y eso era
incoherente: el propio docstring de abajo dice que estas metricas tienen que
salir solas porque nadie las emitiria a mano, y a la vez la puerta estaba
marcada como privada. Un equipo de plataforma de inferencia —que es justo quien
mas las necesita— tenia que escribir `from argus_semconv._metrics_api import
record_ttft` para usarlas, que es exactamente lo que un consumidor no debe hacer
(D-082).

El modulo antiguo sigue importando durante una version, con aviso.

Por que vive aqui y no en el SDK de aplicacion: si las metricas hubiera que
emitirlas a mano, nadie las emitiria. Las convenciones dicen que
`gen_ai.client.operation.duration` y `gen_ai.client.token.usage` van desde el
dia uno, y eso solo se cumple si salen SOLAS del mismo context manager que ya
se usa para las trazas.

La API de metricas de OpenTelemetry tiene la misma propiedad que la de trazas:
sin SDK configurado devuelve instrumentos no operativos con coste cero. Asi que
una libreria puede emitir metricas sin imponer nada a quien la importe, igual
que con los spans.

Los BUCKETS no se fijan aqui —eso es configuracion del SDK— sino en las vistas
de `argus-obs-sdk`, que los aplica por nombre de instrumento cuando esta
presente.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics

from . import _scope
from . import attributes as A

_meter = metrics.get_meter(
    "argus-semconv",
    _scope.version_paquete(),
    attributes=_scope.atributos(),
)

# Creados una vez al importar. Sin SDK son no-ops.
_duracion = _meter.create_histogram(
    A.M_GEN_AI_CLIENT_OPERATION_DURATION,
    unit=A.M_GEN_AI_CLIENT_OPERATION_DURATION_UNIT,
    description="Duracion de la operacion GenAI",
)
_tokens = _meter.create_histogram(
    A.M_GEN_AI_CLIENT_TOKEN_USAGE,
    unit=A.M_GEN_AI_CLIENT_TOKEN_USAGE_UNIT,
    description="Tokens consumidos, con el tipo como dimension",
)
_ttft = _meter.create_histogram(
    A.M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN,
    unit=A.M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN_UNIT,
    description="Tiempo hasta el primer token",
)
_coste = _meter.create_counter(
    A.M_ARGUS_COST_USD,
    unit=A.M_ARGUS_COST_USD_UNIT,
    description="Coste atribuido",
)


def _base(operation: str, provider: str, model: str | None) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        A.GEN_AI_OPERATION_NAME: operation,
        A.GEN_AI_PROVIDER_NAME: provider,
    }
    if model:
        attrs[A.GEN_AI_REQUEST_MODEL] = model
    return attrs


def record_duration(
    *, operation: str, provider: str, model: str | None,
    seconds: float, error_type: str | None = None,
) -> None:
    attrs = _base(operation, provider, model)
    if error_type:
        attrs[A.ERROR_TYPE] = error_type
    try:
        _duracion.record(seconds, attrs)
    except Exception:  # noqa: BLE001 - la telemetria nunca tumba la app
        pass


def record_tokens(
    *, operation: str, provider: str, model: str | None,
    input_tokens: int | None = None, output_tokens: int | None = None,
) -> None:
    """`gen_ai.token.type` va como DIMENSION, no como dos instrumentos.

    Es lo que dice la especificacion y lo que permite agregar por modelo sin
    unir series a mano.
    """
    attrs = _base(operation, provider, model)
    try:
        if input_tokens:
            _tokens.record(input_tokens, {**attrs, "gen_ai.token.type": "input"})
        if output_tokens:
            _tokens.record(output_tokens, {**attrs, "gen_ai.token.type": "output"})
    except Exception:  # noqa: BLE001
        pass


def record_ttft(
    *, provider: str, model: str | None, seconds: float,
    operation: str = "chat", backend_id: str | None = None,
) -> None:
    """Tiempo hasta el primer token.

    `operation` existe —y no se descarta— porque las otras tres metricas la
    llevan, y sin ella el TTFT es la unica que no se puede trocear por tipo de
    operacion. En un despliegue donde chat, embeddings y rerank conviven, ese
    es justo el corte que mas falta hace (D-082).

    Antes se construia con `"chat"` fijo y luego se BORRABA el atributo, asi
    que la serie salia sin operacion y no habia forma de pedirla.
    """
    attrs = _base(operation, provider, model)
    if backend_id:
        attrs[A.ARGUS_INFERENCE_BACKEND_ID] = backend_id
    try:
        _ttft.record(seconds, attrs)
    except Exception:  # noqa: BLE001
        pass


def record_cost(
    *, cost_usd: float, model: str | None,
    app: str | None = None, feature: str | None = None, use_case: str | None = None,
) -> None:
    """Dimensiones pensadas para poder ATRIBUIR el coste.

    Sin app, funcionalidad y caso de uso, el coste llega como un numero opaco a
    fin de mes y no se puede optimizar nada.
    """
    # Se OMITE la dimension que no se conoce, no se rellena con "unknown".
    # Con el relleno, "nadie atribuyo esta llamada" y "se atribuyo a algo que
    # se llama unknown" son indistinguibles, las dos generan serie temporal y
    # las dos cuestan cardinalidad. Es la enfermedad de los tres estados en una
    # etiqueta de metrica (D-082).
    attrs: dict[str, Any] = {}
    if app:
        attrs[A.ARGUS_APP] = app
    if feature:
        attrs[A.ARGUS_FEATURE] = feature
    if use_case:
        attrs[A.ARGUS_USE_CASE] = use_case
    if model:
        attrs[A.GEN_AI_REQUEST_MODEL] = model
    try:
        _coste.add(cost_usd, attrs)
    except Exception:  # noqa: BLE001
        pass
