"""Los dos fallos de configuracion que no dan ningun error.

Aeon perdio una tarde con estos dos, y lo que los hace caros no es que sean
dificiles sino que **fallan en silencio**: la aplicacion arranca, funciona y
sirve trafico; el exportador reintenta de fondo; y no llega un solo span. No
hay excepcion, no hay log de error, solo ausencia de datos — que es justo lo
que nadie esta mirando en los minutos siguientes a montar el trazado (D-096).

Avisan y no levantan. El contrato del SDK es no tumbar nunca la aplicacion: lo
que se corrige es que el fallo sea invisible, no que sea fatal.
"""

from __future__ import annotations

import warnings

import pytest
from argus import _config
from argus._config import Config


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    for v in ("ARGUS_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT", "ARGUS_PROTOCOL",
              "OTEL_EXPORTER_OTLP_PROTOCOL"):
        monkeypatch.delenv(v, raising=False)


def _avisos(monkeypatch, *, endpoint: str, protocolo: str, en_contenedor: bool = False) -> list[str]:
    monkeypatch.setenv("ARGUS_ENDPOINT", endpoint)
    monkeypatch.setenv("ARGUS_PROTOCOL", protocolo)
    monkeypatch.setattr(_config, "_en_contenedor", lambda: en_contenedor)
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        Config.from_env("prueba")
    return [str(a.message) for a in capturados]


# --- Puerto contra protocolo ------------------------------------------------


def test_grpc_contra_el_puerto_de_http_avisa(monkeypatch) -> None:
    """El caso exacto de Aeon: una imagen con los dos exportadores instalados
    autodetecta gRPC y lo habla contra el 4318, que es HTTP."""
    avisos = _avisos(monkeypatch, endpoint="http://otel-collector:4318", protocolo="grpc")
    assert any("4318" in a and "grpc" in a and "4317" in a for a in avisos), avisos


def test_http_contra_el_puerto_de_grpc_avisa(monkeypatch) -> None:
    """La direccion contraria es igual de silenciosa y no la reporto nadie."""
    avisos = _avisos(monkeypatch, endpoint="http://collector:4317", protocolo="http/protobuf")
    assert any("4317" in a and "4318" in a for a in avisos), avisos


@pytest.mark.parametrize(
    "endpoint,protocolo",
    [
        ("http://collector:4318", "http/protobuf"),
        ("http://collector:4318", "http/json"),
        ("http://collector:4317", "grpc"),
        # Sin puerto explicito no hay nada que contrastar.
        ("http://collector", "grpc"),
        # Un puerto que no es ni 4317 ni 4318 es una eleccion deliberada
        # —un proxy, un sidecar—, no un despiste.
        ("http://collector:8080", "grpc"),
    ],
)
def test_las_combinaciones_coherentes_no_avisan(monkeypatch, endpoint, protocolo) -> None:
    """Un aviso que salta cuando no debe se aprende a ignorar, y entonces no
    protege del caso en que si debe."""
    assert not [a for a in _avisos(monkeypatch, endpoint=endpoint, protocolo=protocolo) if "puerto" in a]


# --- `localhost` desde dentro de un contenedor ------------------------------


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_localhost_desde_un_contenedor_avisa(monkeypatch, host) -> None:
    """La convencion «las aplicaciones exportan a localhost» es correcta y se
    lee en el sitio equivocado: el agente publica sus puertos en el 127.0.0.1
    del HOST, asi que desde una red de Docker es inalcanzable."""
    avisos = _avisos(
        monkeypatch, endpoint=f"http://{host}:4318", protocolo="http/protobuf", en_contenedor=True
    )
    assert any("contenedor" in a for a in avisos), avisos


def test_localhost_fuera_de_un_contenedor_no_avisa(monkeypatch) -> None:
    """Es el caso NORMAL y recomendado. Avisar aqui seria ruido en todos."""
    avisos = _avisos(
        monkeypatch, endpoint="http://localhost:4318", protocolo="http/protobuf", en_contenedor=False
    )
    assert not [a for a in avisos if "contenedor" in a]


def test_un_host_de_verdad_desde_un_contenedor_no_avisa(monkeypatch) -> None:
    avisos = _avisos(
        monkeypatch, endpoint="http://host.docker.internal:4318",
        protocolo="http/protobuf", en_contenedor=True,
    )
    assert not [a for a in avisos if "contenedor" in a]


def test_un_aviso_no_impide_arrancar(monkeypatch) -> None:
    """Lo que se corrige es que el fallo sea invisible, no que sea fatal."""
    monkeypatch.setenv("ARGUS_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("ARGUS_PROTOCOL", "grpc")
    monkeypatch.setattr(_config, "_en_contenedor", lambda: True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = Config.from_env("prueba")
    assert cfg.endpoint == "http://collector:4318"
    assert cfg.protocol == "grpc"
