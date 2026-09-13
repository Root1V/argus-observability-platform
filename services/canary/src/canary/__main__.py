"""Arranque: `python -m canary`."""

from __future__ import annotations

import asyncio
import logging

from .build import cargar
from .config import Settings
from .runner import Runner


async def main_async() -> None:
    settings = Settings()
    sondas = cargar(settings)

    if not sondas:
        logging.getLogger("canary").error(
            "canary.no_probes",
            extra={"registro": str(settings.registry_path), "sondas": str(settings.probes_path)},
        )

    runner = Runner(
        sondas,
        alertbus_url=settings.alertbus_url,
        token=settings.token,
        intervalo_s=settings.intervalo_s,
        fallos_para_alertar=settings.fallos_para_alertar,
    )
    await runner.bucle()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # El canario se observa a si mismo. Si deja de emitir, su propio silencio es
    # detectable por el resto del sistema.
    try:
        import argus

        argus.init("canary", namespace="argus", role="scheduler", version="0.1.0")
    except Exception:  # noqa: BLE001
        logging.getLogger("canary").warning("argus no disponible; sin auto-telemetria")

    asyncio.run(main_async())


if __name__ == "__main__":
    main()
