from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from alert_bus.engine import Engine
from alert_bus.registry import Registry
from alert_bus.sinks import MemorySink

REGISTRO = [
    {
        "id": "intelligent-document-platform",
        "nombre_visible": "IDP",
        "estado": "activo",
        "criticidad": "alta",
        "dueño": "emeric",
        "canales": {"page": ["memory"], "ticket": ["memory"]},
        "componentes": [
            {"id": "idp-api", "rol": "api", "slo": {"p95_ms": 8000}},
            {"id": "idp-worker", "rol": "worker"},
            {"id": "idp-ocr", "rol": "model-server", "slo": {"p95_ms": 30000}},
        ],
        "depende_de": ["postgres-main", "minio-main"],
    },
    # Las dependencias tienen que estar DECLARADAS para que la correlacion
    # tenga grafo. Un servicio auto-descubierto entra con criticidad media, asi
    # que ni siquiera podria paginar, y los tests de correlacion medirian otra
    # cosa.
    {
        "id": "postgres-main",
        "estado": "activo",
        "criticidad": "alta",
        "canales": {"page": ["memory"], "ticket": ["memory"]},
        "componentes": [{"id": "postgres-main", "rol": "model-server"}],
    },
    {
        "id": "minio-main",
        "estado": "activo",
        "criticidad": "media",
        "canales": {"page": ["memory"], "ticket": ["memory"]},
        "componentes": [{"id": "minio-main", "rol": "model-server"}],
    },
    {
        "id": "aeon-ai",
        "estado": "activo",
        "criticidad": "alta",
        "canales": {"page": ["memory"], "ticket": ["memory"]},
        "componentes": [{"id": "control-plane", "rol": "api"}],
        "depende_de": ["postgres-main"],
    },
    {
        "id": "llm-benchmark",
        "estado": "activo",
        # Criticidad baja: un laboratorio no puede generar guardias por mucho
        # que falle. Es la valvula que evita que los experimentos despierten a
        # nadie.
        "criticidad": "baja",
        "canales": {"page": ["memory"], "ticket": ["memory"]},
        "componentes": [{"id": "bench-runner", "rol": "cli"}],
    },
]


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    ruta = tmp_path / "apps.yaml"
    ruta.write_text(yaml.safe_dump(REGISTRO, allow_unicode=True), encoding="utf-8")
    return Registry(ruta)


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def engine(registry: Registry, sink: MemorySink) -> Engine:
    return Engine(registry, [sink], group_window_s=60, resolve_after_s=900)
