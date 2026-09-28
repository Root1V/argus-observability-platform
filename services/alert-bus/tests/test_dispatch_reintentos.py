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

import threading

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


def test_drain_espera_al_envio_que_ya_esta_en_vuelo(incidente: Incident) -> None:
    """`drain` no puede decir "drenado" mientras un canal sigue entregando.

    `_cola.empty()` se vuelve cierto en el instante del `get()`, no cuando el
    envio termina. Un canal lento deja la cola vacia y el trabajo sin hacer, y
    `drain` devolvia ahi. En el apagado eso significa `stop()` matando una
    notificacion a medio entregar — el fallo que el reparto existe para impedir.

    Se veia como un test intermitente del despacho; era el codigo (D-092).

    Sin relojes: el canal se bloquea en un `Event` y lo suelta esta prueba, asi
    que el fallo no depende de que la maquina vaya cargada. Tampoco valdria un
    `sleep`, porque la fixture del fichero lo anula.
    """
    en_el_send = threading.Event()
    puede_acabar = threading.Event()

    class SinkBloqueado:
        name = "bloqueado"
        entregados = 0

        def send(self, incident, *, update: bool) -> None:
            en_el_send.set()
            assert puede_acabar.wait(timeout=10), "nadie solto el canal"
            SinkBloqueado.entregados += 1

    SinkBloqueado.entregados = 0
    despachador = D.Dispatcher(workers=1)
    despachador.start()
    try:
        despachador.submit([SinkBloqueado()], incidente, update=False)

        # Dentro del `send`: la cola YA esta vacia y el trabajo NO esta hecho.
        # Es exactamente la ventana que se escapaba.
        assert en_el_send.wait(timeout=5), "el trabajador no llego a arrancar"
        assert despachador._cola.empty(), "la premisa de la prueba no se cumple"

        devuelto = threading.Event()

        def drenar() -> None:
            despachador.drain(timeout_s=10)
            devuelto.set()

        hilo = threading.Thread(target=drenar, daemon=True)
        hilo.start()

        # Con el canal aun dentro del `send`, `drain` tiene que seguir esperando.
        assert not devuelto.wait(timeout=0.5), "drain devolvio con un envio en vuelo"

        puede_acabar.set()
        assert devuelto.wait(timeout=5), "drain no desperto al acabar el envio"
        assert SinkBloqueado.entregados == 1
        assert despachador.stats["enviados"] == 1
    finally:
        puede_acabar.set()
        despachador.stop(timeout_s=2)
