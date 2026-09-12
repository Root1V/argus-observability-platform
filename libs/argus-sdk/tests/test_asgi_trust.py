"""Modos de confianza del middleware ASGI.

El problema: adoptar `traceparent` es lo que hace que una traza cruce
servicios, y a la vez es una superficie de ataque si el servicio esta expuesto.
La respuesta habitual (desactivarlo) deja sin trazas distribuidas. La correcta
es que la confianza sea de RED.
"""

from __future__ import annotations

from argus.asgi import ASGIMiddleware

REMOTE_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{REMOTE_TRACE_ID}-00f067aa0ba902b7-01"

# El TracerProvider y el exportador vienen del conftest raiz: solo puede haber
# un proveedor global por proceso.


async def _call(middleware: ASGIMiddleware, *, client_ip: str, headers: list[tuple[bytes, bytes]], status: int = 200):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/orders",
        "scheme": "http",
        "server": ("localhost", 8000),
        "client": (client_ip, 51234),
        "headers": headers,
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware.app = app
    await middleware(scope, receive, send)
    return sent


async def test_never_mode_ignores_incoming_traceparent(exporter) -> None:
    """Para lo expuesto a internet: nadie de fuera decide tu trace_id."""
    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="never")
    await _call(mw, client_ip="203.0.113.9", headers=[(b"traceparent", TRACEPARENT.encode())])

    span = exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") != REMOTE_TRACE_ID
    assert span.parent is None


async def test_trusted_mode_adopts_from_internal_cidr(exporter) -> None:
    """El caso normal: tus apps hablan entre ellas dentro de tu red."""
    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="trusted", trusted_cidrs=["100.64.0.0/10"])
    await _call(mw, client_ip="100.100.1.5", headers=[(b"traceparent", TRACEPARENT.encode())])

    span = exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") == REMOTE_TRACE_ID


async def test_trusted_mode_rejects_from_outside(exporter) -> None:
    """La prueba negativa: la garantia de seguridad sigue en pie."""
    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="trusted", trusted_cidrs=["100.64.0.0/10"])
    await _call(mw, client_ip="203.0.113.9", headers=[(b"traceparent", TRACEPARENT.encode())])

    span = exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") != REMOTE_TRACE_ID


async def test_trusted_mode_always_trusts_loopback(exporter) -> None:
    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="trusted", trusted_cidrs=[])
    await _call(mw, client_ip="127.0.0.1", headers=[(b"traceparent", TRACEPARENT.encode())])

    assert format(exporter.get_finished_spans()[0].context.trace_id, "032x") == REMOTE_TRACE_ID


async def test_span_uses_stable_http_semconv(exporter) -> None:
    """`http.method` y `http.status_code` estan obsoletos desde 1.23."""
    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="never")
    await _call(mw, client_ip="127.0.0.1", headers=[])

    span = exporter.get_finished_spans()[0]
    assert span.name == "GET /orders"
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["http.response.status_code"] == 200
    assert "http.method" not in span.attributes


async def test_5xx_marks_span_hot_for_realtime_path(exporter) -> None:
    """Un 5xx marca `argus.hot`.

    Es lo que el filtro del Collector agente usa para enrutar al camino
    caliente de deteccion, sin esperar al tail sampling del gateway.
    """
    from argus_semconv import attributes as A

    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="never")
    await _call(mw, client_ip="127.0.0.1", headers=[], status=503)

    attrs = exporter.get_finished_spans()[0].attributes
    assert attrs[A.ARGUS_HOT] is True
    assert attrs["error.type"] == "http-503"


async def test_health_endpoints_are_not_traced(exporter) -> None:
    """Las sondas de salud dominarian el volumen sin aportar nada."""
    exporter.clear()
    mw = ASGIMiddleware(None, propagate_mode="never")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        return None

    mw.app = app
    await mw(
        {"type": "http", "method": "GET", "path": "/health", "headers": [], "client": ("127.0.0.1", 1)},
        receive,
        send,
    )
    assert exporter.get_finished_spans() == ()
