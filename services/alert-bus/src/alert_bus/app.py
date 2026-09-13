"""API del alert-bus.

Dos entradas, con latencias muy distintas:

- `POST /v1/traces` — CAMINO CALIENTE. Spans del Collector agente, lotes de
  200 ms. Presupuesto: ~2 s del fallo al aviso. La respuesta se devuelve
  inmediatamente y el trabajo se hace en segundo plano, porque hacer esperar al
  Collector solo consigue que se le llene la cola.

- `POST /api/v2/alerts` — CAMINO TEMPLADO. Alertas de vmalert en formato
  Alertmanager: burn-rate y anomalias, que necesitan una ventana.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from . import normalize
from .config import Settings
from .engine import Engine
from .registry import Registry
from .sinks import (
    ConsoleSink,
    Dispatcher,
    EmailSink,
    GoogleChatSink,
    JSONSink,
    MemorySink,
    Sink,
    WhatsAppSink,
)

log = logging.getLogger("alert_bus")


def build_sinks(settings: Settings) -> list[Sink]:
    """Construye los canales pedidos, saltando los que no esten configurados.

    Un canal sin credenciales se OMITE con un aviso, no se construye a medias:
    un sink que falla en cada envio llena el log y da la falsa impresion de que
    la plataforma esta avisando.
    """
    salidas: list[Sink] = []

    for nombre in settings.sink_names():
        if nombre == "console":
            salidas.append(ConsoleSink())
        elif nombre == "json":
            salidas.append(JSONSink())
        elif nombre == "memory":
            salidas.append(MemorySink())
        elif nombre == "gchat":
            if not settings.gchat_webhook:
                log.warning("sink.not_configured", extra={"sink": "gchat", "falta": "ALERTBUS_GCHAT_WEBHOOK"})
                continue
            salidas.append(GoogleChatSink(settings.gchat_webhook))
        elif nombre == "email":
            if not (settings.smtp_host and settings.smtp_recipients()):
                log.warning("sink.not_configured", extra={"sink": "email", "falta": "ALERTBUS_SMTP_HOST / _SMTP_TO"})
                continue
            salidas.append(EmailSink(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                sender=settings.smtp_from,
                recipients=settings.smtp_recipients(),
                use_tls=settings.smtp_tls,
            ))
        elif nombre == "whatsapp":
            if not (settings.whatsapp_phone_id and settings.whatsapp_token and settings.whatsapp_recipients()):
                log.warning("sink.not_configured", extra={"sink": "whatsapp", "falta": "ALERTBUS_WHATSAPP_*"})
                continue
            salidas.append(WhatsAppSink(
                phone_number_id=settings.whatsapp_phone_id,
                access_token=settings.whatsapp_token,
                recipients=settings.whatsapp_recipients(),
                template=settings.whatsapp_template,
            ))
        else:
            log.warning("sink.unknown", extra={"sink": nombre})

    return salidas


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()

    dispatcher: Dispatcher | None = None

    if engine is None:
        registry = Registry(settings.registry_path)
        dispatcher = Dispatcher(
            workers=settings.dispatch_workers,
            max_queue=settings.dispatch_queue_size,
        )
        sinks = build_sinks(settings)
        log.info("sinks.ready", extra={"sinks": ",".join(s.name for s in sinks)})
        engine = Engine(
            registry,
            sinks,
            group_window_s=settings.group_window_s,
            resolve_after_s=settings.resolve_after_s,
            dispatcher=dispatcher,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if dispatcher is not None:
            dispatcher.start()
        tarea = asyncio.create_task(_ticker(engine, settings.tick_s))
        yield
        tarea.cancel()
        if dispatcher is not None:
            # Vaciar antes de parar: apagar con avisos pendientes en la cola es
            # perder justo las notificaciones del incidente que probablemente
            # provoco el apagado.
            dispatcher.drain(timeout_s=5)
            dispatcher.stop()

    app = FastAPI(
        title="Argus alert-bus",
        description="Receptor OTLP del camino caliente",
        version="0.1.0",
        lifespan=lifespan,
    )
    # El alert-bus se traza a si mismo. No es narcisismo: si la pieza que
    # detecta incidentes fuera la unica sin telemetria, su propia degradacion
    # seria invisible, y el canario no podria distinguir "esta callado" de
    # "esta muerto".
    try:
        import argus

        app.add_middleware(argus.ASGIMiddleware, service="alert-bus", propagate_mode="trusted")
    except Exception as exc:  # noqa: BLE001
        log.warning("argus.middleware_unavailable", extra={"error": str(exc)})

    app.state.engine = engine
    app.state.settings = settings

    def _check_token(authorization: str | None) -> None:
        """La red privada protege el transporte; el token la integridad.

        Importa porque los agentes leen esta telemetria y sacan conclusiones:
        aceptar senales de cualquiera es aceptar conclusiones de cualquiera.
        """
        if not settings.token:
            return
        esperado = f"Bearer {settings.token}"
        if authorization != esperado:
            raise HTTPException(status_code=401, detail="token invalido o ausente")

    # --- Camino caliente -----------------------------------------------------

    @app.post("/v1/traces")
    async def receive_traces(
        request: Request,
        background: BackgroundTasks,
        authorization: str | None = Header(default=None),
        content_type: str | None = Header(default=None),
        content_encoding: str | None = Header(default=None),
    ) -> Response:
        _check_token(authorization)
        body = await request.body()
        if not body:
            return Response(status_code=200)

        try:
            body = normalize.decompress(body, content_encoding)
            if content_type is None and normalize.looks_like_json(body):
                peticion = normalize.decode_json(body)
            else:
                peticion = normalize.parse_body(body, content_type or "")
        except Exception as exc:
            # Un lote mal formado no puede tumbar el receptor: perderiamos TODA
            # la deteccion por un cliente que envia mal.
            log.error("otlp.decode_failed", extra={"error": str(exc)})
            raise HTTPException(status_code=400, detail=f"OTLP invalido: {exc}") from exc

        # Se responde YA y se procesa despues: hacer esperar al Collector solo
        # consigue llenarle la cola de envio.
        background.add_task(_process, engine, list(normalize.signals_from_otlp(peticion)))
        return Response(status_code=200)

    # --- Camino templado -----------------------------------------------------

    @app.post("/api/v2/alerts")
    async def receive_alerts(
        payload: list[dict[str, Any]] | dict[str, Any],
        background: BackgroundTasks,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        """Camino templado: burn-rate y anomalias, que necesitan una ventana.

        El tipo de `payload` admite las dos formas a proposito: vmalert manda un
        ARRAY pelado (API v2 de Alertmanager) y los scripts suelen mandar
        `{"alerts": [...]}` (formato de webhook). Aceptar solo una deja la
        integracion real rota sin que los tests se enteren.
        """
        _check_token(authorization)
        background.add_task(_process, engine, list(normalize.signals_from_alertmanager(payload)))
        return {"status": "accepted"}

    # --- Consulta y control --------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "incidentes_abiertos": len(engine.open_incidents())}

    @app.get("/incidents")
    async def incidents() -> list[dict[str, Any]]:
        return [
            {
                **i.model_dump(mode="json"),
                "components_affected": sorted(i.components_affected),
            }
            for i in engine.open_incidents()
        ]

    @app.post("/incidents/{incident_id}/ack")
    async def acknowledge(incident_id: str) -> dict[str, Any]:
        incidente = engine.acknowledge(incident_id)
        if incidente is None:
            raise HTTPException(status_code=404, detail="incidente no encontrado")
        return {"status": "acknowledged", "id": incidente.id}

    @app.post("/incidents/{fingerprint}/enrich")
    async def enrich(fingerprint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Lo llama el agente de investigacion al terminar.

        Edita el mensaje que ya salio en vez de mandar uno nuevo: es la tercera
        fase de la divulgacion progresiva.
        """
        incidente = engine.enrich(fingerprint, **payload)
        if incidente is None:
            raise HTTPException(status_code=404, detail="incidente no encontrado")
        return {"status": "enriched", "id": incidente.id}

    @app.get("/registry")
    async def registry_snapshot() -> list[dict[str, Any]]:
        return engine._registry.snapshot()

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {
            **engine.stats,
            "incidentes_abiertos": len(engine.open_incidents()),
            "canales": [s.name for s in engine._sinks],
            "despacho": dict(dispatcher.stats) if dispatcher else {},
        }

    return app


def _process(engine: Engine, signals: list[Any]) -> None:
    if not signals:
        return
    inicio = time.perf_counter()
    notificados = engine.ingest(signals)
    if notificados:
        log.info(
            "incidents.notified",
            extra={
                "count": len(notificados),
                "signals": len(signals),
                "latency_ms": round((time.perf_counter() - inicio) * 1000, 2),
            },
        )


async def _ticker(engine: Engine, every_s: int) -> None:
    """Cierra ventanas de agrupacion y resuelve incidentes inactivos."""
    while True:
        try:
            await asyncio.sleep(every_s)
            engine.flush_grouped()
            engine.sweep()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("ticker.failed", extra={"error": str(exc)})


app = create_app()
