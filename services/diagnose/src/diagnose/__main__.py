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

    sombra = _juez_en_sombra(settings, log)
    investigacion = _investigador_en_sombra(settings, log)
    diagnosticador = Diagnosticador(settings, almacen=almacen, juez=JuezDeReglas(), sombra=sombra,
                                    investigacion=investigacion)
    log.info(
        "diagnose.ready",
        extra={
            "alertbus": settings.alertbus_url,
            "clickhouse": settings.clickhouse_url,
            "juez": settings.juez,
            "sombra": sombra.nombre if sombra else "",
            "l4": bool(investigacion),
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


def _juez_en_sombra(settings: Settings, log: logging.Logger):
    """El juez con modelo, si se pidio y hay con que.

    Pedido sin credencial NO tumba el servicio: el diagnostico que llega a las
    personas es el de las reglas y no depende de esto. Se dice en el log y se
    sigue sin sombra.
    """
    # `modelo` compite con las reglas; `reglas+modelo` revisa su veredicto (B-42).
    if settings.juez_sombra not in ("modelo", "reglas+modelo"):
        return None
    import os

    if not os.environ.get("AXONIUM_CLIENT_ID"):
        log.warning("sombra.sin_credencial", extra={"falta": "AXONIUM_CLIENT_ID / AXONIUM_CLIENT_SECRET"})
        return None
    from .juez_modelo import JuezConModelo, gateway_axonium

    return JuezConModelo(
        gateway_axonium,
        modelo=settings.modelo,
        razonamiento=settings.razonamiento or None,
        completa_a=JuezDeReglas() if settings.juez_sombra == "reglas+modelo" else None,
    )


def _investigador_en_sombra(settings: Settings, log: logging.Logger):
    """L4 en sombra, si se pidio y hay credencial. Como el juez: sin ella, se dice y se sigue."""
    if settings.investigador != "modelo":
        return None
    import asyncio
    import os

    if not os.environ.get("AXONIUM_CLIENT_ID"):
        log.warning("l4.sin_credencial", extra={"falta": "AXONIUM_CLIENT_ID / AXONIUM_CLIENT_SECRET"})
        return None
    from .investigador import investigar_con_mcp
    from .sombra_l4 import SombraL4

    def investigar(huella: str):
        return asyncio.run(investigar_con_mcp(huella, modelo=settings.modelo,
                                              razonamiento=settings.razonamiento or None))

    return SombraL4(investigar, ruta=settings.sombra_l4_path)


if __name__ == "__main__":
    sys.exit(main())
