"""Configuracion del evaluador."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVALUATOR_", extra="ignore")

    clickhouse_url: str = "http://clickhouse:8123/"
    clickhouse_user: str = "argus"
    clickhouse_password: str = ""

    intervalo_s: int = 60
    # Detras del presente: `decision_wait` del gateway (30 s) mas lote e
    # insercion, con margen. Ver `bucle.py`.
    retraso_s: int = 300
    # Sin cursor (primer arranque), cuanto historico se rellena en la tabla.
    relleno_h: int = 24
    # Porcentaje al azar de la muestra para las evaluaciones caras (PLAN §7.4c).
    muestra_pct: float = 5.0

    cursor_path: Path = Path("/var/lib/argus/evaluator-cursor.json")
