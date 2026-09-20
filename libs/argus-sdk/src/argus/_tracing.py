"""Trazas: proveedor, exportador, propagador y muestreo.

Dos decisiones que conviene entender:

1. Propagador COMPUESTO (tracecontext + baggage), identico en los cinco
   lenguajes. Si un servicio usa W3C y otro no, la traza se parte y nadie se
   entera: no hay error, solo dos trazas donde deberia haber una.

2. Muestreo `ParentBased(ALWAYS_ON)` por defecto. El muestreo REAL se hace
   tail-based en el Collector gateway, no head-based en los procesos. Muestrear
   en origen significa decidir si guardas una traza antes de saber si fallo,
   que es exactamente al reves de lo que quieres.
"""

from __future__ import annotations

import warnings
from typing import Any

from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from ._config import Config


def _build_exporter(cfg: Config) -> Any:
    """Construye el exportador OTLP segun el transporte resuelto.

    gRPC cuando esta disponible: menor sobrecoste y latencia, y eso importa
    para el camino caliente de deteccion. JSON cuando el proceso no puede
    cargar protobuf, que es un caso real y no una hipotesis (D-077).
    """
    if cfg.protocol == "http/json":
        from ._otlp_json import ExportadorTrazasJSON

        return ExportadorTrazasJSON(cfg.endpoint, cfg.headers or None)

    if cfg.protocol == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPExporter

        endpoint = cfg.endpoint.rstrip("/")
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        return HTTPExporter(endpoint=endpoint, headers=cfg.headers or None)

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCExporter

    return GRPCExporter(
        endpoint=cfg.endpoint,
        # Las claves de metadatos de gRPC DEBEN ir en minuscula: el protocolo
        # rechaza "Authorization" con "Illegal header key". Es un detalle que
        # el SDK absorbe, porque quien escribe OTEL_EXPORTER_OTLP_HEADERS no
        # tiene por que saberlo.
        headers=tuple((k.lower(), v) for k, v in cfg.headers.items()) if cfg.headers else None,
        insecure=cfg.endpoint.startswith("http://"),
    )


def configure_tracing(cfg: Config, resource: Any) -> TracerProvider | None:
    """Configura el proveedor de trazas global.

    Devuelve `None` si ya habia uno configurado: en ese caso NO lo pisamos.
    Esa coexistencia es lo que permite migrar una app gradualmente, enviando a
    su backend viejo y al nuevo a la vez, y cortar cuando el equipo decida en
    vez de cuando el SDK lo imponga.
    """
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        warnings.warn(
            "Ya habia un TracerProvider configurado. Argus no lo sustituye y "
            "solo anade su exportador, para permitir migraciones graduales.",
            RuntimeWarning,
            stacklevel=3,
        )
        _attach_processor(existing, cfg)
        return None

    provider = TracerProvider(resource=resource, sampler=ParentBased(ALWAYS_ON))
    _attach_processor(provider, cfg)

    if cfg.console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    # El propagador debe ser identico en todos los servicios y lenguajes.
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    return provider


def _attach_processor(provider: TracerProvider, cfg: Config) -> None:
    if cfg.disabled:
        return
    try:
        exporter = _build_exporter(cfg)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"Argus no pudo crear el exportador de trazas: {exc}", RuntimeWarning, stacklevel=3)
        return

    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            # Lote pequeno a proposito: es el presupuesto del camino caliente.
            schedule_delay_millis=cfg.schedule_delay_ms,
            # Cola ACOTADA con descarte al llenarse. Si el Collector cae, la
            # aplicacion pierde telemetria, nunca latencia ni memoria.
            max_queue_size=cfg.max_queue_size,
            max_export_batch_size=min(512, cfg.max_queue_size),
        )
    )
