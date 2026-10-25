"""Arranca L3: `python -m diagnose`."""

from __future__ import annotations

import logging
import sys

from .almacen import Almacen
from .bucle import Diagnosticador
from .config import Settings
from .juicio import JuezDeReglas


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("diagnose")
    settings = Settings()

    almacen = Almacen(
        settings.clickhouse_url,
        settings.clickhouse_user,
        settings.clickhouse_password,
        base_de_datos=settings.clickhouse_db,
        timeout_s=settings.timeout_s(),
    )
    # Hoy solo hay un juez. El dia que haya dos, esto elige — y el de reglas se
    # queda, porque es el control contra el que se mide al otro (F3-08).
    if settings.juez != "reglas":
        log.error("juez.desconocido", extra={"juez": settings.juez})
        return 2

    diagnosticador = Diagnosticador(settings, almacen=almacen, juez=JuezDeReglas())
    log.info(
        "diagnose.ready",
        extra={
            "alertbus": settings.alertbus_url,
            "clickhouse": settings.clickhouse_url,
            "juez": settings.juez,
            "intervalo_s": settings.intervalo_s,
        },
    )
    try:
        diagnosticador.correr()
    except KeyboardInterrupt:
        pass
    finally:
        diagnosticador.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
