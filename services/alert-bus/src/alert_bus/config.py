"""Configuracion, por entorno como todo lo demas."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERTBUS_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080

    # El registro de aplicaciones. Sin el, todo servicio entra como provisional
    # y la correlacion por topologia no tiene grafo sobre el que trabajar.
    registry_path: Path = Path("platform/registry/apps.yaml")

    # Ventana de agrupacion para severidades por debajo de `page`. Los `page`
    # no esperan: avisar tarde es no avisar.
    group_window_s: int = 60

    # Un incidente sin senales nuevas durante este tiempo se da por resuelto.
    resolve_after_s: int = 900

    # Cada cuanto se revisan ventanas y cierres.
    tick_s: int = 5

    # Token que exige a los Collector agente. El mismo del gateway.
    token: str = ""

    sinks: str = "console,json"

    def sink_names(self) -> list[str]:
        return [s.strip() for s in self.sinks.split(",") if s.strip()]
