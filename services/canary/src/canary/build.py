"""Construye las sondas desde el registro de aplicaciones y un fichero propio.

La sonda de SILENCIO se deriva automaticamente del registro: toda aplicacion
activa deberia estar emitiendo algo. No hace falta declararla.

La sonda HTTP si se declara, porque solo tu sabes que endpoint de tu aplicacion
significa "estoy viva".
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .config import Settings
from .probes import Sonda, SondaHTTP, SondaSilencio

log = logging.getLogger("canary.build")

# Roles a los que NO se les pone sonda de silencio.
#
# Un CLI corre y termina; una libreria no corre por si sola; y la
# infraestructura (`infra`) no emite telemetria a nuestro Collector en
# absoluto. Exigirles un latido constante produciria falsos silencios todos los
# dias, y un canario que grita por cosas que sabes que no estan es un canario
# que acaba silenciado.
#
# La infraestructura se vigila por sonda HTTP, que es lo que corresponde a algo
# que no instrumentas.
ROLES_SIN_LATIDO = {"cli", "library", "frontend", "infra"}


def cargar(settings: Settings) -> list[Sonda]:
    sondas: list[Sonda] = []
    sondas.extend(_sondas_http(settings))
    sondas.extend(_sondas_silencio(settings))
    log.info("canary.probes_loaded", extra={"total": len(sondas)})
    return sondas


def _sondas_http(settings: Settings) -> list[SondaHTTP]:
    ruta: Path = settings.probes_path
    if not ruta.exists():
        log.info("canary.no_http_probes", extra={"path": str(ruta)})
        return []

    try:
        entradas = yaml.safe_load(ruta.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        log.error("canary.probes_invalid", extra={"error": str(exc)})
        return []

    return [
        SondaHTTP(
            app=e["app"],
            component=e.get("componente", e["app"]),
            url=e["url"],
            slo_ms=int(e.get("slo_ms", 0)),
            timeout_s=float(e.get("timeout_s", 10)),
            expected_status=e.get("estado_esperado"),
        )
        for e in entradas
    ]


def _sondas_silencio(settings: Settings) -> list[SondaSilencio]:
    ruta: Path = settings.registry_path
    if not ruta.exists():
        log.warning("canary.no_registry", extra={"path": str(ruta)})
        return []

    try:
        apps = yaml.safe_load(ruta.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        log.error("canary.registry_invalid", extra={"error": str(exc)})
        return []

    sondas: list[SondaSilencio] = []
    for app in apps:
        # Solo lo `activo`. Una aplicacion `planificado` esta en el roadmap
        # pero aun no emite: vigilar su silencio seria alertar de algo que ya
        # sabes.
        if app.get("estado") != "activo":
            continue
        for componente in app.get("componentes", []):
            # El registro admite `[{id: x, rol: y}]` y tambien `[{id: x}]`.
            if isinstance(componente, str):
                comp_id, rol = componente, "api"
            else:
                comp_id, rol = componente["id"], componente.get("rol", "api")

            if rol in ROLES_SIN_LATIDO:
                continue

            sondas.append(
                SondaSilencio(
                    app=app["id"],
                    component=comp_id,
                    clickhouse_url=settings.clickhouse_url,
                    usuario=settings.clickhouse_user,
                    password=settings.clickhouse_password,
                    ventana_s=settings.silencio_ventana_s,
                )
            )
    return sondas
