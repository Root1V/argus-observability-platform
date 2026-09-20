"""Configuracion por entorno.

El contrato de configuracion es de VARIABLES DE ENTORNO, no de una API de
Python. Es lo que permite que un componente Go, uno TypeScript y uno Python se
configuren copiando el mismo bloque, y que anadir un componente nuevo no
requiera aprender una libreria.

Precedencia (gana el primero): argumento explicito > variable ARGUS_* >
variable OTEL_* estandar > valor por defecto.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import uuid
from dataclasses import dataclass, field
from typing import Final, Literal

TrustMode = Literal["never", "trusted", "always"]

_TRUE: Final = frozenset({"1", "true", "yes", "on"})

# Las aplicaciones exportan SIEMPRE al Collector agente local, nunca al plano
# central. Es lo que permite mover el plano central de una maquina a otra sin
# tocar ni una aplicacion, y lo que hace que una app no se entere de que el
# central esta suspendido.
DEFAULT_ENDPOINT: Final = "http://localhost:4317"
DEFAULT_ENDPOINT_HTTP: Final = "http://localhost:4318"

Protocolo = Literal["grpc", "http/protobuf", "http/json"]


def _disponible(modulo: str) -> bool:
    """Si el paquete esta instalado, sin importarlo."""
    try:
        return importlib.util.find_spec(modulo) is not None
    except (ImportError, ValueError):
        return False


def _protocolo_por_defecto() -> Protocolo:
    """Elige transporte segun lo que este instalado.

    Los exportadores oficiales de OTLP —gRPC y HTTP por igual— arrastran
    `opentelemetry-proto`, que exige `protobuf>=5.0`. Hay aplicaciones reales
    que no pueden declarar ese rango (D-077), asi que desde 1.0.0a4 esos
    exportadores viven en extras y el nucleo no depende de protobuf.

    El orden preserva el comportamiento de quien ya tiene el extra instalado:
    gRPC si esta, HTTP/protobuf si esta, y JSON —que no necesita nada— si no.
    """
    if _disponible("opentelemetry.exporter.otlp.proto.grpc"):
        return "grpc"
    if _disponible("opentelemetry.exporter.otlp.proto.http"):
        return "http/protobuf"
    return "http/json"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(slots=True)
class Config:
    """Configuracion efectiva de un componente."""

    # --- Identidad de dos niveles -------------------------------------------
    # `namespace` es la APLICACION y `service` el SUB-COMPONENTE. Sin esa
    # separacion acabas con decenas de servicios planos sin forma de
    # agruparlos por aplicacion, y el problema empeora con cada app nueva.
    service: str
    namespace: str = ""
    version: str = ""
    instance_id: str = ""
    role: str = "api"

    # Atributos que salieron de una eleccion real (argumento o ARGUS_*), frente
    # a los que rellenamos nosotros. Lo rellena `from_env`.
    elegidos: set[str] = field(default_factory=set)
    environment: str = ""

    # --- Transporte ----------------------------------------------------------
    endpoint: str = ""
    protocol: Protocolo = "grpc"
    headers: dict[str, str] = field(default_factory=dict)

    # --- Comportamiento ------------------------------------------------------
    propagate: TrustMode = "never"
    trusted_cidrs: tuple[str, ...] = ()
    capture_content: bool = False
    # Presupuesto del camino caliente: con 200 ms, un error llega al alert-bus
    # en ~2 s de punta a punta. El volumen es bajo porque los errores son la
    # excepcion, asi que el coste de lotes pequenos es asumible.
    schedule_delay_ms: int = 200
    max_queue_size: int = 2048
    metric_interval_ms: int = 15_000
    slo_ms: int = 0

    disabled: bool = False
    console: bool = False

    @classmethod
    def from_env(cls, service: str | None = None, **overrides: object) -> Config:
        resolved_service = (
            service
            or os.getenv("ARGUS_SERVICE")
            or os.getenv("OTEL_SERVICE_NAME")
            or "unknown-service"
        )

        protocol = os.getenv("ARGUS_PROTOCOL") or os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") or ""
        if protocol not in ("grpc", "http/protobuf", "http/json"):
            protocol = _protocolo_por_defecto()

        # El puerto por defecto depende del transporte: 4317 es gRPC y 4318 es
        # HTTP. Heredar el de gRPC al caer a JSON seria degradar a un
        # exportador que funciona apuntando a un puerto que no contesta.
        endpoint = (
            os.getenv("ARGUS_ENDPOINT")
            or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
            or (DEFAULT_ENDPOINT if protocol == "grpc" else DEFAULT_ENDPOINT_HTTP)
        )

        propagate = (os.getenv("ARGUS_PROPAGATE") or "never").strip().lower()
        if propagate not in ("never", "trusted", "always"):
            propagate = "never"

        cidrs = tuple(c.strip() for c in os.getenv("ARGUS_TRUSTED_CIDRS", "").split(",") if c.strip())

        cfg = cls(
            service=resolved_service,
            namespace=os.getenv("ARGUS_NAMESPACE", ""),
            version=os.getenv("ARGUS_VERSION") or os.getenv("SERVICE_VERSION", ""),
            instance_id=os.getenv("ARGUS_INSTANCE_ID") or os.getenv("HOSTNAME", ""),
            role=os.getenv("ARGUS_ROLE", ""),
            environment=os.getenv("ARGUS_ENVIRONMENT") or os.getenv("DEPLOYMENT_ENVIRONMENT", ""),
            endpoint=endpoint,
            protocol=protocol,  # type: ignore[arg-type]
            headers=_parse_headers(os.getenv("ARGUS_HEADERS") or os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")),
            propagate=propagate,  # type: ignore[arg-type]
            trusted_cidrs=cidrs,
            capture_content=_flag("ARGUS_CAPTURE_CONTENT"),
            schedule_delay_ms=_int("ARGUS_SCHEDULE_DELAY_MS", 200),
            max_queue_size=_int("ARGUS_MAX_QUEUE_SIZE", 2048),
            metric_interval_ms=_int("ARGUS_METRIC_INTERVAL_MS", 15_000),
            slo_ms=_int("ARGUS_SLO_MS", 0),
            disabled=_flag("ARGUS_DISABLED"),
            console=_flag("ARGUS_CONSOLE"),
        )

        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)

        # Que atributos salieron de una eleccion REAL —argumento o variable
        # ARGUS_*— y cuales son un relleno nuestro. La diferencia decide quien
        # gana frente a OTEL_RESOURCE_ATTRIBUTES: lo elegido pisa la variable,
        # un relleno JAMAS (D-072).
        cfg.elegidos = {
            campo for campo in ("namespace", "role", "environment", "version", "instance_id")
            if getattr(cfg, campo)
        }
        if not cfg.role:
            cfg.role = "api"

        if not cfg.instance_id:
            cfg.instance_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        if not cfg.namespace:
            cfg.namespace = cfg.service
        if not cfg.environment:
            cfg.environment = "local"

        return cfg


def _parse_headers(raw: str) -> dict[str, str]:
    """Formato estandar de OTel: `k1=v1,k2=v2`."""
    out: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            out[key.strip()] = value.strip()
    return out
