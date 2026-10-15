"""Middleware ASGI con modos de confianza para la propagacion.

El problema que resuelve: adoptar el `traceparent` entrante es lo que hace que
una traza cruce servicios, y a la vez es una superficie de ataque si el
servicio esta expuesto a internet (un tercero puede inyectar identificadores de
traza arbitrarios y contaminar tu telemetria, u OWASP A03).

La respuesta habitual es desactivar la propagacion por completo, y entonces
nunca tienes trazas distribuidas. La respuesta correcta es que la confianza sea
de RED, no de identidad:

- `never`   : arranca siempre una traza nueva. Para lo expuesto a internet.
- `trusted` : adopta `traceparent` solo desde CIDRs internos. El caso normal.
- `always`  : adopta siempre. Solo para redes privadas.

`trusted` es el que permite que tus aplicaciones, que hablan entre ellas dentro
de tu red, tengan trazas completas sin abrir la puerta al exterior.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

from argus_semconv import attributes as A
from opentelemetry import context, propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from . import _scope
from ._config import TrustMode

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

# Rutas que no generan traza: sondas de salud y scrape de metricas dominarian
# el volumen sin aportar nada.
DEFAULT_EXCLUDED = frozenset({"/health", "/healthz", "/readyz", "/livez", "/metrics"})


class ASGIMiddleware:
    """Traza peticiones ASGI respetando el modo de confianza configurado."""

    def __init__(
        self,
        app: Any,
        *,
        service: str | None = None,
        propagate_mode: TrustMode = "never",
        trusted_cidrs: Sequence[str] = (),
        excluded_paths: Iterable[str] | None = None,
        slo_ms: int = 0,
    ) -> None:
        self.app = app
        self.service = service
        self.mode: TrustMode = propagate_mode
        self.networks = tuple(_parse_networks(trusted_cidrs))
        self.excluded = frozenset(excluded_paths) if excluded_paths is not None else DEFAULT_EXCLUDED
        self.slo_ms = slo_ms
        self._tracer = trace.get_tracer(
            "argus-sdk",
            _scope.version_paquete(),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.excluded:
            await self.app(scope, receive, send)
            return

        token = None
        if self._should_adopt(scope):
            headers = _headers_to_dict(scope.get("headers") or [])
            parent = propagate.extract(headers)
            token = context.attach(parent)

        method = scope.get("method", "GET")
        # Provisional: la PLANTILLA de ruta no existe todavia.
        #
        # El router no ha corrido aun —este middleware envuelve la aplicacion—
        # asi que aqui solo hay el path resuelto. La plantilla se pone al
        # cerrar, en `_renombrar_con_la_plantilla`.
        #
        # Hasta hoy esta linea decia `scope.get("route_path") or path`, y
        # `route_path` NO ES UNA CLAVE DE ASGI NI DE STARLETTE: aparecia una
        # sola vez en todo el repositorio, en la linea que la leia. Asi que
        # siempre caia al path resuelto.
        #
        # Con una ruta estatica el path resuelto ES la plantilla, asi que
        # parecia correcto. Solo fallaba en las rutas con parametro, que son
        # las que importan (D-117).
        route = path
        name = f"{method} {route}"

        status_holder: dict[str, int] = {}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 0))
            await send(message)

        try:
            with self._tracer.start_as_current_span(name, kind=SpanKind.SERVER) as span:
                _set_http_attributes(span, scope, method, route, path)
                try:
                    await self.app(scope, receive, send_wrapper)
                except Exception as exc:
                    span.set_attribute(A.ERROR_TYPE, type(exc).__name__)
                    span.set_attribute(A.ARGUS_HOT, True)
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    raise
                finally:
                    _renombrar_con_la_plantilla(span, scope, method)
                    self._finalize(span, status_holder.get("status"))
        finally:
            if token is not None:
                context.detach(token)

    def _finalize(self, span: trace.Span, status: int | None) -> None:
        if status is None:
            return
        span.set_attribute("http.response.status_code", status)
        if status >= 500:
            # 5xx marca el span como candidato a incidente. El filtro del
            # Collector agente lo enruta al camino caliente de deteccion,
            # sin esperar al tail sampling del gateway.
            span.set_attribute(A.ERROR_TYPE, f"http-{status}")
            span.set_attribute(A.ARGUS_HOT, True)
            span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))

    def _should_adopt(self, scope: Scope) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False

        client = scope.get("client")
        if not client:
            return False
        try:
            address = ipaddress.ip_address(client[0])
        except ValueError:
            return False

        # Loopback siempre es de confianza: es el Collector agente local o un
        # proceso de la misma maquina.
        if address.is_loopback:
            return True
        return any(address in network for network in self.networks)


def _parse_networks(cidrs: Sequence[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return networks


def _headers_to_dict(raw: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in raw}


def _renombrar_con_la_plantilla(span: trace.Span, scope: Scope, method: str) -> None:
    """Pone `http.route` y el nombre del span con la PLANTILLA de la ruta.

    Starlette expone la ruta resuelta en `scope["route"]`, y eso solo existe
    DESPUES de que el router haya corrido — o sea, no cuando este middleware
    abre el span, sino cuando lo cierra.

    Por que importa, medido sobre trafico real de Veritium: su
    `POST /v1/cases/{case_id}/reprocess` llegaba al almacen con el UUID del
    caso dentro, en `http.route` **y en el nombre del span**. Eso rompe tres
    cosas a la vez, y las tres en silencio:

      - `GROUP BY http.route` da una fila por caso, o sea ninguna respuesta.
      - `spanmetrics` deriva una serie por caso: cardinalidad sin techo en el
        almacen de metricas.
      - Y nuestra propia huella de error incluye el nombre del span, asi que
        cada expediente que fallara tendria una huella distinta — justo lo que
        `argus.error.fingerprint` existe para evitar.

    Es la tercera cosa que se agrupa por un identificador sin querer: antes
    fueron el cursor del long-poll de Prometheus y sus rutas de `{action}`.

    No levanta nunca: corre en el camino de la peticion.
    """
    try:
        objeto = scope.get("route")
        plantilla = getattr(objeto, "path", None)
        if not plantilla or not isinstance(plantilla, str):
            return
        span.set_attribute("http.route", plantilla)
        span.update_name(f"{method} {plantilla}")
    except Exception:  # noqa: BLE001
        return


def _set_http_attributes(span: trace.Span, scope: Scope, method: str, route: str, path: str) -> None:
    # Semconv HTTP estable. Los nombres antiguos (`http.method`,
    # `http.status_code`) estan obsoletos desde la estabilizacion de 1.23.
    span.set_attribute("http.request.method", method)
    span.set_attribute("http.route", route)
    span.set_attribute("url.path", path)
    if scheme := scope.get("scheme"):
        span.set_attribute("url.scheme", scheme)
    if server := scope.get("server"):
        span.set_attribute("server.address", str(server[0]))
        if len(server) > 1 and server[1]:
            span.set_attribute("server.port", int(server[1]))
    if client := scope.get("client"):
        span.set_attribute("client.address", str(client[0]))
