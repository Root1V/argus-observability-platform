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
import warnings
from dataclasses import dataclass, field
from typing import Final, Literal

from argus_semconv import attributes as A

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



# ---------------------------------------------------------------------------
# Dos comprobaciones de arranque. Las dos existen porque un equipo perdio una
# tarde con ellas, y las dos fallaban EN SILENCIO: la aplicacion funciona, el
# exportador reintenta de fondo, y no llega un solo span. No hay excepcion ni
# log de error, solo ausencia — que es justo lo que nadie mira despues de
# montar el trazado (D-096).
#
# Avisan, no levantan. El contrato es no tumbar nunca la aplicacion; lo que se
# corrige es que el fallo sea invisible, no que sea fatal.
# ---------------------------------------------------------------------------

# 4317 es gRPC y 4318 es HTTP. No es convencion nuestra, es la del estandar.
_PUERTO_DE = {"grpc": "4317", "http/protobuf": "4318", "http/json": "4318"}


def _en_contenedor() -> bool:
    """Heuristica, y basta con que lo sea.

    Un falso positivo cuesta un aviso de mas; un falso negativo devuelve el
    fallo silencioso que esto viene a quitar.
    """
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as f:
            return any(m in f.read() for m in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


def _avisar_de_la_configuracion(endpoint: str, protocol: str) -> None:
    # `http://otel-collector:4318/v1/traces` -> ("otel-collector", "4318")
    autoridad = endpoint.rsplit("//", 1)[-1].split("/")[0]
    host, _, puerto = autoridad.rpartition(":")
    if not host:                      # sin puerto: "otel-collector"
        host, puerto = autoridad, ""

    # 1. `localhost` desde dentro de un contenedor.
    #
    # La convencion "las aplicaciones exportan a localhost" es correcta y se
    # lee en el sitio equivocado: el agente publica sus puertos en el
    # 127.0.0.1 del HOST, asi que desde una red de Docker es inalcanzable.
    if host in ("localhost", "127.0.0.1", "::1", "[::1]") and _en_contenedor():
        warnings.warn(
            f"Argus: el endpoint es {endpoint!r} y este proceso parece estar en un "
            "contenedor. `localhost` ahi es el propio contenedor, no el host donde "
            "corre el Collector agente: no llegara ni un span, y sin ningun error. "
            "Usa la direccion del host (`host.docker.internal` en Docker Desktop) "
            "o pon el agente en la misma red.",
            RuntimeWarning,
            stacklevel=3,
        )

    # 2. Protocolo resuelto contra puerto configurado.
    #
    # El protocolo se autodetecta segun lo instalado. Una imagen que arrastre
    # gRPC por otra dependencia elige gRPC y lo habla contra el 4318, que es
    # HTTP: `StatusCode.UNAVAILABLE` en bucle de reintentos, para las tres
    # senales.
    # Solo si el puerto es el OTRO puerto OTLP. Un 8080 o un 14318 es una
    # eleccion deliberada —un proxy, un sidecar, un mapeo del host— y avisar
    # ahi seria ruido en todos para proteger de ninguno.
    esperado = _PUERTO_DE.get(protocol)
    otros = [p for p, n in _PUERTO_DE.items() if n == puerto]
    if puerto and esperado and puerto != esperado and puerto in set(_PUERTO_DE.values()):
        warnings.warn(
            f"Argus: protocolo {protocol!r} contra el puerto {puerto}, que es el de "
            f"{' o '.join(otros) if otros else 'otro transporte'} ({protocol} usa el "
            f"{esperado}). Si no lo has elegido a proposito, el protocolo se "
            f"autodetecto por los paquetes instalados: fija ARGUS_PROTOCOL.",
            RuntimeWarning,
            stacklevel=3,
        )


def _avisar_del_entorno(valor: str) -> None:
    """`deployment.environment.name` tiene vocabulario CERRADO, y es del estandar.

    No lo cerramos nosotros: OTel ya lo fija en production/staging/test/
    development. Aqui solo se comprueba, que es la diferencia entre tener un
    vocabulario y tenerlo de verdad.

    Existe porque derivo a cuatro valores entre cuatro emisores —`mac-dev`,
    `local`, `local` y `bare-metal`— y ninguno era del estandar. El motivo no
    fue descuido: el campo se estaba usando para responder "¿en que MAQUINA?",
    que es `host.name` y no esto. Y el primer valor inventado lo escribimos
    nosotros en el plan (D-106).

    AVISA y no levanta, y el valor se conserva: un entorno mal escrito es un
    dato peor, no un motivo para no arrancar.
    """
    if not valor:
        return
    if valor in A.DEPLOYMENT_ENVIRONMENT_NAME_VALUES:
        return

    pista = ""
    if valor.lower() in ("local", "dev", "mac-dev", "laptop", "bare-metal"):
        pista = " Si lo que querias decir es en que maquina corre, eso es `host.name` y lo pone el agente."
    warnings.warn(
        f"Argus: deployment.environment.name={valor!r} no esta en el vocabulario "
        f"del estandar ({', '.join(A.DEPLOYMENT_ENVIRONMENT_NAME_VALUES)}). Se usa "
        f"igual, pero no agrupara con el resto del portafolio.{pista}",
        RuntimeWarning,
        stacklevel=3,
    )


# `ARGUS_PROPAGATE` fue un mal nombre y lo arrastramos.
#
# Gobierna UNA cosa: si confiar en un `traceparent` que llega de fuera. No
# tiene nada que ver con `argus.propagate`, el modulo que lleva el contexto
# entre hilos, procesos y colas — y los dos se llaman igual.
#
# Lo reporto Prosodia: `init()` registraba `"propagate": "never"` justo despues
# de que ellos acabaran de adoptar `argus.propagate.Executor`, y tardaron un
# rato en separar las dos cosas. No es confusion suya; les pusimos el mismo
# nombre a dos cosas distintas (D-107).
#
# El nombre nuevo dice lo que hace. El viejo sigue valiendo porque ya esta en
# tres repositorios que no controlamos, y romperlo para arreglar una palabra
# seria cobrarles a ellos nuestro error.
_VAR_CONFIANZA = "ARGUS_TRUST_INBOUND"
_VAR_CONFIANZA_VIEJA = "ARGUS_PROPAGATE"
_MODOS_DE_CONFIANZA = ("never", "trusted", "always")


def _modo_de_confianza() -> str:
    """Resuelve el modo de confianza con el nombre nuevo y el viejo."""
    nuevo = (os.getenv(_VAR_CONFIANZA) or "").strip().lower()
    viejo = (os.getenv(_VAR_CONFIANZA_VIEJA) or "").strip().lower()

    # Dos nombres puestos y en desacuerdo: avisar en vez de elegir callando.
    # Un desacuerdo silencioso aqui decide si se adopta un `traceparent`
    # ajeno, que es una decision de seguridad (OWASP A03).
    if nuevo and viejo and nuevo != viejo:
        warnings.warn(
            f"Argus: {_VAR_CONFIANZA}={nuevo!r} y {_VAR_CONFIANZA_VIEJA}={viejo!r} "
            f"no coinciden. Gana {_VAR_CONFIANZA}. Son la misma opcion y "
            f"{_VAR_CONFIANZA_VIEJA} es el nombre viejo: borra uno de los dos.",
            RuntimeWarning,
            stacklevel=4,
        )

    elegido = nuevo or viejo or "never"
    if elegido not in _MODOS_DE_CONFIANZA:
        warnings.warn(
            f"Argus: modo de confianza {elegido!r} desconocido; se usa 'never'. "
            f"Validos: {', '.join(_MODOS_DE_CONFIANZA)}.",
            RuntimeWarning,
            stacklevel=4,
        )
        return "never"
    return elegido

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
    # El modo de confianza para un `traceparent` ENTRANTE. El campo conserva el
    # nombre viejo porque lo leen el middleware ASGI y los tests; `trust_inbound`
    # de abajo es el nombre que decimos hacia fuera (D-107).
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

    @property
    def trust_inbound(self) -> TrustMode:
        """Si se adopta un `traceparent` que llega de fuera.

        Es el mismo valor que `propagate`, con el nombre que no se confunde
        con el modulo `argus.propagate`. Se lee igual desde los dos sitios; lo
        que cambia es cual se escribe en documentacion y en los logs.
        """
        return self.propagate

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

        propagate = _modo_de_confianza()

        cidrs = tuple(c.strip() for c in os.getenv("ARGUS_TRUSTED_CIDRS", "").split(",") if c.strip())

        _avisar_de_la_configuracion(endpoint, protocol)
        _avisar_del_entorno(os.getenv("ARGUS_ENVIRONMENT") or os.getenv("DEPLOYMENT_ENVIRONMENT", ""))

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
