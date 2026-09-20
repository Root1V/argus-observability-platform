"""La entrega sobrevive a un fallo pasajero de red.

Nacen de un incidente real: entre el 17 y el 19 de septiembre de 2026 se
detectaron seis incidentes correctamente y **ninguno se entrego**. Telegram
fallaba con `CERTIFICATE_VERIFY_FAILED` —una red con inspeccion TLS por el
medio— y el despachador lo intentaba una sola vez (D-080).

La asimetria que corrigen: la telemetria tiene una cola en disco con WAL para
aguantar que el portatil cambie de red; la notificacion, que es la SALIDA del
sistema entero, tenia un unico intento sobre esa misma red.
"""

from __future__ import annotations

import pytest
from alert_bus.sinks import dispatch as D
from argus_schemas import Incident, Severity, Signal, SignalKind


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    """El retroceso es correcto; hacer que los tests lo aguanten no lo es."""
    monkeypatch.setattr(D.time, "sleep", lambda _s: None)


@pytest.fixture
def incidente() -> Incident:
    senal = Signal(
        kind=SignalKind.ERROR,
        app="prometheus-inference-platform",
        component="gateway",
        environment="mac-dev",
        signature="silencio",
        title="gateway lleva 15 minutos sin emitir",
    )
    return Incident.from_signal(senal, Severity.PAGE, "huella1")


class SinkInestable:
    """Falla las primeras `fallos` veces y luego funciona."""

    def __init__(self, fallos: int) -> None:
        self.name = "telegram"
        self._restantes = fallos
        self.intentos = 0

    def send(self, incident: Incident, *, update: bool = False) -> None:
        self.intentos += 1
        if self._restantes > 0:
            self._restantes -= 1
            raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")


def test_un_fallo_pasajero_acaba_entregando(incidente):
    """Es exactamente la forma del fallo real: falla una vez, luego va."""
    sink = SinkInestable(fallos=1)
    D._enviar_con_reintentos(sink, incidente, update=False)
    assert sink.intentos == 2


def test_aguanta_hasta_dos_reintentos(incidente):
    sink = SinkInestable(fallos=2)
    D._enviar_con_reintentos(sink, incidente, update=False)
    assert sink.intentos == 3


def test_un_fallo_permanente_se_propaga(incidente):
    """Reintentar no puede convertir un canal muerto en un exito silencioso.

    Si se agotan los intentos la excepcion sube, y quien llama la cuenta como
    fallo. Una plataforma que cree estar avisando y no lo esta es peor que una
    que dice que no pudo.
    """
    sink = SinkInestable(fallos=99)
    with pytest.raises(OSError):
        D._enviar_con_reintentos(sink, incidente, update=False)
    assert sink.intentos == D.REINTENTOS + 1


def test_el_despachador_en_linea_reintenta_y_cuenta(incidente):
    """El camino completo, no solo el helper."""
    sink = SinkInestable(fallos=1)
    d = D.InlineDispatcher()
    d.submit([sink], incidente, update=False)
    assert sink.intentos == 2
    assert d.stats["enviados"] == 1
    assert d.stats["fallidos"] == 0


def test_el_despachador_en_linea_cuenta_el_fallo_definitivo(incidente):
    sink = SinkInestable(fallos=99)
    d = D.InlineDispatcher()
    d.submit([sink], incidente, update=False)
    assert d.stats["fallidos"] == 1
    assert d.por_canal["telegram"]["fallidos"] == 1
