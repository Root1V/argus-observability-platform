"""Argus SDK — observabilidad para APLICACIONES.

    import argus
    argus.init()

Eso configura resource, trazas, metricas, logs, propagadores y todas las
auto-instrumentaciones disponibles, leyendo la configuracion del entorno.

Si escribes una LIBRERIA y no una aplicacion, no uses este paquete: usa
`argus-semconv`, que depende solo de la API de OpenTelemetry y por tanto no
impone un SDK ni exportadores a quien te importe.

CONTRATO DE DISENO (verificado por tests, no por buenas intenciones):

1. Nunca tumba la aplicacion. Si algo falla al inicializar, se degrada a no-op
   con un aviso. Las colas son acotadas y descartan al llenarse: si el Collector
   cae, la app pierde telemetria, nunca latencia ni memoria.
2. Idempotente. `init()` dos veces es no-op la segunda.
3. No-op si no esta configurada. Los decoradores funcionan con coste cero.
4. Cero configuracion en el caso normal. Los argumentos son para sobrescribir.
5. Superficie publica minima, fijada por test.
"""

from __future__ import annotations

import atexit
import logging
import warnings
from dataclasses import dataclass
from typing import Any

# Re-exportamos la superficie de convenciones para que una aplicacion solo
# tenga que importar `argus`.
from argus_semconv import (
    GenAISpan,
    Step,
    agent,
    attributes,
    capture_enabled,
    documents,
    genai,
    instrument,
    mask,
    retrieval,
    step,
    tool,
)

from . import autoinst, propagate
from ._config import Config, TrustMode
from ._logging import configure_logging, emit_wide_event
from ._metrics import GenAIMetrics, configure_metrics
from ._resource import build_resource
from ._tracing import configure_tracing
from .asgi import ASGIMiddleware

# De los metadatos del paquete instalado, no de una constante: una constante se
# queda obsoleta en cuanto se publica una version y nadie se entera.
try:
    from importlib.metadata import version as _version

    __version__ = _version("argus-obs-sdk")
except Exception:  # noqa: BLE001 - sin instalar (ejecucion desde el arbol)
    __version__ = "0.0.0.dev0"

_HANDLE: Argus | None = None
_log = logging.getLogger("argus")


@dataclass(slots=True)
class Argus:
    """Handle devuelto por `init()`.

    Devolver un handle en vez de `None` no es un capricho: los procesos cortos
    (CLIs, activities, trabajos por lotes) necesitan `force_flush()` antes de
    salir, o la telemetria muere con el proceso sin exportarse.
    """

    config: Config
    tracer_provider: Any = None
    meter_provider: Any = None
    instrumented: tuple[str, ...] = ()
    genai_metrics: GenAIMetrics | None = None

    def force_flush(self, timeout_millis: int = 5_000) -> None:
        for provider in (self.tracer_provider, self.meter_provider):
            if provider is None:
                continue
            try:
                provider.force_flush(timeout_millis)
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        self.force_flush()
        for provider in (self.tracer_provider, self.meter_provider):
            if provider is None:
                continue
            try:
                provider.shutdown()
            except Exception:  # noqa: BLE001
                pass


def init(
    service: str | None = None,
    *,
    namespace: str | None = None,
    version: str | None = None,
    role: str | None = None,
    environment: str | None = None,
    endpoint: str | None = None,
    propagate_mode: TrustMode | None = None,
    slo_ms: int | None = None,
    resource_attributes: dict[str, object] | None = None,
    logging_level: int | str | None = None,
    json_logs: bool = True,
    auto_instrument: bool = True,
    skip_instrumentation: frozenset[str] = frozenset(),
    disabled: bool | None = None,
) -> Argus:
    """Inicializa la telemetria. Idempotente y a prueba de fallos.

    Todos los argumentos son opcionales: el caso normal es `argus.init()` y que
    la configuracion venga del entorno, que es lo que permite configurar un
    componente Python y uno Go copiando el mismo bloque de variables.
    """
    global _HANDLE

    if _HANDLE is not None:
        warnings.warn(
            "argus.init() ya se habia llamado en este proceso. La segunda "
            "llamada es un no-op.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _HANDLE

    try:
        cfg = Config.from_env(
            service,
            namespace=namespace,
            version=version,
            role=role,
            environment=environment,
            endpoint=endpoint,
            propagate=propagate_mode,
            slo_ms=slo_ms,
            disabled=disabled,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"Argus: configuracion invalida ({exc}). Telemetria desactivada.", RuntimeWarning, stacklevel=2)
        _HANDLE = Argus(config=Config(service=service or "unknown-service", disabled=True))
        return _HANDLE

    handle = Argus(config=cfg)

    try:
        resource = build_resource(cfg, resource_attributes)
        handle.tracer_provider = configure_tracing(cfg, resource)
        handle.meter_provider = configure_metrics(cfg, resource)
        configure_logging(cfg, level=logging_level, force_json=json_logs)

        if handle.meter_provider is not None:
            handle.genai_metrics = GenAIMetrics()

        if auto_instrument:
            activated = autoinst.activate(skip=skip_instrumentation)
            activated += autoinst.activate_genai(skip=skip_instrumentation)
            autoinst.run_hooks()
            handle.instrumented = tuple(activated)

    except Exception as exc:  # noqa: BLE001 - regla 1: nunca tumbar la app
        warnings.warn(
            f"Argus: la inicializacion fallo ({exc}). La aplicacion continua "
            "sin telemetria.",
            RuntimeWarning,
            stacklevel=2,
        )

    atexit.register(handle.shutdown)
    _HANDLE = handle

    _log.info(
        "argus.init",
        extra={
            "app": cfg.namespace,
            "service": cfg.service,
            "role": cfg.role,
            "environment": cfg.environment,
            "endpoint": cfg.endpoint if not cfg.disabled else "disabled",
            "propagate": cfg.propagate,
            "instrumented": ",".join(handle.instrumented),
        },
    )
    return handle


def handle() -> Argus | None:
    """El handle actual, si `init()` ya se llamo."""
    return _HANDLE


def middleware(app: Any, **kwargs: Any) -> ASGIMiddleware:
    """Envuelve una app ASGI heredando la configuracion de `init()`."""
    cfg = _HANDLE.config if _HANDLE else Config.from_env()
    kwargs.setdefault("service", cfg.service)
    kwargs.setdefault("propagate_mode", cfg.propagate)
    kwargs.setdefault("trusted_cidrs", cfg.trusted_cidrs)
    kwargs.setdefault("slo_ms", cfg.slo_ms)
    return ASGIMiddleware(app, **kwargs)


def _reset_for_tests() -> None:
    global _HANDLE
    from . import _logging

    _logging.reset_for_tests()
    _HANDLE = None


__all__ = [
    # Arranque
    "init",
    "handle",
    "Argus",
    "Config",
    # ASGI
    "ASGIMiddleware",
    "middleware",
    # Convenciones (re-exportadas de argus-semconv)
    "genai",
    "retrieval",
    "tool",
    "agent",
    "documents",
    "step",
    "instrument",
    "GenAISpan",
    "Step",
    "attributes",
    "capture_enabled",
    "mask",
    # Propagacion fuera de HTTP
    "propagate",
    "autoinst",
    # Utilidades
    "emit_wide_event",
    "GenAIMetrics",
    "__version__",
]
