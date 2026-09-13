"""Configuracion del canario."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CANARY_", extra="ignore")

    registry_path: Path = Path("/etc/argus/apps.yaml")
    probes_path: Path = Path("/etc/argus/probes.yaml")

    alertbus_url: str = "http://alert-bus:8080"
    token: str = ""

    intervalo_s: int = 300

    # Dos fallos consecutivos antes de alertar. Cambia tiempo de deteccion por
    # precision, que en un canario es la moneda correcta: uno que grita por
    # cada microcorte de red es uno que acaba silenciado.
    fallos_para_alertar: int = 2

    # --- Sonda de silencio ---------------------------------------------------
    clickhouse_url: str = "http://clickhouse:8123/"
    clickhouse_user: str = "argus"
    clickhouse_password: str = ""
    # Ventana generosa: un worker con poco trabajo puede pasar minutos sin
    # emitir sin que pase nada. Quince minutos sin UN solo span sí es raro.
    silencio_ventana_s: int = 900
