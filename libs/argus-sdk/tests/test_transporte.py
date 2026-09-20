"""Eleccion de transporte y ruido de los avisos.

Las dos cosas que arreglaron el bloqueo de un equipo consumidor: que el nucleo
funcione sin protobuf, y que un `init()` repetido normal no avise.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from argus._config import Config, _protocolo_por_defecto


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    for v in ("ARGUS_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL", "ARGUS_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(v, raising=False)


def test_sin_exportadores_instalados_cae_a_json(monkeypatch):
    """El nucleo no declara ningun exportador oficial: todos piden protobuf>=5."""
    monkeypatch.setattr("argus._config._disponible", lambda _: False)
    assert _protocolo_por_defecto() == "http/json"


def test_con_grpc_instalado_se_prefiere_grpc(monkeypatch):
    """Quien ya tiene el extra no cambia de comportamiento al actualizar."""
    monkeypatch.setattr(
        "argus._config._disponible",
        lambda m: m == "opentelemetry.exporter.otlp.proto.grpc",
    )
    assert _protocolo_por_defecto() == "grpc"


def test_el_puerto_por_defecto_sigue_al_transporte(monkeypatch):
    """4317 es gRPC y 4318 es HTTP.

    Heredar el de gRPC al caer a JSON seria degradar a un exportador que
    funciona apuntando a un puerto que no contesta: no hay error, solo
    silencio.
    """
    monkeypatch.setattr("argus._config._disponible", lambda _: False)
    cfg = Config.from_env("s")
    assert cfg.protocol == "http/json"
    assert cfg.endpoint.endswith(":4318")

    monkeypatch.setattr(
        "argus._config._disponible",
        lambda m: m == "opentelemetry.exporter.otlp.proto.grpc",
    )
    assert Config.from_env("s").endpoint.endswith(":4317")


def test_el_endpoint_explicito_manda(monkeypatch):
    monkeypatch.setenv("ARGUS_ENDPOINT", "http://otro:9999")
    assert Config.from_env("s").endpoint == "http://otro:9999"


def test_protocolo_invalido_no_revienta(monkeypatch):
    monkeypatch.setenv("ARGUS_PROTOCOL", "carrier-pigeon")
    assert Config.from_env("s").protocol in ("grpc", "http/protobuf", "http/json")


# --- El aviso de init() repetida -------------------------------------------
# Van en un proceso aparte: `init()` fija los proveedores GLOBALES de
# OpenTelemetry, y hacerlo dentro del proceso de pytest contamina el resto de
# la suite. Es el mismo motivo por el que test_contract.py usa subprocesos.


def _en_proceso_limpio(cuerpo: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(cuerpo)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_repetir_init_identica_no_avisa():
    """Una API que importa el modulo de tareas llama a `init()` dos veces.

    Es NORMAL. Avisar en cada arranque y en cada test ensena a ignorar los
    avisos, que es lo contrario de lo que queremos el dia que uno importe.
    """
    r = _en_proceso_limpio(
        """
        import warnings
        import argus

        argus.init("s", endpoint="http://127.0.0.1:1")
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always")
            argus.init()
            argus.init("s", endpoint="http://127.0.0.1:1")
        print("REPETIDAS", len([a for a in avisos if "ya se habia llamado" in str(a.message)]))
        """
    )
    assert "REPETIDAS 0" in r.stdout, r.stdout + r.stderr


def test_repetir_init_con_otra_configuracion_si_avisa():
    """Estos argumentos se descartan en silencio: eso si merece un aviso."""
    r = _en_proceso_limpio(
        """
        import warnings
        import argus

        argus.init("s", endpoint="http://127.0.0.1:1")
        with warnings.catch_warnings(record=True) as avisos:
            warnings.simplefilter("always")
            argus.init(endpoint="http://otro:4318", namespace="otro-ns")
        m = [str(a.message) for a in avisos if "ya se habia llamado" in str(a.message)]
        print("AVISOS", len(m))
        print("MENSAJE", m[0] if m else "")
        """
    )
    assert "AVISOS 1" in r.stdout, r.stdout + r.stderr
    assert "endpoint" in r.stdout and "namespace" in r.stdout
