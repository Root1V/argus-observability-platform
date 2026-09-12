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
from .sinks import ConsoleSink, JSONSink, MemorySink, Sink

log = logging.getLogger("alert_bus")

SINK_FACTORIES = {
    "console": ConsoleSink,
    "json": JSONSink,
    "memory": MemorySink,
}


def build_sinks(names: list[str]) -> list[Sink]:
    salidas: list[Sink] = []
    for nombre in names:
        factory = SINK_FACTORIES.get(nombre)
        if factory is None:
            log.warning("sink.unknown", extra={"sink": nombre})
            continue
        salidas.append(factory())
    return salidas


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()

    if engine is None:
        registry = Registry(settings.registry_path)
        engine = Engine(
            registry,
            build_sinks(settings.sink_names()),
            group_window_s=settings.group_window_s,
            resolve_after_s=settings.resolve_after_s,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tarea = asyncio.create_task(_ticker(engine, settings.tick_s))
        yield
        tarea.cancel()

    app = FastAPI(
        title="Argus alert-bus",
        description="Receptor OTLP del camino caliente",
        version="0.1.0",
        lifespan=lifespan,
    )
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
        payload: dict[str, Any],
        background: BackgroundTasks,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
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
        return {**engine.stats, "incidentes_abiertos": len(engine.open_incidents())}

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
