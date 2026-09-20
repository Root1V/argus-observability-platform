"""Metricas: proveedor e instrumentos GenAI con los buckets de las semconv.

Los buckets no son arbitrarios: vienen fijados en el modelo de convenciones
para que los cinco lenguajes emitan histogramas comparables. Dos servicios con
buckets distintos producen series que no se pueden agregar.
"""

from __future__ import annotations

import warnings
from typing import Any

from argus_semconv import attributes as A
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

from ._config import Config


def _build_exporter(cfg: Config) -> Any:
    if cfg.protocol == "http/json":
        from ._otlp_json import ExportadorMetricasJSON

        return ExportadorMetricasJSON(cfg.endpoint, cfg.headers or None)

    if cfg.protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter as HTTPExporter

        endpoint = cfg.endpoint.rstrip("/")
        if not endpoint.endswith("/v1/metrics"):
            endpoint = f"{endpoint}/v1/metrics"
        return HTTPExporter(endpoint=endpoint, headers=cfg.headers or None)

    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GRPCExporter

    return GRPCExporter(
        endpoint=cfg.endpoint,
        # Las claves de metadatos de gRPC DEBEN ir en minuscula: el protocolo
        # rechaza "Authorization" con "Illegal header key". Es un detalle que
        # el SDK absorbe, porque quien escribe OTEL_EXPORTER_OTLP_HEADERS no
        # tiene por que saberlo.
        headers=tuple((k.lower(), v) for k, v in cfg.headers.items()) if cfg.headers else None,
        insecure=cfg.endpoint.startswith("http://"),
    )


def _views() -> list[View]:
    return [
        View(
            instrument_name=A.M_GEN_AI_CLIENT_OPERATION_DURATION,
            aggregation=ExplicitBucketHistogramAggregation(list(A.M_GEN_AI_CLIENT_OPERATION_DURATION_BUCKETS)),
        ),
        View(
            instrument_name=A.M_GEN_AI_CLIENT_TOKEN_USAGE,
            aggregation=ExplicitBucketHistogramAggregation(list(A.M_GEN_AI_CLIENT_TOKEN_USAGE_BUCKETS)),
        ),
    ]


def configure_metrics(cfg: Config, resource: Any) -> MeterProvider | None:
    if cfg.disabled:
        return None
    try:
        reader = PeriodicExportingMetricReader(
            _build_exporter(cfg), export_interval_millis=cfg.metric_interval_ms
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"Argus no pudo crear el exportador de metricas: {exc}", RuntimeWarning, stacklevel=3)
        return None

    provider = MeterProvider(resource=resource, metric_readers=[reader], views=_views())
    metrics.set_meter_provider(provider)
    return provider


class GenAIMetrics:
    """Instrumentos GenAI conformes a las semconv.

    `gen_ai.token.type` (input/output) va como DIMENSION y no como dos
    instrumentos separados. Es lo que dice la especificacion, y es lo que
    permite agregar por modelo sin unir series a mano.
    """

    __slots__ = ("_cost", "_duration", "_tokens", "_ttft")

    def __init__(self) -> None:
        meter = metrics.get_meter("argus-sdk", A.SEMCONV_VERSION)
        self._duration = meter.create_histogram(
            A.M_GEN_AI_CLIENT_OPERATION_DURATION,
            unit=A.M_GEN_AI_CLIENT_OPERATION_DURATION_UNIT,
            description="Duracion de la operacion GenAI",
        )
        self._tokens = meter.create_histogram(
            A.M_GEN_AI_CLIENT_TOKEN_USAGE,
            unit=A.M_GEN_AI_CLIENT_TOKEN_USAGE_UNIT,
            description="Tokens consumidos, con el tipo como dimension",
        )
        self._ttft = meter.create_histogram(
            A.M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN,
            unit=A.M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN_UNIT,
            description="Tiempo hasta el primer token",
        )
        self._cost = meter.create_counter(
            A.M_ARGUS_COST_USD,
            unit=A.M_ARGUS_COST_USD_UNIT,
            description="Coste atribuido, para el agente de FinOps",
        )

    def record(
        self,
        *,
        operation: str,
        provider: str,
        model: str,
        duration_s: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_type: str | None = None,
        server_address: str | None = None,
        ttft_s: float | None = None,
        cost_usd: float | None = None,
        app: str | None = None,
        feature: str | None = None,
        use_case: str | None = None,
    ) -> None:
        base = {
            A.GEN_AI_OPERATION_NAME: operation,
            A.GEN_AI_PROVIDER_NAME: provider,
            A.GEN_AI_REQUEST_MODEL: model,
        }

        duration_attrs = dict(base)
        if error_type:
            duration_attrs[A.ERROR_TYPE] = error_type
        if server_address:
            duration_attrs["server.address"] = server_address
        self._duration.record(duration_s, duration_attrs)

        if input_tokens is not None:
            self._tokens.record(input_tokens, {**base, "gen_ai.token.type": "input"})
        if output_tokens is not None:
            self._tokens.record(output_tokens, {**base, "gen_ai.token.type": "output"})

        if ttft_s is not None:
            self._ttft.record(ttft_s, base)

        if cost_usd:
            # Dimensiones pensadas para que el agente de FinOps pueda atribuir.
            # Sin estas etiquetas el coste llega como un numero opaco.
            self._cost.add(
                cost_usd,
                {
                    A.ARGUS_APP: app or "unknown",
                    A.ARGUS_FEATURE: feature or "unknown",
                    A.ARGUS_USE_CASE: use_case or "unknown",
                    A.GEN_AI_REQUEST_MODEL: model,
                },
            )
