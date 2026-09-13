"""Instrumentos de metricas GenAI, emitidos desde la API de OpenTelemetry.

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

from . import attributes as A

_meter = metrics.get_meter("argus-semconv", A.SEMCONV_VERSION)

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


def record_ttft(*, provider: str, model: str | None, seconds: float, backend_id: str | None = None) -> None:
    attrs = _base("chat", provider, model)
    attrs.pop(A.GEN_AI_OPERATION_NAME, None)
    if backend_id:
        attrs[A.ARGUS_BACKEND_ID] = backend_id
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
    attrs = {
        A.ARGUS_APP: app or "unknown",
        A.ARGUS_FEATURE: feature or "unknown",
        A.ARGUS_USE_CASE: use_case or "unknown",
    }
    if model:
        attrs[A.GEN_AI_REQUEST_MODEL] = model
    try:
        _coste.add(cost_usd, attrs)
    except Exception:  # noqa: BLE001
        pass
