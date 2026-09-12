"""Arranque: `python -m alert_bus`."""

from __future__ import annotations

import logging

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # El alert-bus se observa a si mismo: es un servicio mas del portafolio y
    # no tendria ninguna gracia que la pieza que detecta incidentes fuera la
    # unica sin telemetria.
    try:
        import argus

        argus.init("alert-bus", namespace="argus", role="api", version="0.1.0")
    except Exception:  # noqa: BLE001
        logging.getLogger("alert_bus").warning("argus no disponible; sin auto-telemetria")

    uvicorn.run(
        "alert_bus.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,   # el middleware de argus ya traza cada peticion
    )


if __name__ == "__main__":
    main()
