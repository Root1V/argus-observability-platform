"""Puente de logging y eventos anchos.

Esta es la pieza que hace barata la migracion. Una aplicacion que ya usa
`logging`, `structlog` o `colorlog` NO tiene que reescribir ni una llamada:
instalamos un handler en la raiz que capta lo que ya emite, lo correlaciona con
la traza activa y lo exporta. Los `logger.info()` existentes siguen
funcionando exactamente igual y ganan `trace_id`.

El evento ancho es una mejora posterior y opcional, no un requisito.
"""

from __future__ import annotations

import json
import logging
import sys
import warnings
from typing import Any

from argus_semconv import attributes as A
from opentelemetry import trace

from ._config import Config

_INSTALLED = False


class TraceCorrelationFilter(logging.Filter):
    """Inyecta `trace_id` y `span_id` en cada registro.

    Sin esto no se puede saltar de un log a su traza, que es la operacion mas
    frecuente durante un incidente.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = "none"
            record.span_id = "none"
        return True


class JSONFormatter(logging.Formatter):
    """Formato JSON con orden de claves estable.

    El orden fijo no es cosmetico: hace que los logs sean legibles a ojo
    cuando los miras en crudo y comprimibles cuando no.
    """

    _RESERVED = frozenset(
        {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "module", "msecs",
            "message", "msg", "name", "pathname", "process", "processName",
            "relativeCreated", "stack_info", "thread", "threadName", "taskName",
            "trace_id", "span_id",
        }
    )

    def __init__(self, service: str, namespace: str) -> None:
        super().__init__()
        self._service = service
        self._namespace = namespace

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f%z"),
            "level": record.levelname.lower(),
            "app": self._namespace,
            "service": self._service,
            "component": record.name,
            "event": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "none"),
            "span_id": getattr(record, "span_id", "none"),
        }

        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["error.type"] = record.exc_info[0].__name__ if record.exc_info[0] else "unknown"
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(cfg: Config, *, level: int | str | None = None, force_json: bool = True) -> None:
    """Instala el puente de logging.

    Idempotente: llamarlo dos veces no duplica handlers ni registros.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    root = logging.getLogger()
    if level is not None:
        root.setLevel(level if isinstance(level, int) else level.upper())
    elif root.level == logging.NOTSET:
        root.setLevel(logging.INFO)

    correlation = TraceCorrelationFilter()

    if force_json:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter(cfg.service, cfg.namespace))
        handler.addFilter(correlation)
        handler.set_name("argus")
        # Sustituimos handlers de consola previos para no duplicar salida, pero
        # respetamos los de fichero o los que el usuario haya puesto a proposito.
        root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)]
        root.addHandler(handler)
    else:
        # La app conserva su formato; solo anadimos correlacion con la traza.
        for existing in root.handlers:
            existing.addFilter(correlation)

    _bridge_to_otlp(cfg)
    _INSTALLED = True


def _bridge_to_otlp(cfg: Config) -> None:
    """Exporta tambien por OTLP, si el SDK de logs esta disponible.

    La señal de logs es la menos madura de las tres en varios SDKs, asi que si
    no esta se degrada en silencio: el log por stdout sigue funcionando y el
    Collector agente lo recoge con su receptor de ficheros.
    """
    if cfg.disabled:
        return
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        if cfg.protocol == "http/protobuf":
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

            endpoint = cfg.endpoint.rstrip("/")
            if not endpoint.endswith("/v1/logs"):
                endpoint = f"{endpoint}/v1/logs"
            exporter = OTLPLogExporter(endpoint=endpoint, headers=cfg.headers or None)
        else:
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

            exporter = OTLPLogExporter(
                endpoint=cfg.endpoint,
                # Minuscula obligatoria en metadatos gRPC.
                headers=tuple((k.lower(), v) for k, v in cfg.headers.items()) if cfg.headers else None,
                insecure=cfg.endpoint.startswith("http://"),
            )

        from ._resource import build_resource

        provider = LoggerProvider(resource=build_resource(cfg))
        provider.add_log_record_processor(
            BatchLogRecordProcessor(exporter, schedule_delay_millis=cfg.schedule_delay_ms)
        )
        set_logger_provider(provider)

        otlp_handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
        otlp_handler.addFilter(TraceCorrelationFilter())
        otlp_handler.set_name("argus-otlp")
        logging.getLogger().addHandler(otlp_handler)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(
            f"Argus: puente de logs OTLP no disponible ({exc}). Los logs siguen "
            "saliendo por stdout y el Collector agente puede recogerlos.",
            RuntimeWarning,
            stacklevel=3,
        )


def emit_wide_event(logger: logging.Logger, event: str, fields: dict[str, Any]) -> None:
    """Emite UN evento ancho por unidad de trabajo.

    El patron de `canonical log line`: en vez de decenas de lineas sueltas por
    peticion, una sola estructurada al final con todo el contexto ya adjunto.
    Como los campos son los mismos que los del span, el evento ancho ES el span
    enriquecido, no un canal paralelo que haya que correlacionar.
    """
    logger.info(event, extra={k: v for k, v in fields.items() if k != A.ARGUS_EVENT})


def reset_for_tests() -> None:
    """Solo para tests."""
    global _INSTALLED
    _INSTALLED = False
