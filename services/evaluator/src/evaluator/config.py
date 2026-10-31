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

    # --- F5-02: el juez de L3 en sombra ----------------------------------------
    # El diario de la sombra de `diagnose`, montado en solo lectura. Vacio apaga
    # estas evaluaciones. Es el unico sitio con prompt y respuesta hasta que se
    # encienda Langfuse (B-45).
    diario_juez_path: Path | None = None
    diario_estado_path: Path = Path("/var/lib/argus/evaluator-diario.json")
    # El modelo de groundedness y consistencia, por Axonium. Vacio: solo formato.
    modelo: str = ""
    razonamiento: str = "off"
    # Inferencia de la plataforma compartida: como mucho tantos registros por hora.
    caras_por_hora: int = 6
